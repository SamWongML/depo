#!/usr/bin/env python3
"""Self-test for figma_probe.py. Run: python3 test_probe.py"""
import io
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
import zlib
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import figma_probe as fp

FAILED = []
PASSED = 0


def check(name, cond, detail=""):
    global PASSED
    if cond:
        PASSED += 1
        print("  ok   %s" % name)
    else:
        FAILED.append(name)
        print("  FAIL %s  %s" % (name, detail))


# ---------------------------------------------------------------- url parsing
print("\n== URL parsing ==")
cases = [
    ("https://www.figma.com/design/AbCdEf123/My-App?node-id=24626-100",
     "AbCdEf123", "24626:100"),
    ("https://www.figma.com/file/XyZ987/Old-Style?node-id=1-2&t=abc",
     "XyZ987", "1:2"),
    ("https://www.figma.com/proto/PrOtO55/Flow?node-id=9-9&scaling=min-zoom",
     "PrOtO55", "9:9"),
    ("https://www.figma.com/design/NoNode123/Title", "NoNode123", None),
    ("https://www.figma.com/board/Jam42/Board?node-id=0-1", "Jam42", "0:1"),
    ("BareKey1234567890", "BareKey1234567890", None),
]
for url, want_key, want_node in cases:
    got = fp.parse_figma_url(url)
    check("key   %-58s" % url[:58], got["file_key"] == want_key,
          "got %r want %r" % (got["file_key"], want_key))
    check("node  %-58s" % url[:58], got["node_id_api"] == want_node,
          "got %r want %r" % (got["node_id_api"], want_node))

check("empty url is safe", fp.parse_figma_url("")["parse_ok"] is False)
check("garbage url is safe", fp.parse_figma_url("::::")["file_key"] is None)

# ------------------------------------------------------------- redaction
print("\n== Token handling ==")
fpx = fp.token_fingerprint("figd_SUPERSECRETVALUE123")
check("fingerprint has no secret", "SUPERSECRET" not in json.dumps(fpx))
check("fingerprint prefix", fpx["prefix"] == "figd_", str(fpx))
check("fingerprint length", fpx["length"] == len("figd_SUPERSECRETVALUE123"), str(fpx))
check("absent token", fp.token_fingerprint("")["present"] is False)
check("redact works",
      fp.redact("token=figd_ABC here", "figd_ABC") == "token=«REDACTED-TOKEN» here")

# ------------------------------------------------------------- .fig inspector
print("\n== .fig structural inspector ==")
tmp = Path("/tmp/figtest")
if tmp.exists():
    shutil.rmtree(tmp)
tmp.mkdir(parents=True)


def make_canvas(msg_zstd=False):
    schema_raw = b"SCHEMA-DEFINITIONS-" * 40
    schema_c = zlib.compressobj(9, zlib.DEFLATED, -15)
    schema_z = schema_c.compress(schema_raw) + schema_c.flush()
    if msg_zstd:
        msg_z = b"\x28\xb5\x2f\xfd" + b"\x00" * 60
    else:
        mc = zlib.compressobj(9, zlib.DEFLATED, -15)
        msg_z = mc.compress(b"NODE_CHANGES-" * 80) + mc.flush()
    out = b"fig-kiwi" + (109).to_bytes(4, "little")
    out += len(schema_z).to_bytes(4, "little") + schema_z
    out += len(msg_z).to_bytes(4, "little") + msg_z
    return out


# modern: zip container, zstd message
zp = tmp / "modern.fig"
with zipfile.ZipFile(zp, "w") as z:
    z.writestr("canvas.fig", make_canvas(msg_zstd=True))
    z.writestr("meta.json", json.dumps({"file_name": "Checkout Flows"}))
    z.writestr("thumbnail.png", b"\x89PNG")
ev = fp.inspect_fig(str(zp))
check("zip container detected", ev["container"] == "zip", str(ev.get("container")))
check("canvas entry found", ev["canvas_entry"] == "canvas.fig")
check("meta.json parsed", ev["meta_json"]["file_name"] == "Checkout Flows")
check("prelude ok", ev["prelude_ok"] is True, str(ev.get("prelude")))
check("format version", ev["format_version"] == 109)
check("two chunks", len(ev["chunks"]) == 2, str(len(ev.get("chunks", []))))
check("schema chunk decompressed", ev["schema_chunk_readable"] is True)
check("zstd detected on message", ev["message_needs_zstd"] is True,
      str(ev["chunks"][1]))

# legacy: raw container, deflate message
rp = tmp / "legacy.fig"
rp.write_bytes(make_canvas(msg_zstd=False))
ev2 = fp.inspect_fig(str(rp))
check("raw container detected", ev2["container"] == "raw")
check("deflate message decompressed",
      ev2["chunks"][1].get("decompress_ok") is True, str(ev2["chunks"][1]))
check("zstd not flagged", ev2["message_needs_zstd"] is False)

# corrupt file must not raise
bad = tmp / "bad.fig"
bad.write_bytes(b"not a fig file at all")
ev3 = fp.inspect_fig(str(bad))
check("corrupt file handled", ev3["prelude_ok"] is False)

# ------------------------------------------------------- interactions counter
print("\n== Interaction extraction ==")
payload = {"nodes": {"1:1": {"document": {
    "id": "1:1", "transitionNodeID": "1:2",
    "interactions": [{"trigger": {"type": "ON_CLICK"},
                      "actions": [{"type": "NODE", "navigation": "NAVIGATE"}]}],
    "children": [{"id": "1:3",
                  "interactions": [{"trigger": {"type": "ON_DRAG"},
                                    "actions": [{"type": "NODE",
                                                 "navigation": "OVERLAY"},
                                                {"type": "SET_VARIABLE"}]}],
                  "children": []}]}}}}
s = fp._interactions_inference(payload)
check("counts interactions", "interactions=2" in s, s)
check("counts transitionNodeID", "transitionNodeID=1" in s, s)
check("counts triggers", "ON_DRAG" in s and "ON_CLICK" in s, s)
check("counts actions", "SET_VARIABLE" in s, s)
check("handles junk", fp._interactions_inference(None) == "")
check("handles empty", fp._interactions_inference({}) is not None)

# ------------------------------------------------------------- full runs
print("\n== End-to-end runs against mock ==")


def run_mock(mode, port, extra_args, tag):
    srv = subprocess.Popen([sys.executable, "mock_figma.py", mode, str(port)],
                           stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(1.2)
    env = dict(os.environ)
    env["FIGMA_API_ROOT"] = "http://127.0.0.1:%d" % port
    env["FIGMA_TOKEN"] = "figd_TESTTOKEN_DO_NOT_LOG"
    out = Path("/tmp/figtest/out-%s" % tag)
    if out.exists():
        shutil.rmtree(out)
    cmd = [sys.executable, "figma_probe.py",
           "--url", "https://www.figma.com/design/MOCKKEY123/App?node-id=24626-100",
           "--outdir", str(out), "--timeout", "8",
           "--fig-dir", "/tmp/figtest", "--cdp-ports", "59999"] + extra_args
    r = subprocess.run(cmd, capture_output=True, env=env, timeout=180)
    srv.terminate()
    srv.wait(timeout=10)
    runs = sorted(out.glob("*"))
    return r, (runs[0] if runs else None)


# --- mode: high seat, tier1 allowed
r, d = run_mock("high", 8741, ["--allow-tier1"], "high")
check("high: exit 0", r.returncode == 0, r.stderr.decode()[-900:])
check("high: outdir created", d is not None)
if d:
    lines = [json.loads(x) for x in
             (d / "audit.jsonl").read_text().splitlines() if x.strip()]
    v = json.loads((d / "verdict.json").read_text())
    ids = [l.get("probe_id") for l in lines if l.get("type") == "probe"]
    check("high: run_header first", lines[0]["type"] == "run_header")
    check("high: seq monotonic",
          [l["seq"] for l in lines] == list(range(len(lines))))
    check("high: all probes present",
          {"E01", "E02", "E03", "E04", "A01", "A03", "B01", "B02", "B03",
           "B04", "C01", "C02", "D01", "D02", "D03", "D04", "D05",
           "D06"} <= set(ids), str(sorted(set(ids))))
    check("high: A01 passed",
          next(l for l in lines if l.get("probe_id") == "A01")["outcome"] == "PASS")
    check("high: C01 passed",
          next(l for l in lines if l.get("probe_id") == "C01")["outcome"] == "PASS")
    c02 = next(l for l in lines if l.get("probe_id") == "C02")
    check("high: C02 found interactions", "interactions=2" in (c02["inference"] or ""),
          c02.get("inference"))
    check("high: C02 flags lossy shape", "transitionNodeID=1" in (c02["inference"] or ""))
    b03 = next(l for l in lines if l.get("probe_id") == "B03")
    check("high: B03 403 -> BLOCKED", b03["outcome"] == "BLOCKED", b03["outcome"])
    check("high: scope discovery worked",
          "file_dev_resources:read" in v["scopes"]["reported_missing"],
          json.dumps(v["scopes"]))
    check("high: budget accounted",
          v["budget_spent_this_run"]["tier1_calls"] == 2, json.dumps(v["budget_spent_this_run"]))
    check("high: .fig route OPEN",
          any(x["status"] == "OPEN" for x in v["routes"] if ".fig" in x["route"]),
          json.dumps([x for x in v["routes"] if ".fig" in x["route"]]))
    check("high: MCP marked unavailable",
          any("UNAVAILABLE" in x["status"] for x in v["routes"] if "MCP" in x["route"]))
    check("high: summary.md written", (d / "summary.md").exists())
    check("high: raw bodies stored", len(list((d / "raw").glob("*.bin"))) > 0)
    blob = (d / "audit.jsonl").read_text() + (d / "verdict.json").read_text() \
        + (d / "summary.md").read_text() \
        + "".join(p.read_text(errors="replace") for p in (d / "raw").glob("*.bin"))
    check("high: TOKEN NEVER ON DISK", "figd_TESTTOKEN_DO_NOT_LOG" not in blob)

# --- mode: low seat, 429 with 9.5h retry-after
r, d = run_mock("low", 8742, ["--allow-tier1", "--force"], "low")
check("low: exit 0", r.returncode == 0, r.stderr.decode()[-900:])
if d:
    lines = [json.loads(x) for x in
             (d / "audit.jsonl").read_text().splitlines() if x.strip()]
    v = json.loads((d / "verdict.json").read_text())
    c01 = next(l for l in lines if l.get("probe_id") == "C01")
    check("low: C01 BLOCKED by 429", c01["outcome"] == "BLOCKED", c01["outcome"])
    check("low: retry-after captured",
          c01["http"]["figma_diag"].get("retry-after") == "34200",
          json.dumps(c01["http"]["figma_diag"]))
    check("low: 9.5h computed", "9.5h" in (c01["inference"] or ""), c01["inference"])
    check("low: rate-limit-type surfaced",
          v["identity"]["rate_limit_type"] == "low", json.dumps(v["identity"]))
    check("low: plan tier is starter (not enterprise!)",
          v["identity"]["plan_tier_of_file"] == "starter")
    check("low: breaker tripped", v["circuit_breaker_tripped"] is True)
    c02 = next(l for l in lines if l.get("probe_id") == "C02")
    check("low: C02 skipped after breaker", c02["outcome"] == "SKIPPED", c02["outcome"])
    check("low: seat class named",
          "View/Collab" in v["identity"]["seat_class"], v["identity"]["seat_class"])

# --- mode: tier1 gated off by default
r, d = run_mock("high", 8743, [], "gated")
check("gated: exit 0", r.returncode == 0, r.stderr.decode()[-600:])
if d:
    v = json.loads((d / "verdict.json").read_text())
    check("gated: zero tier1 spent",
          v["budget_spent_this_run"]["tier1_calls"] == 0,
          json.dumps(v["budget_spent_this_run"]))

# --- mode: low seat auto-refuses tier1 without --force
r, d = run_mock("low", 8744, ["--allow-tier1"], "guard")
check("guard: exit 0", r.returncode == 0, r.stderr.decode()[-600:])

# --- mode: 401 -> bearer fallback
r, d = run_mock("bearer_only", 8745, [], "bearer")
check("bearer: exit 0", r.returncode == 0, r.stderr.decode()[-600:])
if d:
    lines = [json.loads(x) for x in
             (d / "audit.jsonl").read_text().splitlines() if x.strip()]
    ids = [l.get("probe_id") for l in lines if l.get("type") == "probe"]
    check("bearer: A02 fallback fired", "A02" in ids, str(ids))
    a02 = next(l for l in lines if l.get("probe_id") == "A02")
    check("bearer: A02 succeeded", a02["outcome"] == "PASS", a02["outcome"])

# --- offline: no network at all
r, d = run_mock("high", 8746, ["--skip-api"], "offline")
check("offline: exit 0", r.returncode == 0, r.stderr.decode()[-600:])
if d:
    v = json.loads((d / "verdict.json").read_text())
    check("offline: no api spend", sum(v["budget_spent_this_run"].values()) == 0)
    check("offline: still emits verdict", len(v["routes"]) == 6)

# --- dry run spends nothing
r = subprocess.run([sys.executable, "figma_probe.py", "--url",
                    "https://www.figma.com/design/K/T?node-id=1-2",
                    "--dry-run"], capture_output=True,
                   env={**os.environ, "FIGMA_TOKEN": "figd_x"})
check("dryrun: exit 0", r.returncode == 0, r.stderr.decode()[-400:])
check("dryrun: shows plan", b"DRY RUN" in r.stdout)

print("\n" + "=" * 60)
print("PASSED %d   FAILED %d" % (PASSED, len(FAILED)))
if FAILED:
    for f in FAILED:
        print("  - %s" % f)
    sys.exit(1)
print("ALL GREEN")
