#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
figma_probe.py - Figma access-route feasibility auditor.

Determines WHICH routes into a Figma file are actually open to you, and at what
cost, WITHOUT burning the scarce Tier-1 REST budget to find out.

Design principles
-----------------
1. ASCENDING COST ORDER. Free local probes first, then Tier 3 (10/min even on a
   View/Collab seat), then Tier 2 (5/min), then Tier 1 (possibly 6/MONTH).
2. HARD BUDGET GATE. Tier 1 probes never run unless explicitly enabled.
3. CIRCUIT BREAKER. First 429 with rate-limit-type=low aborts all remaining
   API probes. We refuse to spend a monthly quota discovering we have none.
4. NO RETRIES. A retry on 429 is a second charge against the same bucket.
5. EVERY response header is recorded. The Figma diagnostic headers are the
   entire point of this exercise.
6. The token is NEVER written to disk. Only a salted fingerprint.

Requires: Python 3.9+ (stdlib only). No pip install.
"""

from __future__ import annotations

import argparse
import base64
import glob
import hashlib
import json
import os
import platform
import plistlib
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import zipfile
import zlib
from datetime import datetime, timezone
from pathlib import Path

SCHEMA_VERSION = "1.0.0"
TOOL_VERSION = "1.0.0"
USER_AGENT = "figma-probe/%s (+local audit tool)" % TOOL_VERSION
# Overridable purely so the shipped test-suite can exercise this file against a
# local mock. Leave unset in normal use.
API_ROOT = os.environ.get("FIGMA_API_ROOT", "https://api.figma.com")

# --------------------------------------------------------------------------
# Rate-limit tier map. Source: developers.figma.com/docs/rest-api/rate-limits
# Budget on a View/Collab seat ("low"):  T1 = 6/month, T2 = 5/min, T3 = 10/min
# Budget on a Dev/Full seat  ("high"):   T1 = 10-20/min, T2 = 25-100/min,
#                                        T3 = 50-150/min  (varies by plan)
# --------------------------------------------------------------------------
TIER_BUDGET_LOW = {1: "6 per MONTH", 2: "5 per minute", 3: "10 per minute"}
TIER_BUDGET_HIGH = {1: "10-20 per minute", 2: "25-100 per minute", 3: "50-150 per minute"}

# Figma-specific response headers we care about most.
FIGMA_DIAG_HEADERS = (
    "retry-after",
    "x-figma-plan-tier",
    "x-figma-rate-limit-type",
    "x-figma-upgrade-link",
)

ALL_SCOPES = [
    "current_user:read", "file_comments:read", "file_comments:write",
    "file_content:read", "file_dev_resources:read", "file_dev_resources:write",
    "file_metadata:read", "file_variables:read", "file_variables:write",
    "file_versions:read", "files:read", "library_analytics:read",
    "library_assets:read", "library_content:read", "org:activity_log_read",
    "projects:read", "team_library_content:read", "webhooks:read",
    "webhooks:write",
]


# ==========================================================================
# Audit log
# ==========================================================================
class AuditLog:
    """Append-only JSONL audit log. One record per probe. Never buffers."""

    def __init__(self, outdir: Path, run_id: str, context: dict):
        self.outdir = outdir
        self.run_id = run_id
        self.path = outdir / "audit.jsonl"
        self.records = []
        self._seq = 0
        self._t0 = time.monotonic()
        self.outdir.mkdir(parents=True, exist_ok=True)
        (self.outdir / "raw").mkdir(exist_ok=True)
        # Record 0 is always the run header, so a log is self-describing.
        self._write({
            "seq": 0,
            "type": "run_header",
            "schema_version": SCHEMA_VERSION,
            "tool_version": TOOL_VERSION,
            "run_id": run_id,
            "ts_utc": _now(),
            "context": context,
        })

    def _write(self, rec: dict) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, sort_keys=False) + "\n")
            fh.flush()
            os.fsync(fh.fileno())   # survive a crash mid-probe

    def emit(self, **kw) -> dict:
        self._seq += 1
        rec = {
            "seq": self._seq,
            "type": "probe",
            "run_id": self.run_id,
            "ts_utc": _now(),
            "t_offset_ms": int((time.monotonic() - self._t0) * 1000),
        }
        rec.update(kw)
        self.records.append(rec)
        self._write(rec)
        return rec

    def save_raw(self, probe_id: str, body: bytes) -> dict:
        """Persist a response body for forensics. Returns a descriptor."""
        if body is None:
            return {"stored": False}
        digest = hashlib.sha256(body).hexdigest()
        name = "%s.%s.bin" % (probe_id, digest[:12])
        (self.outdir / "raw" / name).write_bytes(body)
        return {
            "stored": True,
            "file": "raw/" + name,
            "sha256": digest,
            "bytes": len(body),
        }


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# ==========================================================================
# Helpers
# ==========================================================================
def token_fingerprint(token: str) -> dict:
    """Identify a token in the log without ever disclosing it."""
    if not token:
        return {"present": False}
    prefix = token.split("_", 1)[0] + "_" if "_" in token else "(no-prefix)"
    return {
        "present": True,
        "prefix": prefix,
        "length": len(token),
        "sha256_12": hashlib.sha256(token.encode()).hexdigest()[:12],
    }


def redact(text, token):
    if not text or not token:
        return text
    return text.replace(token, "«REDACTED-TOKEN»")


def parse_figma_url(url: str) -> dict:
    """Extract file key + node id from any Figma URL form.

    Handles /file/, /design/, /proto/, /board/, /slides/, /deck/.
    node-id arrives as '1-23' in URLs but the API wants '1:23'.
    """
    out = {"input": url, "file_key": None, "node_id_url": None,
           "node_id_api": None, "surface": None, "parse_ok": False}
    if not url:
        return out
    # Bare file key (22-ish alphanumerics, no slashes)
    if re.fullmatch(r"[A-Za-z0-9]{10,64}", url.strip()):
        out.update(file_key=url.strip(), surface="bare-key", parse_ok=True)
        return out
    try:
        p = urllib.parse.urlparse(url)
    except Exception:
        return out
    m = re.search(r"/(file|design|proto|board|slides|deck|buzz)/([A-Za-z0-9]+)", p.path)
    if m:
        out["surface"] = m.group(1)
        out["file_key"] = m.group(2)
        out["parse_ok"] = True
    q = urllib.parse.parse_qs(p.query)
    node = (q.get("node-id") or q.get("node_id") or [None])[0]
    if node:
        out["node_id_url"] = node
        out["node_id_api"] = node.replace("-", ":", 1) if "-" in node else node
    return out


def which(name: str):
    return shutil.which(name)


def run_cmd(args, timeout=15):
    """Run a subprocess, never raise. Returns (rc, stdout, stderr)."""
    try:
        p = subprocess.run(args, capture_output=True, timeout=timeout)
        return (p.returncode,
                p.stdout.decode("utf-8", "replace").strip(),
                p.stderr.decode("utf-8", "replace").strip())
    except FileNotFoundError:
        return (-1, "", "not-found")
    except subprocess.TimeoutExpired:
        return (-2, "", "timeout")
    except Exception as e:                                  # noqa: BLE001
        return (-3, "", "%s: %s" % (type(e).__name__, e))


# ==========================================================================
# HTTP
# ==========================================================================
class Http:
    def __init__(self, token: str, timeout: int = 40):
        self.token = token
        self.timeout = timeout
        self.auth_header = "X-Figma-Token"   # flipped to Bearer if that's what works

    def request(self, url: str, method: str = "GET", authed: bool = True,
                extra_headers=None, max_body=2_000_000):
        """Perform one HTTP request. Never raises. Returns a result dict
        containing status, ALL response headers, timing, and the body."""
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        if authed and self.token:
            if self.auth_header == "X-Figma-Token":
                headers["X-Figma-Token"] = self.token
            else:
                headers["Authorization"] = "Bearer " + self.token
        if extra_headers:
            headers.update(extra_headers)

        req = urllib.request.Request(url, method=method, headers=headers)
        t0 = time.monotonic()
        status = None
        resp_headers = {}
        body = b""
        err = None
        final_url = url
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                status = r.status
                resp_headers = {k.lower(): v for k, v in r.headers.items()}
                body = r.read(max_body)
                final_url = r.geturl()
        except urllib.error.HTTPError as e:
            status = e.code
            resp_headers = {k.lower(): v for k, v in (e.headers or {}).items()}
            try:
                body = e.read(max_body)
            except Exception:                               # noqa: BLE001
                body = b""
            final_url = getattr(e, "url", url)
        except urllib.error.URLError as e:
            err = "URLError: %s" % (e.reason,)
        except socket.timeout:
            err = "socket.timeout after %ss" % self.timeout
        except Exception as e:                              # noqa: BLE001
            err = "%s: %s" % (type(e).__name__, e)
        dt = int((time.monotonic() - t0) * 1000)

        parsed = None
        if body:
            try:
                parsed = json.loads(body.decode("utf-8", "replace"))
            except Exception:                               # noqa: BLE001
                parsed = None

        return {
            "url": url,
            "final_url": final_url,
            "method": method,
            "auth_header_used": self.auth_header if (authed and self.token) else None,
            "status": status,
            "elapsed_ms": dt,
            "transport_error": err,
            "response_headers": resp_headers,
            "figma_diag": {h: resp_headers.get(h) for h in FIGMA_DIAG_HEADERS
                           if resp_headers.get(h) is not None},
            "body_bytes": len(body),
            "_body": body,
            "_json": parsed,
        }


# ==========================================================================
# Probe engine
# ==========================================================================
class Prober:
    def __init__(self, args, log: AuditLog):
        self.args = args
        self.log = log
        self.http = Http(args.token, timeout=args.timeout)
        self.spent = {1: 0, 2: 0, 3: 0, 0: 0}
        self.rate_limit_type = None     # 'low' | 'high'
        self.plan_tier = None           # enterprise|org|pro|starter|student
        self.tripped = False            # circuit breaker
        self.scopes_seen = set()
        self.scopes_missing = set()
        self.facts = {}

    # ---- core recorder -------------------------------------------------
    def record(self, probe_id, phase, name, question, tier, outcome,
               evidence=None, inference=None, next_action=None,
               http=None, error=None, cost_units=0):
        rec = {
            "probe_id": probe_id,
            "phase": phase,
            "name": name,
            "question": question,
            "cost": {
                "rate_limit_tier": tier,
                "units_charged": cost_units,
                "budget_if_low_seat": TIER_BUDGET_LOW.get(tier, "n/a (local)"),
                "budget_if_high_seat": TIER_BUDGET_HIGH.get(tier, "n/a (local)"),
            },
            "outcome": outcome,
            "evidence": evidence or {},
            "inference": inference,
            "next_action": next_action,
            "error": error,
        }
        if http is not None:
            body = http.pop("_body", b"")
            js = http.pop("_json", None)
            rec["http"] = http
            rec["http"]["body_ref"] = self.log.save_raw(probe_id, body)
            excerpt = body[:600].decode("utf-8", "replace") if body else ""
            rec["http"]["body_excerpt"] = redact(excerpt, self.args.token)
            rec["_json"] = js
        self.log.emit(**rec)
        self._console(rec)
        return rec

    def _console(self, rec):
        mark = {"PASS": "PASS ", "FAIL": "FAIL ", "BLOCKED": "BLOCK",
                "SKIPPED": "SKIP ", "ERROR": "ERR  ", "INFO": "INFO "}
        sym = mark.get(rec["outcome"], "?????")
        line = "  [%s] %-6s %s" % (sym, rec["probe_id"], rec["name"])
        if rec.get("http", {}).get("status"):
            line += "  -> HTTP %s" % rec["http"]["status"]
        print(line)
        if rec.get("inference") and self.args.verbose:
            print("           %s" % rec["inference"])

    # ---- shared API caller ---------------------------------------------
    def api(self, probe_id, name, question, path, tier,
            inference_fn=None, next_action=None, allow=True):
        if self.tripped:
            return self.record(probe_id, "api", name, question, tier, "SKIPPED",
                               inference="Circuit breaker open: a prior 429 on a "
                                         "'low' bucket means further calls would "
                                         "burn a monthly quota for no new information.",
                               next_action="Re-run after the Retry-After window, "
                                           "or fix the seat/plan first.")
        if not allow:
            return self.record(probe_id, "api", name, question, tier, "SKIPPED",
                               inference="Gated off by CLI flag.",
                               next_action="Re-run with the enabling flag.")

        url = API_ROOT + path
        r = self.http.request(url)
        self.spent[tier] += 1

        status = r["status"]
        diag = r["figma_diag"]
        if diag.get("x-figma-plan-tier"):
            self.plan_tier = diag["x-figma-plan-tier"]
        if diag.get("x-figma-rate-limit-type"):
            self.rate_limit_type = diag["x-figma-rate-limit-type"]

        outcome, inference = self._classify(status, r, diag)

        # scope discovery from 403 bodies
        js = r.get("_json")
        if status == 403 and isinstance(js, dict):
            msg = str(js.get("message", ""))
            for s in re.findall(r"[a-z_]+:[a-z_]+", msg):
                if "Invalid scope" in msg and msg.index(s) < msg.find("This endpoint") % (len(msg) + 1):
                    self.scopes_seen.add(s)
            req = re.search(r"requires the ([a-z_:]+(?: or [a-z_:]+)*) scope", msg)
            if req:
                for s in req.group(1).split(" or "):
                    self.scopes_missing.add(s.strip())

        if inference_fn and status == 200:
            try:
                extra = inference_fn(js, r)
                if extra:
                    inference = (inference or "") + " " + extra
            except Exception as e:                          # noqa: BLE001
                inference = (inference or "") + " [inference_fn error: %s]" % e

        # trip the breaker
        if status == 429 and self.rate_limit_type == "low":
            self.tripped = True

        return self.record(probe_id, "api", name, question, tier, outcome,
                           evidence={"path": path,
                                     "json_keys": sorted(js.keys())[:25]
                                     if isinstance(js, dict) else None},
                           inference=inference, next_action=next_action,
                           http=r, cost_units=1)

    def _classify(self, status, r, diag):
        if r["transport_error"]:
            return "ERROR", ("Network/TLS failure: %s. On a corporate Mac this is "
                             "usually the MITM proxy: set HTTPS_PROXY and point "
                             "SSL_CERT_FILE / NODE_EXTRA_CA_CERTS at the corporate "
                             "root CA." % r["transport_error"])
        if status == 200:
            return "PASS", "Route open."
        if status == 401:
            return "FAIL", ("401: token rejected. Either expired (PATs max out at "
                            "90 days) or the wrong auth header.")
        if status == 403:
            return "BLOCKED", ("403: authenticated but not permitted. Either a "
                               "missing scope on the token, or a plan/seat "
                               "entitlement you do not hold.")
        if status == 404:
            return "FAIL", ("404: file key wrong, OR the token's account cannot "
                            "see this file at all.")
        if status == 429:
            ra = diag.get("retry-after")
            rt = diag.get("x-figma-rate-limit-type")
            pt = diag.get("x-figma-plan-tier")
            hrs = None
            try:
                hrs = round(int(ra) / 3600.0, 2)
            except Exception:                               # noqa: BLE001
                pass
            return "BLOCKED", (
                "429 RATE LIMITED. retry-after=%s%s, rate-limit-type=%s, "
                "plan-tier=%s. rate-limit-type 'low' means a View/Collab seat; "
                "'high' means Dev/Full. plan-tier reflects where the FILE lives, "
                "not your best seat elsewhere."
                % (ra, (" (%sh)" % hrs) if hrs else "", rt, pt))
        return "FAIL", "Unexpected HTTP %s." % status


# ==========================================================================
# PHASE 0 - local environment (zero API cost)
# ==========================================================================
def phase_env(p: Prober):
    print("\n[phase 0] local environment  (cost: none)")

    p.record("E01", "env", "Host", "What machine is this?", 0, "INFO",
             evidence={
                 "platform": platform.platform(),
                 "machine": platform.machine(),
                 "python": sys.version.split()[0],
                 "is_macos": sys.platform == "darwin",
                 "is_apple_silicon": platform.machine() == "arm64",
             },
             inference="Apple Silicon + macOS is the supported path for the "
                       "desktop-app routes.")

    proxy = {k: os.environ.get(k) for k in
             ("HTTPS_PROXY", "https_proxy", "HTTP_PROXY", "http_proxy",
              "NO_PROXY", "no_proxy", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE",
              "REQUESTS_CA_BUNDLE")}
    p.record("E02", "env", "Proxy / TLS trust",
             "Will HTTPS to api.figma.com survive the corporate MITM?", 0, "INFO",
             evidence={"env": {k: v for k, v in proxy.items() if v}},
             inference=("No proxy/CA env vars set. If E10 shows a TLS error, that "
                        "is the cause." if not any(proxy.values())
                        else "Proxy/CA configuration present."))

    app = Path("/Applications/Figma.app")
    ev = {"installed": app.exists()}
    if app.exists():
        try:
            pl = plistlib.loads((app / "Contents/Info.plist").read_bytes())
            ev["version"] = pl.get("CFBundleShortVersionString")
            ev["build"] = pl.get("CFBundleVersion")
        except Exception as e:                              # noqa: BLE001
            ev["plist_error"] = str(e)
    ver = ev.get("version") or ""
    major = 0
    m = re.match(r"(\d+)", ver)
    if m:
        major = int(m.group(1))
    ev["major"] = major
    ev["cdp_port_expected_blocked"] = major >= 126
    p.record("E03", "env", "Figma desktop app",
             "Is the desktop app present, and is its CDP port likely stripped?",
             0, "INFO", evidence=ev,
             inference=("Figma %s detected. Versions >= 126.1.x strip "
                        "--remote-debugging-port; expect D01 to fail and only the "
                        "--remote-debugging-pipe path to work." % ver
                        if major >= 126 else
                        "Figma %s detected." % (ver or "unknown")))

    tools = {}
    for t in ("node", "npm", "npx", "agent-browser", "figma-use", "jq",
              "unzip", "osascript", "curl"):
        path = which(t)
        entry = {"present": bool(path), "path": path}
        if path and t in ("node", "npm"):
            rc, out, _ = run_cmd([t, "--version"], timeout=10)
            entry["version"] = out if rc == 0 else None
        tools[t] = entry
    p.record("E04", "env", "Toolchain",
             "Which extraction tools are already installed?", 0, "INFO",
             evidence=tools,
             inference="node+npm are required for any kiwi (.fig) decoding, "
                       "since kiwi-schema is JS-only.")


# ==========================================================================
# PHASE 1-3 - REST, ascending cost
# ==========================================================================
def phase_api(p: Prober, key: str, node: str):
    a = p.args

    # ---- auth-method detection (Tier 3, 1 unit) ----
    print("\n[phase 1] REST Tier 3  (cheapest: %s / %s)"
          % (TIER_BUDGET_LOW[3], TIER_BUDGET_HIGH[3]))

    r = p.api("A01", "Identity via X-Figma-Token",
              "Does the token authenticate, and as whom?",
              "/v1/me", 3,
              inference_fn=lambda js, _: (
                  "Authenticated as %s (handle=%s, id=%s)."
                  % (js.get("email"), js.get("handle"), js.get("id"))
                  if isinstance(js, dict) else ""),
              next_action="If 401, regenerate the PAT (90-day max lifetime).")

    if r.get("http", {}).get("status") == 401:
        p.http.auth_header = "Authorization"
        p.api("A02", "Identity via Bearer",
              "Does the token work as a Bearer credential instead?",
              "/v1/me", 3,
              next_action="If this passes, your extension is sending the wrong "
                          "auth header. PATs use X-Figma-Token.")
        if p.log.records[-1].get("http", {}).get("status") != 200:
            p.http.auth_header = "X-Figma-Token"

    p.api("A03", "File metadata",
          "Does the file exist, can this account see it, and WHICH PLAN owns it?",
          "/v1/files/%s/meta" % key, 3,
          inference_fn=lambda js, _: _meta_inference(js, p),
          next_action="This is the cheapest possible existence+permission check. "
                      "Prefer it over GET file for health checks.")

    # ---- Tier 2 ----
    print("\n[phase 2] REST Tier 2  (%s / %s)"
          % (TIER_BUDGET_LOW[2], TIER_BUDGET_HIGH[2]))

    p.api("B01", "Version history",
          "Can we detect file changes cheaply, for cache invalidation?",
          "/v1/files/%s/versions?page_size=5" % key, 2,
          inference_fn=lambda js, _: (
              "%d versions visible; newest id=%s. This is the correct cache key "
              "for an offline pipeline." % (
                  len(js.get("versions", [])),
                  (js.get("versions") or [{}])[0].get("id"))
              if isinstance(js, dict) else ""),
          next_action="Poll this (Tier 2) instead of GET file (Tier 1) to decide "
                      "whether a re-dump is needed.",
          allow=not a.skip_tier2)

    p.api("B02", "Comments",
          "Is file_comments:read granted? Comments often document flow intent.",
          "/v1/files/%s/comments" % key, 2,
          inference_fn=lambda js, _: "%d comments." % len(js.get("comments", []))
          if isinstance(js, dict) else "",
          allow=not a.skip_tier2)

    p.api("B03", "Dev resources",
          "Are design->code links already mapped in the file?",
          "/v1/files/%s/dev_resources" % key, 2,
          allow=not a.skip_tier2)

    p.api("B04", "Local variables (Enterprise-only)",
          "Can we read the variables that drive CONDITIONAL prototype navigation?",
          "/v1/files/%s/variables/local" % key, 2,
          inference_fn=lambda js, _: _vars_inference(js),
          next_action="Variables ARE the app state machine for advanced "
                      "prototypes. If 403, that state must come from the .fig "
                      "kiwi payload instead.",
          allow=not a.skip_tier2)

    # ---- Tier 1: GATED ----
    print("\n[phase 3] REST Tier 1  (%s / %s)  %s"
          % (TIER_BUDGET_LOW[1], TIER_BUDGET_HIGH[1],
             "ENABLED" if a.allow_tier1 else "GATED OFF (use --allow-tier1)"))

    if a.allow_tier1 and p.rate_limit_type == "low":
        print("  !! rate-limit-type=low detected. Tier 1 budget is ~6 PER MONTH.")
        if not a.force:
            print("  !! Refusing to spend it. Re-run with --force to override.")
            a.allow_tier1 = False

    p.api("C01", "File tree, depth=1",
          "Can we enumerate pages, and how expensive is a full dump?",
          "/v1/files/%s?depth=1" % key, 1,
          inference_fn=lambda js, r_: _depth1_inference(js, r_),
          next_action="depth=1 is the cheapest useful Tier-1 shape: it names every "
                      "page without shipping the node tree.",
          allow=a.allow_tier1)

    if node:
        p.api("C02", "Node subtree + interactions",
              "Does the REST payload actually carry prototype routing?",
              "/v1/files/%s/nodes?ids=%s&depth=3"
              % (key, urllib.parse.quote(node)), 1,
              inference_fn=lambda js, _: _interactions_inference(js),
              next_action="Compare this against the same node decoded from .fig. "
                          "REST collapses multi-destination reactions into a "
                          "single transitionNodeID; kiwi does not.",
              allow=a.allow_tier1 and a.probe_node)
    else:
        p.record("C02", "api", "Node subtree + interactions",
                 "Does the REST payload carry prototype routing?", 1, "SKIPPED",
                 inference="No node-id in the supplied URL.",
                 next_action="Re-run with a URL containing ?node-id=, or pass "
                             "--node-id.")


def _meta_inference(js, p: Prober):
    if not isinstance(js, dict):
        return ""
    f = js.get("file", js)
    bits = ["name=%r" % f.get("name"),
            "editorType=%s" % f.get("editor_type", f.get("editorType")),
            "lastModified=%s" % f.get("last_touched_at", f.get("lastModified")),
            "linkAccess=%s" % f.get("link_access", f.get("linkAccess"))]
    p.facts["file_meta"] = f if isinstance(f, dict) else {}
    return "File visible. " + ", ".join(str(b) for b in bits)


def _vars_inference(js):
    if not isinstance(js, dict):
        return ""
    meta = js.get("meta", {}) or {}
    v = meta.get("variables", {}) or {}
    c = meta.get("variableCollections", {}) or {}
    return ("%d variables across %d collections. These are candidate route "
            "guards / feature flags." % (len(v), len(c)))


def _depth1_inference(js, r_):
    if not isinstance(js, dict):
        return ""
    doc = js.get("document", {}) or {}
    pages = doc.get("children", []) or []
    names = [c.get("name") for c in pages][:20]
    return ("%d pages: %s. depth=1 response was %s bytes; a full dump will be "
            "orders of magnitude larger."
            % (len(pages), names, r_.get("body_bytes")))


def _interactions_inference(js):
    """Walk the node payload counting real routing signal."""
    if not isinstance(js, dict):
        return ""
    inter = 0
    trans = 0
    triggers = {}
    actions = {}

    def walk(n):
        nonlocal inter, trans
        if not isinstance(n, dict):
            return
        for it in (n.get("interactions") or []):
            inter += 1
            t = ((it.get("trigger") or {}).get("type")) or "?"
            triggers[t] = triggers.get(t, 0) + 1
            for ac in (it.get("actions") or []):
                a = ac.get("type") or "?"
                actions[a] = actions.get(a, 0) + 1
        if n.get("transitionNodeID"):
            trans += 1
        for ch in (n.get("children") or []):
            walk(ch)

    for wrapper in (js.get("nodes") or {}).values():
        if isinstance(wrapper, dict):
            walk(wrapper.get("document"))

    return ("interactions=%d, transitionNodeID=%d, triggers=%s, actions=%s. "
            "If transitionNodeID > 0 while interactions == 0, this payload is "
            "the LOSSY legacy shape and cannot represent multi-destination "
            "reactions." % (inter, trans, triggers, actions))


# ==========================================================================
# PHASE 4 - desktop / local routes (zero API cost)
# ==========================================================================
def phase_local(p: Prober, key: str):
    print("\n[phase 4] local + desktop routes  (cost: none)")

    # --- CDP TCP port ---
    ports = [int(x) for x in str(p.args.cdp_ports).split(",") if x.strip()]
    found = {}
    for port in ports:
        s = socket.socket()
        s.settimeout(1.5)
        ok = False
        try:
            s.connect(("127.0.0.1", port))
            ok = True
        except Exception:                                   # noqa: BLE001
            ok = False
        finally:
            s.close()
        detail = None
        if ok:
            r = p.http.request("http://127.0.0.1:%d/json/version" % port,
                               authed=False)
            detail = {"status": r["status"],
                      "browser": (r.get("_json") or {}).get("Browser")}
        found[port] = {"tcp_open": ok, "cdp": detail}

    any_open = any(v["tcp_open"] for v in found.values())
    p.record("D01", "local", "CDP debugging port",
             "Is any Chromium/Electron CDP endpoint listening?",
             0, "PASS" if any_open else "FAIL",
             evidence=found,
             inference=("A CDP endpoint is reachable." if any_open else
                        "Nothing listening. On Figma >= 126.1.x this is expected: "
                        "the app strips --remote-debugging-port. Only "
                        "'figma-use daemon start --pipe' "
                        "(--remote-debugging-pipe) can attach."),
             next_action=("Confirm which app owns the port before trusting it."
                          if any_open else
                          "Do NOT patch the Figma binary on a managed Mac. Use "
                          "the pipe transport or drop this route."))

    # --- .fig files on disk ---
    cands = []
    for d in (p.args.fig_dir or "~/Downloads,~/Desktop,~/Documents").split(","):
        cands += glob.glob(os.path.expanduser(d.strip()) + "/*.fig")
    if p.args.fig_file:
        cands.insert(0, os.path.expanduser(p.args.fig_file))
    cands = [c for c in cands if os.path.exists(c)]
    cands.sort(key=lambda c: os.path.getmtime(c), reverse=True)

    if not cands:
        p.record("D02", "local", ".fig offline parse",
                 "Is the highest-fidelity, zero-quota route available?",
                 0, "SKIPPED",
                 evidence={"searched": p.args.fig_dir},
                 inference="No .fig file found on disk.",
                 next_action="In Figma: Main menu > File > Save local copy. "
                             "If that menu item is ABSENT, the file owner has "
                             "restricted copying/exporting and this entire route "
                             "is closed - which is itself a decisive finding.")
    else:
        p.record("D02", "local", ".fig offline parse", 
                 "Is the highest-fidelity, zero-quota route available?",
                 0, "PASS", evidence=inspect_fig(cands[0]),
                 inference="A .fig is present and structurally parseable. This "
                           "route needs no token, no seat and no quota, and "
                           "carries the FULL reaction list that REST truncates.",
                 next_action="Decode canvas.fig with kiwi-schema (JS) to get "
                             "nodeChanges, then link by parent GUID.")

    # --- clipboard kiwi payload ---
    p.record("D03", "local", "Clipboard kiwi payload",
             "Can we harvest node data by Cmd+C, with no export permission?",
             0, *clipboard_probe())

    # --- desktop cache ---
    cache = Path(os.path.expanduser("~/Library/Application Support/Figma"))
    ev = {"path": str(cache), "exists": cache.exists()}
    if cache.exists():
        ev["entries"] = sorted([c.name for c in cache.iterdir()])[:20]
    p.record("D04", "local", "Figma desktop cache",
             "Is there a local cache that already holds the scenegraph?",
             0, "INFO", evidence=ev,
             inference="Informational only. Undocumented and unstable; listed "
                       "for completeness, not recommended.")

    # --- public web surface / bot blocking ---
    for pid, label, url in (
        ("D05", "Design URL (bot check)",
         "https://www.figma.com/design/%s/probe" % key),
        ("D06", "Prototype presentation view",
         "https://www.figma.com/proto/%s/probe" % key),
    ):
        r = p.http.request(url, authed=False)
        st = r["status"]
        server = r["response_headers"].get("server", "")
        cfray = r["response_headers"].get("cf-ray")
        blocked = st in (403, 429, 503) or "cloudflare" in server.lower()
        p.record(pid, "local", label,
                 "Does the web surface challenge an unauthenticated automated "
                 "client?", 0,
                 "BLOCKED" if blocked else ("PASS" if st and st < 400 else "FAIL"),
                 evidence={"status": st, "server": server, "cf_ray": cfray,
                           "final_url": r["final_url"]},
                 inference=("Edge protection is answering. Any headless "
                            "navigation here will be challenged; drive it from "
                            "your already-authenticated Chrome profile instead."
                            if blocked else
                            "No challenge on an anonymous request. Auth-gated "
                            "redirect is still expected."),
                 http=r)


def inspect_fig(path: str) -> dict:
    """Structurally validate a .fig without needing kiwi. Proves route viability."""
    ev = {"path": path, "bytes": os.path.getsize(path),
          "mtime": datetime.fromtimestamp(os.path.getmtime(path),
                                          timezone.utc).isoformat()}
    raw = None
    try:
        if zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                ev["container"] = "zip"
                ev["entries"] = z.namelist()[:20]
                target = next((n for n in z.namelist()
                               if n.endswith("canvas.fig")), None)
                ev["canvas_entry"] = target
                if target:
                    raw = z.read(target)
                if "meta.json" in z.namelist():
                    try:
                        ev["meta_json"] = json.loads(z.read("meta.json"))
                    except Exception:                       # noqa: BLE001
                        pass
        else:
            ev["container"] = "raw"
            raw = Path(path).read_bytes()
    except Exception as e:                                  # noqa: BLE001
        ev["container_error"] = "%s: %s" % (type(e).__name__, e)
        return ev

    if not raw:
        ev["canvas_error"] = "no canvas payload found"
        return ev

    ev["prelude"] = raw[:8].decode("latin-1")
    ev["prelude_ok"] = raw[:8] == b"fig-kiwi"
    if not ev["prelude_ok"]:
        return ev
    ev["format_version"] = int.from_bytes(raw[8:12], "little")

    chunks = []
    off = 12
    idx = 0
    while off + 4 <= len(raw) and idx < 8:
        n = int.from_bytes(raw[off:off + 4], "little")
        off += 4
        if n <= 0 or off + n > len(raw):
            break
        data = raw[off:off + n]
        off += n
        c = {"index": idx, "compressed_bytes": n}
        if data[:4] == b"\x28\xb5\x2f\xfd":
            c["compression"] = "zstd (magic 0xFD2FB528)"
        else:
            c["compression"] = "deflate-raw (assumed)"
            try:
                out = zlib.decompressobj(-15).decompress(data)
                c["decompressed_bytes"] = len(out)
                c["decompress_ok"] = True
            except Exception as e:                          # noqa: BLE001
                c["decompress_ok"] = False
                c["decompress_error"] = str(e)
        chunks.append(c)
        idx += 1
    ev["chunks"] = chunks
    ev["schema_chunk_readable"] = bool(
        chunks and chunks[0].get("decompress_ok"))
    ev["message_needs_zstd"] = bool(
        len(chunks) > 1 and "zstd" in chunks[1].get("compression", ""))
    return ev


def clipboard_probe():
    """Read the macOS clipboard HTML flavor and look for Figma's kiwi payload."""
    if sys.platform != "darwin":
        return ("SKIPPED", {"reason": "not macOS"},
                "Clipboard route is macOS-specific here.", None)
    script = 'try\nreturn the clipboard as «class HTML»\non error e\nreturn "ERR:" & e\nend try'
    tmp = Path("/tmp/.figma_probe_clip.applescript")
    try:
        tmp.write_text(script, encoding="utf-8")
        rc, out, err = run_cmd(["osascript", str(tmp)], timeout=15)
    finally:
        try:
            tmp.unlink()
        except Exception:                                   # noqa: BLE001
            pass

    if rc != 0 or out.startswith("ERR:"):
        return ("SKIPPED",
                {"rc": rc, "stderr": err[:300], "stdout": out[:300]},
                "No HTML flavor on the clipboard (nothing copied from Figma yet).",
                "Select frames in Figma, press Cmd+C, then re-run with --only D03.")

    m = re.search(r"«data HTML([0-9A-Fa-f]+)»", out) or re.search(r"([0-9A-Fa-f]{64,})", out)
    if not m:
        return ("FAIL", {"raw_head": out[:200]},
                "Clipboard HTML present but not in the expected hex envelope.",
                None)
    try:
        html = bytes.fromhex(m.group(1)).decode("utf-8", "replace")
    except Exception as e:                                  # noqa: BLE001
        return ("ERROR", {"hex_error": str(e)}, "Could not decode clipboard hex.",
                None)

    has_meta = "(figmeta)" in html
    has_fig = "(figma)" in html
    b64 = re.search(r"<!--\(figma\)(.*?)\(/figma\)-->", html, re.S)
    payload_len = len(b64.group(1).strip()) if b64 else 0
    decoded = 0
    if b64:
        try:
            decoded = len(base64.b64decode(b64.group(1).strip() + "=="))
        except Exception:                                   # noqa: BLE001
            decoded = -1
    meta = None
    mm = re.search(r"<!--\(figmeta\)(.*?)\(/figmeta\)-->", html, re.S)
    if mm:
        try:
            meta = json.loads(base64.b64decode(mm.group(1).strip() + "==")
                              .decode("utf-8", "replace"))
        except Exception:                                   # noqa: BLE001
            meta = "present-but-unparsed"

    ev = {"html_bytes": len(html), "has_figmeta": has_meta, "has_figma": has_fig,
          "b64_chars": payload_len, "decoded_bytes": decoded, "figmeta": meta}
    if has_fig and decoded > 0:
        return ("PASS", ev,
                "Live kiwi payload on the clipboard. This route needs NO token, "
                "NO seat and NO export permission - only the ability to copy.",
                "Decode with fig-kiwi readHTMLMessage(); scope it per-selection "
                "for incremental syncs.")
    return ("FAIL", ev, "Clipboard HTML has no Figma payload.",
            "Copy frames inside Figma first.")


# ==========================================================================
# Synthesis
# ==========================================================================
def synthesize(p: Prober, key: str, node: str) -> dict:
    by_id = {}
    for r in p.log.records:
        if r.get("type") == "probe":
            by_id[r["probe_id"]] = r

    def st(pid):
        return by_id.get(pid, {}).get("outcome", "SKIPPED")

    def http_status(pid):
        return by_id.get(pid, {}).get("http", {}).get("status")

    seat = ("View/Collab (LOW bucket)" if p.rate_limit_type == "low"
            else "Dev/Full (HIGH bucket)" if p.rate_limit_type == "high"
            else "UNDETERMINED - no 429 observed this run")

    routes = []

    routes.append({
        "route": "REST Tier 1 (GET file / nodes / images)",
        "status": ("OPEN" if st("C01") == "PASS" else
                   "RATE-LIMITED" if http_status("C01") == 429 else
                   "UNTESTED (gated)" if st("C01") == "SKIPPED" else "BLOCKED"),
        "quota": TIER_BUDGET_LOW[1] if p.rate_limit_type == "low"
                 else TIER_BUDGET_HIGH[1] if p.rate_limit_type == "high" else "unknown",
        "prototype_fidelity": "LOSSY - single transitionNodeID per node",
        "blocker": None if st("C01") == "PASS" else "see probe C01",
    })
    routes.append({
        "route": "REST Tier 2/3 (meta, versions, comments, variables)",
        "status": "OPEN" if st("A03") == "PASS" else "BLOCKED",
        "quota": "%s / %s" % (TIER_BUDGET_LOW[2], TIER_BUDGET_LOW[3]),
        "prototype_fidelity": "n/a - metadata only",
        "blocker": None if st("A03") == "PASS" else "see probe A03",
    })
    routes.append({
        "route": ".fig offline kiwi parse",
        "status": "OPEN" if st("D02") == "PASS" else "NEEDS EXPORT",
        "quota": "unlimited",
        "prototype_fidelity": "FULL - complete reaction list incl. conditionals",
        "blocker": None if st("D02") == "PASS"
                   else "no .fig on disk; verify 'Save local copy' is not "
                        "restricted by the file owner",
    })
    routes.append({
        "route": "Clipboard kiwi payload",
        "status": "OPEN" if st("D03") == "PASS" else "UNPROVEN",
        "quota": "unlimited",
        "prototype_fidelity": "FULL, selection-scoped",
        "blocker": None if st("D03") == "PASS" else "copy frames in Figma, re-run",
    })
    routes.append({
        "route": "Desktop CDP (figma-use / agent-browser)",
        "status": "OPEN" if st("D01") == "PASS" else "CLOSED",
        "quota": "unlimited",
        "prototype_fidelity": "FULL + write access",
        "blocker": None if st("D01") == "PASS"
                   else "Figma >=126.1.x strips --remote-debugging-port; only "
                        "--remote-debugging-pipe remains",
    })
    routes.append({
        "route": "Official Figma MCP server",
        "status": "UNAVAILABLE TO pi",
        "quota": "600/day Enterprise Dev-Full; 6/month otherwise",
        "prototype_fidelity": "good",
        "blocker": "pi ships no MCP client AND is not in Figma's MCP catalog "
                   "allowlist. Two independent blockers.",
    })

    open_full = [r for r in routes
                 if r["status"] == "OPEN" and r["prototype_fidelity"].startswith("FULL")]
    if open_full:
        rec = ("PRIMARY: %s. It is the only open route that preserves the full "
               "reaction list, and it costs nothing per query."
               % open_full[0]["route"])
    elif st("C01") == "PASS":
        rec = ("PRIMARY: REST Tier 1 with aggressive caching. Accept lossy "
               "routing data and reconcile transitionNodeID manually.")
    else:
        rec = ("NO ROUTE IS CURRENTLY OPEN. Highest-leverage unblock: confirm "
               "whether Main menu > File > Save local copy exists for you. That "
               "single UI check decides everything.")

    verdict = {
        "schema_version": SCHEMA_VERSION,
        "run_id": p.log.run_id,
        "generated_utc": _now(),
        "file_key": key,
        "node_id": node,
        "identity": {
            "seat_class": seat,
            "rate_limit_type": p.rate_limit_type,
            "plan_tier_of_file": p.plan_tier,
            "plan_tier_note": ("X-Figma-Plan-Tier reflects the plan that OWNS "
                               "THE FILE, not your best seat elsewhere. A file "
                               "in personal drafts gets starter limits even for "
                               "an Enterprise member."),
        },
        "scopes": {
            "observed_on_token": sorted(p.scopes_seen),
            "reported_missing": sorted(p.scopes_missing),
            "full_scope_catalog": ALL_SCOPES,
        },
        "budget_spent_this_run": {
            "tier1_calls": p.spent[1],
            "tier2_calls": p.spent[2],
            "tier3_calls": p.spent[3],
        },
        "circuit_breaker_tripped": p.tripped,
        "routes": routes,
        "recommendation": rec,
    }
    return verdict


def write_summary(p: Prober, verdict: dict, outdir: Path):
    L = []
    L.append("# Figma access audit\n")
    L.append("- Run: `%s`" % verdict["run_id"])
    L.append("- Generated: %s" % verdict["generated_utc"])
    L.append("- File key: `%s`" % verdict["file_key"])
    L.append("- Node id: `%s`\n" % (verdict["node_id"] or "(none supplied)"))

    ident = verdict["identity"]
    L.append("## Identity and budget\n")
    L.append("| Field | Value |")
    L.append("|---|---|")
    L.append("| Seat class | %s |" % ident["seat_class"])
    L.append("| `X-Figma-Rate-Limit-Type` | `%s` |" % ident["rate_limit_type"])
    L.append("| `X-Figma-Plan-Tier` (file owner) | `%s` |" % ident["plan_tier_of_file"])
    L.append("| Tier 1 calls spent | %d |" % verdict["budget_spent_this_run"]["tier1_calls"])
    L.append("| Tier 2 calls spent | %d |" % verdict["budget_spent_this_run"]["tier2_calls"])
    L.append("| Tier 3 calls spent | %d |" % verdict["budget_spent_this_run"]["tier3_calls"])
    L.append("| Circuit breaker | %s |\n" % ("TRIPPED" if verdict["circuit_breaker_tripped"] else "not tripped"))
    L.append("> %s\n" % ident["plan_tier_note"])

    L.append("## Route feasibility\n")
    L.append("| Route | Status | Quota | Prototype fidelity | Blocker |")
    L.append("|---|---|---|---|---|")
    for r in verdict["routes"]:
        L.append("| %s | **%s** | %s | %s | %s |" % (
            r["route"], r["status"], r["quota"], r["prototype_fidelity"],
            r["blocker"] or "-"))
    L.append("")

    L.append("## Recommendation\n")
    L.append(verdict["recommendation"] + "\n")

    L.append("## Probe ledger\n")
    L.append("| # | ID | Probe | Tier | Outcome | HTTP | Question answered |")
    L.append("|---|---|---|---|---|---|---|")
    for r in p.log.records:
        if r.get("type") != "probe":
            continue
        L.append("| %d | %s | %s | %s | %s | %s | %s |" % (
            r["seq"], r["probe_id"], r["name"],
            r["cost"]["rate_limit_tier"] or "-",
            r["outcome"],
            r.get("http", {}).get("status", "-"),
            r["question"]))
    L.append("")

    L.append("## Failures and inferences\n")
    for r in p.log.records:
        if r.get("type") != "probe":
            continue
        if r["outcome"] in ("PASS", "INFO"):
            continue
        L.append("### %s - %s (%s)\n" % (r["probe_id"], r["name"], r["outcome"]))
        if r.get("inference"):
            L.append("**Inference:** %s\n" % r["inference"])
        if r.get("next_action"):
            L.append("**Next action:** %s\n" % r["next_action"])
        diag = r.get("http", {}).get("figma_diag")
        if diag:
            L.append("**Figma diagnostic headers:** `%s`\n" % json.dumps(diag))
    (outdir / "summary.md").write_text("\n".join(L), encoding="utf-8")


# ==========================================================================
# main
# ==========================================================================
def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Audit every available route into a Figma file.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples
  export FIGMA_TOKEN=figd_xxx
  python3 figma_probe.py --url 'https://www.figma.com/design/KEY/App?node-id=24626-100'
  python3 figma_probe.py --url ... --allow-tier1        # spend Tier 1 knowingly
  python3 figma_probe.py --url ... --dry-run            # show plan and cost only
""")
    ap.add_argument("--url", help="Figma file URL (or bare file key)")
    ap.add_argument("--file-key", help="Override the parsed file key")
    ap.add_argument("--node-id", help="Override the parsed node id (API form 1:23)")
    ap.add_argument("--token", default=os.environ.get("FIGMA_TOKEN", ""),
                    help="PAT. Prefer the FIGMA_TOKEN env var.")
    ap.add_argument("--outdir", default="./figma-audit")
    ap.add_argument("--allow-tier1", action="store_true",
                    help="Permit Tier 1 calls (may cost a MONTHLY quota).")
    ap.add_argument("--force", action="store_true",
                    help="Spend Tier 1 even when a low-seat bucket is detected.")
    ap.add_argument("--skip-tier2", action="store_true")
    ap.add_argument("--skip-api", action="store_true", help="Local probes only.")
    ap.add_argument("--skip-local", action="store_true")
    ap.add_argument("--probe-node", action="store_true", default=True)
    ap.add_argument("--cdp-ports", default="9222,9223,9225,3845")
    ap.add_argument("--fig-dir", default="~/Downloads,~/Desktop,~/Documents")
    ap.add_argument("--fig-file", default=None)
    ap.add_argument("--timeout", type=int, default=40)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--verbose", "-v", action="store_true")
    a = ap.parse_args(argv)

    if not a.url and not a.file_key:
        ap.error("supply --url or --file-key")

    parsed = parse_figma_url(a.url or a.file_key or "")
    key = a.file_key or parsed["file_key"]
    node = a.node_id or parsed["node_id_api"]
    if not key:
        ap.error("could not extract a file key from %r" % a.url)

    if a.dry_run:
        print("DRY RUN - nothing will be called.\n")
        print("  file_key : %s" % key)
        print("  node_id  : %s" % (node or "(none)"))
        print("  token    : %s" % json.dumps(token_fingerprint(a.token)))
        print("\n  Tier 3 calls planned : 2  (budget %s)" % TIER_BUDGET_LOW[3])
        print("  Tier 2 calls planned : %d  (budget %s)"
              % (0 if a.skip_tier2 else 4, TIER_BUDGET_LOW[2]))
        print("  Tier 1 calls planned : %d  (budget %s)  <-- %s"
              % ((2 if node else 1) if a.allow_tier1 else 0,
                 TIER_BUDGET_LOW[1],
                 "ENABLED" if a.allow_tier1 else "gated off"))
        print("  Local probes         : 8  (free)")
        return 0

    if not a.token and not a.skip_api:
        print("No token (FIGMA_TOKEN unset). Running local probes only.")
        a.skip_api = True

    run_id = "%s-%s" % (datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ"),
                        hashlib.sha256(str(time.time()).encode()).hexdigest()[:6])
    outdir = Path(os.path.expanduser(a.outdir)) / run_id

    context = {
        "file_key": key,
        "node_id_api": node,
        "url_parse": parsed,
        "token": token_fingerprint(a.token),
        "flags": {k: v for k, v in vars(a).items() if k != "token"},
        "host": {"platform": platform.platform(), "python": sys.version.split()[0]},
    }
    log = AuditLog(outdir, run_id, context)
    p = Prober(a, log)

    print("=" * 72)
    print("Figma access audit  |  run %s" % run_id)
    print("file_key=%s  node=%s" % (key, node or "-"))
    print("=" * 72)

    phase_env(p)
    if not a.skip_api:
        phase_api(p, key, node)
    else:
        print("\n[phase 1-3] REST probes SKIPPED")
    if not a.skip_local:
        phase_local(p, key)

    verdict = synthesize(p, key, node)
    (outdir / "verdict.json").write_text(
        json.dumps(verdict, indent=2, ensure_ascii=False), encoding="utf-8")
    write_summary(p, verdict, outdir)

    print("\n" + "=" * 72)
    print("VERDICT")
    print("=" * 72)
    print("  seat class : %s" % verdict["identity"]["seat_class"])
    print("  file plan  : %s" % verdict["identity"]["plan_tier_of_file"])
    print("  spent      : T1=%d T2=%d T3=%d"
          % (p.spent[1], p.spent[2], p.spent[3]))
    print("")
    for r in verdict["routes"]:
        print("  %-46s %s" % (r["route"], r["status"]))
    print("\n  %s\n" % verdict["recommendation"])
    print("  audit.jsonl  -> %s" % (outdir / "audit.jsonl"))
    print("  verdict.json -> %s" % (outdir / "verdict.json"))
    print("  summary.md   -> %s" % (outdir / "summary.md"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
