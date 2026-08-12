#!/usr/bin/env python3
"""
flow_kit.py — the Flow Spec harness (supersedes validate_flow.py).

The LLM emits schema-constrained JSON. This tool ingests it, writes the
canonical YAML block into flow.md, validates the *graph* (constrained decoding
only guarantees the shape), and renders every derived view deterministically —
so the model never spends a token drawing a diagram, writing Gherkin, or
regenerating a file it already produced.

Commands
    emit-schema                       print flowspec.schema.json
    emit-prompt                       print the compact grammar card for prompts
    ingest  FLOW.md --from IN.json     replace the spec from LLM JSON
    ingest  FLOW.md --from IN.json --merge
                                      id-keyed upsert (iteration; no whole-file regen)
    gen     FLOW.md [--all]           regenerate mermaid + matrix + assumptions
                                      (--all also writes acceptance.feature, tasks.md, machine.ts)
    check   FLOW.md                   validate + fail on drift (CI)
    repair  IN.(json|yaml)            print a line-anchored error to feed back, or OK

Dependencies: PyYAML.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import pathlib
import re
import sys

import yaml

BLOCK_RE = re.compile(r"(```yaml flowspec\n)(.*?)(```)", re.DOTALL)
STRONG_EVIDENCE = {"observed", "extracted", "named"}
TERMINALS = {"EXIT", "ENTRY"}
CANON_STATE_ORDER = [
    "loading", "empty", "partial", "ready", "form", "entry", "submitting",
    "processing", "success", "error.validation", "error.declined",
    "error.network", "error.permission", "error.conflict", "offline",
    "disabled", "readonly",
]
ID_PATTERNS = {
    "screen": re.compile(r"^S[0-9]+[a-z]?_[a-z0-9_]+$"),
    "transition": re.compile(r"^T[0-9]+$"),
    "address": re.compile(r"^(S[0-9]+[a-z]?_[a-z0-9_]+#[a-z0-9._]+|EXIT|ENTRY)$"),
    "event": re.compile(r"^[a-z_]+:[a-z0-9_]+$"),
    "assumption": re.compile(r"^A[0-9]+$"),
}


# ------------------------------------------------------------ io / parsing --

def read_block(path: pathlib.Path):
    text = path.read_text(encoding="utf-8")
    m = BLOCK_RE.search(text)
    if not m:
        sys.exit(f"error: no ```yaml flowspec block in {path}")
    try:
        data = yaml.safe_load(m.group(2)) or {}
    except yaml.YAMLError as exc:
        sys.exit(f"error: flowspec YAML is invalid\n{exc}")
    return text, data


def write_block(path: pathlib.Path, text: str, data: dict) -> str:
    body = yaml.safe_dump(data, sort_keys=False, allow_unicode=True,
                          width=100, default_flow_style=False).rstrip("\n")
    return BLOCK_RE.sub(lambda m: m.group(1) + body + "\n" + m.group(3),
                        text, count=1)


def sid(screen: str, state: str) -> str:
    return f"{screen}__{state}".replace(".", "_").replace("-", "_")


def split_addr(addr: str):
    if addr in TERMINALS or "#" not in (addr or ""):
        return addr, None
    scr, st = addr.split("#", 1)
    return scr, st


def spec_hash(data: dict) -> str:
    payload = json.dumps({k: v for k, v in data.items()},
                         sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()[:8]


# ------------------------------------------------------- id-keyed merging --

def _upsert(base_list, new_list, key="id"):
    """Replace items with a matching key, append the rest. Order preserved;
    new ids appended in arrival order. No positional/index reasoning — the
    documented failure mode of RFC-6902-style patches."""
    base_list = list(base_list or [])
    index = {item.get(key): i for i, item in enumerate(base_list)
             if isinstance(item, dict)}
    for item in new_list or []:
        k = item.get(key) if isinstance(item, dict) else None
        if k is not None and k in index:
            if key == "id" and "states" in base_list[index[k]] and "states" in item:
                item = {**base_list[index[k]], **item,
                        "states": _upsert(base_list[index[k]]["states"],
                                          item["states"])}
            else:
                item = {**base_list[index[k]], **item}
            base_list[index[k]] = item
        else:
            base_list.append(item)
            if k is not None:
                index[k] = len(base_list) - 1
    return base_list


def merge(base: dict, partial: dict) -> dict:
    out = dict(base)
    out["flow"] = {**base.get("flow", {}), **partial.get("flow", {})}
    out["screens"] = _upsert(base.get("screens"), partial.get("screens"))
    out["transitions"] = _upsert(base.get("transitions"),
                                 partial.get("transitions"))
    out["assumptions"] = _upsert(base.get("assumptions"),
                                 partial.get("assumptions"))
    rules = list(base.get("rules") or [])
    for r in partial.get("rules") or []:
        if r not in rules:
            rules.append(r)
    if rules:
        out["rules"] = rules
    return out


# ---------------------------------------------------------- graph checks --

def validate(data: dict, base_dir: pathlib.Path):
    errors, warnings = [], []
    flow = data.get("flow") or {}
    screens = data.get("screens") or []
    transitions = data.get("transitions") or []

    for f in ("id", "version", "status", "goal"):
        if not flow.get(f):
            errors.append(f"flow.{f} is required")

    index = {}
    first_state_addr = None
    for scr in screens:
        s_id = scr.get("id")
        if not s_id:
            errors.append("a screen is missing id"); continue
        if not ID_PATTERNS["screen"].match(s_id):
            errors.append(f"screen id '{s_id}' must match S<n>_<slug>")
        if s_id in index:
            errors.append(f"duplicate screen id: {s_id}")
        states = {st["id"]: st for st in (scr.get("states") or []) if st.get("id")}
        if not states:
            errors.append(f"{s_id}: no states")
        index[s_id] = states
        if first_state_addr is None and states:
            first_state_addr = f"{s_id}#{next(iter(states))}"
        for st_id, st in states.items():
            if st.get("evidence") not in {"observed", "extracted", "named",
                                          "inferred", "assumed"}:
                errors.append(f"{s_id}#{st_id}: bad/missing evidence")
        png = (scr.get("evidence") or {}).get("png")
        if png and not (base_dir / png).exists():
            warnings.append(f"{s_id}: evidence png missing on disk: {png}")

    seen, outgoing, incoming = set(), {}, set()
    for tr in transitions:
        t = tr.get("id", "<no id>")
        if t in seen:
            errors.append(f"duplicate transition id: {t}")
        seen.add(t)
        if not ID_PATTERNS["transition"].match(t):
            errors.append(f"transition id '{t}' must match T<n>")
        for f in ("from", "to", "event"):
            if not tr.get(f):
                errors.append(f"{t}: missing {f}")
        ev = tr.get("event", "")
        if ev and not ID_PATTERNS["event"].match(ev):
            errors.append(f"{t}: event '{ev}' must be verb:target")
        for role in ("from", "to"):
            addr = tr.get(role)
            if not addr:
                continue
            if not ID_PATTERNS["address"].match(addr):
                errors.append(f"{t}: {role}='{addr}' must be SCREEN#state"); continue
            scr, st = split_addr(addr)
            if scr in TERMINALS:
                continue
            if scr not in index:
                errors.append(f"{t}: {role} unknown screen '{scr}'")
            elif st not in index[scr]:
                errors.append(f"{t}: {role} unknown state '{st}' on {scr}")
        outgoing.setdefault(tr.get("from"), []).append(tr)
        if tr.get("to") not in TERMINALS:
            incoming.add(tr.get("to"))
        if flow.get("status") == "approved" and tr.get("evidence") == "assumed":
            errors.append(f"{t}: approved spec may not contain an 'assumed' "
                          "transition")

    groups = {}
    for tr in transitions:
        groups.setdefault((tr.get("from"), tr.get("event")), []).append(tr)
    for (frm, evt), grp in groups.items():
        if len(grp) < 2:
            continue
        guards = [str(t.get("guard", "true")).strip() for t in grp]
        if "true" in guards:
            continue
        complementary = any(f"!{g}" in guards for g in guards)
        subjects = {re.split(r"[=!<>]", g, maxsplit=1)[0].strip() for g in guards}
        same_subject = len(subjects) == 1 and all(re.search(r"[=!<>]", g)
                                                  for g in guards)
        if not (complementary or same_subject):
            warnings.append(f"guard-gap risk on {frm}+{evt}: {guards} — "
                            "confirm exhaustive and mutually exclusive")

    for s_id, states in index.items():
        for st_id, st in states.items():
            addr = f"{s_id}#{st_id}"
            if addr not in incoming and addr != first_state_addr:
                warnings.append(f"unreachable state: {addr}")
            if not outgoing.get(addr) and not st.get("terminal"):
                warnings.append(f"dead end (no outgoing transition): {addr}")

    for a in data.get("assumptions") or []:
        if a.get("id") and not ID_PATTERNS["assumption"].match(a["id"]):
            errors.append(f"assumption id '{a['id']}' must match A<n>")
        if a.get("blocking") and flow.get("status") == "approved":
            errors.append(f"{a.get('id')}: blocking assumption unresolved "
                          "but status is approved")
    return errors, warnings


# ------------------------------------------------------------- rendering --

def _label(tr) -> str:
    guard = str(tr.get("guard", "true")).strip()
    txt = f"{tr.get('id')} {tr.get('event')}".replace(":", " ")
    if guard and guard != "true":
        txt += f" [{guard}]"
    return txt


def mermaid(data) -> str:
    screens = data.get("screens") or []
    transitions = data.get("transitions") or []
    L = ["```mermaid", "stateDiagram-v2", "    direction LR"]
    if screens:
        L.append(f"    [*] --> {screens[0]['id']}")
    for scr in screens:
        s = scr["id"]
        states = [st["id"] for st in (scr.get("states") or [])]
        L.append(f'    state "{s} · {scr.get("title", s)}" as {s} {{')
        for st in states:                      # declare nodes before edges
            L.append(f'        state "{st}" as {sid(s, st)}')
        if states:
            L.append(f"        [*] --> {sid(s, states[0])}")
        for tr in transitions:
            fs, fst = split_addr(tr.get("from", ""))
            ts, tst = split_addr(tr.get("to", ""))
            if fs == s and ts == s:
                L.append(f"        {sid(fs, fst)} --> {sid(ts, tst)} : {_label(tr)}")
        L.append("    }")
    for tr in transitions:
        fs, fst = split_addr(tr.get("from", ""))
        ts, tst = split_addr(tr.get("to", ""))
        if fs == ts:
            continue
        src = sid(fs, fst) if fst else fs
        dst = "[*]" if ts in TERMINALS else (sid(ts, tst) if tst else ts)
        L.append(f"    {src} --> {dst} : {_label(tr)}")
    L.append("```")
    return "\n".join(L)


def matrix(data) -> str:
    screens = data.get("screens") or []
    present = []
    for scr in screens:
        for st in scr.get("states") or []:
            if st["id"] not in present:
                present.append(st["id"])
    present.sort(key=lambda s: (CANON_STATE_ORDER.index(s)
                                if s in CANON_STATE_ORDER else 99, s))
    rows = ["| Screen | " + " | ".join(present) + " |",
            "|---" * (len(present) + 1) + "|"]
    for scr in screens:
        states = {st["id"]: st for st in (scr.get("states") or [])}
        cells = []
        for name in present:
            st = states.get(name)
            if not st:
                cells.append("—")
            elif st.get("evidence") in STRONG_EVIDENCE:
                cells.append("✅ designed")
            else:
                cells.append(f"⚠ {st.get('evidence')}")
        rows.append(f"| {scr['id']} | " + " | ".join(cells) + " |")
    rows += ["", "✅ evidence in design · ⚠ inferred/invented — confirm with "
             "designer · — not applicable"]
    return "\n".join(rows)


def assumptions_md(data) -> str:
    items = data.get("assumptions") or []
    if not items:
        return "_No open assumptions._"
    out, block = [], [a for a in items if a.get("blocking")]
    free = [a for a in items if not a.get("blocking")]
    if block:
        out.append("**Blocking — resolve before approval**\n")
        out += [f"- **{a.get('id')}** {a.get('statement')}  \n  _why:_ {a.get('why','')}"
                for a in block]
        out.append("")
    if free:
        out.append("**Non-blocking**\n")
        out += [f"- **{a.get('id')}** {a.get('statement')}  \n  _why:_ {a.get('why','')}"
                for a in free]
    return "\n".join(out)


def gherkin(data) -> str:
    flow = data.get("flow") or {}
    L = [f"Feature: {flow.get('id')} — {str(flow.get('goal','')).strip()}", ""]
    for tr in data.get("transitions") or []:
        fs, fst = split_addr(tr.get("from", ""))
        ts, tst = split_addr(tr.get("to", ""))
        guard = str(tr.get("guard", "true")).strip()
        L.append(f"  Scenario: {tr.get('id')} {tr.get('event')}")
        L.append(f'    Given the "{fs}" screen is in state "{fst}"')
        if guard and guard != "true":
            L.append(f"    And the condition {guard} holds")
        L.append(f"    When the event {tr.get('event')} occurs")
        if ts in TERMINALS:
            L.append("    Then the user leaves the flow")
        else:
            L.append(f'    Then the "{ts}" screen is shown in state "{tst}"')
        if tr.get("effect") == "navigate":
            L.append("    And the browser URL matches that screen's route")
        L.append("")
    return "\n".join(L)


def tasks_md(data) -> str:
    L = ["# Implementation tasks", "",
         "> Generated from flow.md. One task per screen (all its states), "
         "then the transition module. Do not start a task until the previous "
         "one passes acceptance.feature + pixel diff.", ""]
    for i, scr in enumerate(data.get("screens") or [], 1):
        states = ", ".join(st["id"] for st in (scr.get("states") or []))
        comps = ", ".join(scr.get("components") or []) or "—"
        L.append(f"- [ ] **T{i}. {scr['id']} — {scr.get('title', '')}**  \n"
                 f"  states: {states}  \n"
                 f"  components: {comps}  \n"
                 f"  route: {scr.get('route', '—')}")
    L.append(f"- [ ] **T{len(data.get('screens') or []) + 1}. Transition module** — "
             "implement all transitions[] as one navigation/state unit derived "
             "from the spec.")
    return "\n".join(L)


def xstate_ts(data) -> str:
    flow = data.get("flow") or {}
    lines = ["// GENERATED from flow.md — do not hand-edit.",
             "import { setup } from 'xstate';", "",
             f"export const {re.sub(r'[^a-zA-Z0-9]', '', flow.get('id','flow').title())}Machine = setup({{}}).createMachine({{",
             f"  id: '{flow.get('id')}',"]
    screens = data.get("screens") or []
    if screens:
        lines.append(f"  initial: '{screens[0]['id']}',")
    fid = flow.get("id")

    def target_expr(to: str, cur_screen: str) -> str:
        if to in TERMINALS:                      # leaving the flow
            return "undefined"
        scr, st = split_addr(to)
        if scr == cur_screen:                    # sibling state
            return f"'{st}'"
        return f"'#{fid}.{scr}.{st}'"             # cross-screen (quoted!)

    def transition_obj(t, cur_screen):
        guard = t.get("guard")
        g = f", guard: '{guard}'" if guard and guard != "true" else ""
        return f"{{ target: {target_expr(t['to'], cur_screen)}{g} }}"

    lines.append("  states: {")
    for scr in screens:
        s = scr["id"]
        st_defs = scr.get("states") or []
        states = [st["id"] for st in st_defs]
        terminal = {st["id"] for st in st_defs if st.get("terminal")}
        lines.append(f"    {s}: {{")
        if states:
            lines.append(f"      initial: '{states[0]}',")
        lines.append("      states: {")
        for st in states:
            outs = [t for t in data.get("transitions") or []
                    if t.get("from") == f"{s}#{st}"]
            if not outs:
                lines.append(f"        '{st}': {{ type: 'final' }},"
                             if st in terminal else f"        '{st}': {{}},")
                continue
            # group by event: guarded variants of one event become an array
            by_event = {}
            for t in outs:
                by_event.setdefault(t["event"], []).append(t)
            lines.append(f"        '{st}': {{ on: {{")
            for event, group in by_event.items():
                if len(group) == 1:
                    lines.append(f"          '{event}': "
                                 f"{transition_obj(group[0], s)},")
                else:
                    variants = ", ".join(transition_obj(t, s) for t in group)
                    lines.append(f"          '{event}': [{variants}],")
            lines.append("        } },")
        lines.append("      }")
        lines.append("    },")
    lines.append("  }")
    lines.append("});")
    return "\n".join(lines)


def replace_marked(text: str, name: str, body: str) -> str:
    start = f"<!-- GENERATED:{name} — do not edit by hand -->"
    end = f"<!-- /GENERATED:{name} -->"
    pat = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    return pat.sub(f"{start}\n{body}\n{end}", text) if pat.search(text) else text


# --------------------------------------------------------------- grammar --

GRAMMAR_CARD = """\
FLOW SPEC — emit ONE JSON object matching this shape. No prose, no markdown.

flow: { id(kebab), version(int), status:"draft", spec_hash:null, goal(1 sentence),
        actors[], entry_points[], success_criteria[], non_goals[] }
screens[]: { id:"S<n>_<slug>", route, title,
             evidence:{png,css,layer}, data_requires[], data_source, components[],
             a11y:{focus_on_enter,live_region}, responsive:{breakpoints[],notes},
             states[]: { id, entry_condition, copy, evidence, confidence, png, terminal } }
transitions[]: { id:"T<n>", from:"S<id>#<state>", event:"verb:target",
                 guard:"boolean over data_requires", effect, to:"S<id>#<state>|EXIT",
                 optimistic, idempotency, evidence, confidence }
rules[]: "WHEN <cond> THE SYSTEM SHALL <behaviour>"
assumptions[]: { id:"A<n>", statement, why, blocking:bool }

evidence ∈ observed|extracted|named|inferred|assumed  (downgrade when unsure;
  NEVER label invention as observed). effect ∈ none|navigate|mutate|open_overlay|
  close_overlay|replace. confidence ∈ high|med|low.

RULES
- Consider every state per screen: loading, empty, partial, ready, submitting,
  success, error.validation, error.network, error.permission, error.conflict,
  offline, disabled, readonly. Include the ones that apply; if one plausibly
  applies but has no design, include it evidence:"assumed" + a blocking assumption.
- Every error.* state needs a recovery transition. Every screen needs a
  back/cancel/dismiss unless deliberately one-way.
- Sibling transitions sharing from+event need mutually exclusive, exhaustive guards.
- Components must be real import paths, else "UNKNOWN" + an assumption.
- No pixel values, hex, or spacing (tokens.json owns those).
ITERATION: to change an existing spec, emit ONLY changed entities keyed by id.
"""


# ------------------------------------------------------------------ main --

def cmd_ingest(args):
    path = pathlib.Path(args.flow)
    incoming = json.loads(pathlib.Path(args.from_).read_text(encoding="utf-8"))
    if path.exists():
        text, base = read_block(path)
    else:
        text, base = _scaffold(), {}
    data = merge(base, incoming) if args.merge else incoming
    if args.bump:
        data.setdefault("flow", {})["version"] = \
            int(data.get("flow", {}).get("version", 0)) + 1
    text = _render_all(text, data)
    data["flow"]["spec_hash"] = spec_hash({k: v for k, v in data.items()
                                           if k != "flow" or True})
    text = write_block(path, text, data)
    text = re.sub(r"spec_hash:\s*\S+", f"spec_hash: {data['flow']['spec_hash']}",
                  text, count=1)
    path.write_text(text, encoding="utf-8")
    errs, warns = validate(data, path.parent)
    for w in warns:
        print(f"warn : {w}")
    for e in errs:
        print(f"ERROR: {e}")
    print(f"ingested {'(merge)' if args.merge else '(replace)'} -> {path} · "
          f"spec_hash {data['flow']['spec_hash']} · {len(errs)} error(s)")
    return 1 if errs else 0


def cmd_gen(args):
    path = pathlib.Path(args.flow)
    text, data = read_block(path)
    text = _render_all(text, data)
    path.write_text(text, encoding="utf-8")
    if args.all:
        (path.parent / "acceptance.feature").write_text(gherkin(data), "utf-8")
        (path.parent / "tasks.md").write_text(tasks_md(data), "utf-8")
        (path.parent / "machine.ts").write_text(xstate_ts(data), "utf-8")
        print("wrote acceptance.feature, tasks.md, machine.ts")
    print(f"regenerated views in {path}")
    return 0


def cmd_check(args):
    path = pathlib.Path(args.flow)
    text, data = read_block(path)
    errs, warns = validate(data, path.parent)
    for w in warns:
        print(f"warn : {w}")
    for e in errs:
        print(f"ERROR: {e}")
    if errs:
        return 1
    if _render_all(text, data) != text:
        print("ERROR: generated views are stale — run `gen`")
        return 1
    print(f"ok: {path} valid, {len(warns)} warning(s)")
    return 0


def cmd_repair(args):
    p = pathlib.Path(args.file)
    raw = p.read_text(encoding="utf-8")
    try:
        data = json.loads(raw) if p.suffix == ".json" else yaml.safe_load(raw)
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        line = getattr(exc, "lineno", None) or _yaml_line(exc)
        snippet = raw.splitlines()[line - 1] if line and line <= len(raw.splitlines()) else ""
        print(f"PARSE ERROR at line {line}: {snippet!r}\n"
              f"reason: {exc}\nFix only this line; resend the full document.")
        return 1
    errs, _ = validate(data or {}, p.parent)
    if not errs:
        print("OK")
        return 0
    print("VALIDATION ERRORS (fix these, resend the full document):")
    for e in errs:
        print(f"  - {e}")
    return 1


def _yaml_line(exc):
    mark = getattr(exc, "problem_mark", None)
    return (mark.line + 1) if mark else None


def _render_all(text, data):
    text = replace_marked(text, "mermaid", mermaid(data))
    text = replace_marked(text, "matrix", matrix(data))
    text = replace_marked(text, "assumptions", assumptions_md(data))
    return text


def _scaffold() -> str:
    return ("# Flow Spec\n\n## 1. Normative specification\n\n"
            "```yaml flowspec\n```\n\n## 2. Flow map\n\n"
            "<!-- GENERATED:mermaid — do not edit by hand -->\n"
            "<!-- /GENERATED:mermaid -->\n\n## 3. Screen × state matrix\n\n"
            "<!-- GENERATED:matrix — do not edit by hand -->\n"
            "<!-- /GENERATED:matrix -->\n\n## 4. Open questions\n\n"
            "<!-- GENERATED:assumptions — do not edit by hand -->\n"
            "<!-- /GENERATED:assumptions -->\n")


def main():
    ap = argparse.ArgumentParser(prog="flow_kit")
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("emit-schema")
    sub.add_parser("emit-prompt")

    p_ing = sub.add_parser("ingest")
    p_ing.add_argument("flow")
    p_ing.add_argument("--from", dest="from_", required=True)
    p_ing.add_argument("--merge", action="store_true")
    p_ing.add_argument("--bump", action="store_true")

    p_gen = sub.add_parser("gen")
    p_gen.add_argument("flow")
    p_gen.add_argument("--all", action="store_true")

    p_chk = sub.add_parser("check")
    p_chk.add_argument("flow")

    p_rep = sub.add_parser("repair")
    p_rep.add_argument("file")

    args = ap.parse_args()
    here = pathlib.Path(__file__).parent

    if args.cmd == "emit-schema":
        schema = here / "flowspec.schema.json"
        print(schema.read_text(encoding="utf-8") if schema.exists()
              else "{}  // flowspec.schema.json not found next to flow_kit.py")
        return 0
    if args.cmd == "emit-prompt":
        print(GRAMMAR_CARD)
        return 0
    if args.cmd == "ingest":
        return cmd_ingest(args)
    if args.cmd == "gen":
        return cmd_gen(args)
    if args.cmd == "check":
        return cmd_check(args)
    if args.cmd == "repair":
        return cmd_repair(args)


if __name__ == "__main__":
    raise SystemExit(main())
