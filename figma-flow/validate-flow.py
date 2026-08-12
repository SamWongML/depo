#!/usr/bin/env python3
"""
validate_flow.py — Flow Spec validator and projector.

The `yaml flowspec` fenced block inside flow.md is the single normative source.
This script validates it and regenerates every derived view in place:
  * a Mermaid stateDiagram-v2 flow map
  * a screen x state evidence matrix
  * the open-questions list
and can emit Gherkin acceptance skeletons.

Usage:
    python validate_flow.py path/to/flow.md              # validate + regenerate
    python validate_flow.py path/to/flow.md --check      # CI: fail on drift
    python validate_flow.py path/to/flow.md --gherkin    # write acceptance.feature

Dependencies: PyYAML.
"""

from __future__ import annotations

import argparse
import hashlib
import pathlib
import re
import sys

import yaml

BLOCK_RE = re.compile(r"```yaml flowspec\n(.*?)```", re.DOTALL)
CANON_STATE_ORDER = [
    "loading", "empty", "partial", "ready", "form", "entry", "submitting",
    "processing", "success", "error.validation", "error.declined",
    "error.network", "error.permission", "error.conflict", "offline",
    "disabled", "readonly",
]
STRONG_EVIDENCE = {"observed", "extracted", "named"}
TERMINALS = {"EXIT", "ENTRY"}


# ----------------------------------------------------------------- parsing --

def load(path: pathlib.Path):
    text = path.read_text(encoding="utf-8")
    match = BLOCK_RE.search(text)
    if not match:
        sys.exit(f"error: no ```yaml flowspec block found in {path}")
    raw = match.group(1)
    try:
        data = yaml.safe_load(raw)
    except yaml.YAMLError as exc:
        sys.exit(f"error: flowspec block is not valid YAML\n{exc}")
    return text, raw, data


def sid(screen: str, state: str) -> str:
    """Mermaid-safe node id."""
    return f"{screen}__{state}".replace(".", "_").replace("-", "_")


def split_addr(addr: str):
    if addr in TERMINALS:
        return addr, None
    if "#" not in addr:
        return addr, None
    screen, state = addr.split("#", 1)
    return screen, state


# -------------------------------------------------------------- validation --

def validate(data, base: pathlib.Path):
    errors: list[str] = []
    warnings: list[str] = []

    flow = data.get("flow") or {}
    screens = data.get("screens") or []
    transitions = data.get("transitions") or []
    assumptions = data.get("assumptions") or []

    for field in ("id", "version", "status", "goal"):
        if not flow.get(field):
            errors.append(f"flow.{field} is required")

    index: dict[str, dict] = {}
    for scr in screens:
        s_id = scr.get("id")
        if not s_id:
            errors.append("a screen is missing an id")
            continue
        if s_id in index:
            errors.append(f"duplicate screen id: {s_id}")
        states = {st["id"]: st for st in (scr.get("states") or []) if st.get("id")}
        if not states:
            errors.append(f"{s_id}: no states declared")
        index[s_id] = {"screen": scr, "states": states}
        for st_id, st in states.items():
            ev = st.get("evidence")
            if ev not in {"observed", "extracted", "named", "inferred", "assumed"}:
                errors.append(f"{s_id}#{st_id}: evidence must be one of "
                              "observed|extracted|named|inferred|assumed")
        png = (scr.get("evidence") or {}).get("png")
        if png and not (base / png).exists():
            warnings.append(f"{s_id}: evidence file not found on disk: {png}")

    seen_t: set[str] = set()
    outgoing: dict[str, list[dict]] = {}
    incoming: set[str] = set()

    for tr in transitions:
        t_id = tr.get("id", "<no id>")
        if t_id in seen_t:
            errors.append(f"duplicate transition id: {t_id}")
        seen_t.add(t_id)
        for field in ("from", "to", "event"):
            if not tr.get(field):
                errors.append(f"{t_id}: missing {field}")
        if "guard" not in tr:
            warnings.append(f"{t_id}: no guard — assumed unconditional")

        for role in ("from", "to"):
            addr = tr.get(role)
            if not addr:
                continue
            screen, state = split_addr(addr)
            if screen in TERMINALS:
                continue
            if screen not in index:
                errors.append(f"{t_id}: {role} references unknown screen '{screen}'")
            elif state is None:
                errors.append(f"{t_id}: {role}='{addr}' must be SCREEN#state")
            elif state not in index[screen]["states"]:
                errors.append(f"{t_id}: {role} references unknown state "
                              f"'{state}' on {screen}")

        outgoing.setdefault(tr.get("from", ""), []).append(tr)
        if tr.get("to") not in TERMINALS:
            incoming.add(tr.get("to", ""))

        if flow.get("status") == "approved" and tr.get("evidence") == "assumed":
            errors.append(f"{t_id}: an approved spec may not contain an "
                          "'assumed' transition")

    # guard exhaustiveness: siblings sharing from+event
    groups: dict[tuple, list[dict]] = {}
    for tr in transitions:
        groups.setdefault((tr.get("from"), tr.get("event")), []).append(tr)
    for (frm, evt), group in groups.items():
        if len(group) < 2:
            continue
        guards = [str(t.get("guard", "true")).strip() for t in group]
        if "true" in guards:
            continue
        negations = {g.lstrip("!").strip() for g in guards}
        complementary = any(f"!{g}" in guards for g in guards)
        equality_pair = len(negations) == 1
        lhs = {re.split(r"[=!<>]", g, maxsplit=1)[0].strip() for g in guards}
        same_subject = len(lhs) == 1 and all(
            re.search(r"[=!<>]", g) for g in guards)
        if not (complementary or equality_pair or same_subject):
            warnings.append(
                f"guard gap risk on {frm} + {evt}: {guards} — confirm the "
                "branches are exhaustive and mutually exclusive")

    # reachability
    first_screen = screens[0]["id"] if screens else None
    for s_id, meta in index.items():
        for st_id in meta["states"]:
            addr = f"{s_id}#{st_id}"
            is_initial = (s_id == first_screen and
                          st_id == next(iter(meta["states"])))
            if addr not in incoming and not is_initial:
                warnings.append(f"unreachable state: {addr}")
            terminal = meta["states"][st_id].get("terminal", False)
            if not outgoing.get(addr) and not terminal:
                warnings.append(f"dead end (no outgoing transition): {addr}")

    for a in assumptions:
        if a.get("blocking") and flow.get("status") == "approved":
            errors.append(f"{a.get('id')}: blocking assumption unresolved but "
                          "status is 'approved'")

    return errors, warnings


# -------------------------------------------------------------- projection --

def mermaid(data) -> str:
    screens = data.get("screens") or []
    transitions = data.get("transitions") or []
    lines = ["```mermaid", "stateDiagram-v2", "    direction LR"]

    if screens:
        lines.append(f"    [*] --> {screens[0]['id']}")

    for scr in screens:
        s_id = scr["id"]
        title = scr.get("title", s_id)
        states = [st["id"] for st in (scr.get("states") or [])]
        lines.append(f'    state "{s_id} · {title}" as {s_id} {{')
        if states:
            lines.append(f"        [*] --> {sid(s_id, states[0])}")
        for st in states:
            lines.append(f'        state "{st}" as {sid(s_id, st)}')
        for tr in transitions:
            f_scr, f_st = split_addr(tr.get("from", ""))
            t_scr, t_st = split_addr(tr.get("to", ""))
            if f_scr == s_id and t_scr == s_id:
                lines.append(f"        {sid(f_scr, f_st)} --> {sid(t_scr, t_st)}"
                             f" : {label(tr)}")
        lines.append("    }")

    for tr in transitions:
        f_scr, f_st = split_addr(tr.get("from", ""))
        t_scr, t_st = split_addr(tr.get("to", ""))
        if f_scr == t_scr:
            continue
        src = sid(f_scr, f_st) if f_st else f_scr
        dst = "[*]" if t_scr in TERMINALS else (sid(t_scr, t_st) if t_st else t_scr)
        lines.append(f"    {src} --> {dst} : {label(tr)}")

    lines.append("```")
    return "\n".join(lines)


def label(tr) -> str:
    guard = str(tr.get("guard", "true")).strip()
    text = f"{tr.get('id')} {tr.get('event')}"
    if guard and guard != "true":
        text += f" [{guard}]"
    return text.replace(":", " ")


def matrix(data) -> str:
    screens = data.get("screens") or []
    present: list[str] = []
    for scr in screens:
        for st in scr.get("states") or []:
            if st["id"] not in present:
                present.append(st["id"])
    present.sort(key=lambda s: (CANON_STATE_ORDER.index(s)
                                if s in CANON_STATE_ORDER else 99, s))

    head = "| Screen | " + " | ".join(present) + " |"
    rule = "|---" * (len(present) + 1) + "|"
    rows = [head, rule]
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
    rows.append("")
    rows.append("✅ evidence in the design · ⚠ inferred or invented — confirm "
                "with the designer · — not applicable")
    return "\n".join(rows)


def assumptions_md(data) -> str:
    items = data.get("assumptions") or []
    if not items:
        return "_No open assumptions._"
    blocking = [a for a in items if a.get("blocking")]
    other = [a for a in items if not a.get("blocking")]
    out = []
    if blocking:
        out.append("**Blocking — must be resolved before approval**\n")
        for a in blocking:
            out.append(f"- **{a.get('id')}** {a.get('statement')}  \n"
                       f"  _why:_ {a.get('why')}")
        out.append("")
    if other:
        out.append("**Non-blocking**\n")
        for a in other:
            out.append(f"- **{a.get('id')}** {a.get('statement')}  \n"
                       f"  _why:_ {a.get('why')}")
    return "\n".join(out)


def gherkin(data) -> str:
    flow = data.get("flow") or {}
    lines = [f"Feature: {flow.get('id')} — {flow.get('goal', '').strip()}", ""]
    for tr in data.get("transitions") or []:
        f_scr, f_st = split_addr(tr.get("from", ""))
        t_scr, t_st = split_addr(tr.get("to", ""))
        guard = str(tr.get("guard", "true")).strip()
        lines.append(f"  Scenario: {tr.get('id')} {tr.get('event')}")
        lines.append(f'    Given the "{f_scr}" screen is in state "{f_st}"')
        if guard and guard != "true":
            lines.append(f"    And the condition {guard} holds")
        lines.append(f"    When the event {tr.get('event')} occurs")
        if t_scr in TERMINALS:
            lines.append("    Then the user leaves the flow")
        else:
            lines.append(f'    Then the "{t_scr}" screen is shown '
                         f'in state "{t_st}"')
        if tr.get("effect") == "navigate":
            lines.append("    And the browser URL matches that screen's route")
        lines.append("")
    return "\n".join(lines)


def replace_block(text: str, name: str, body: str) -> str:
    start = f"<!-- GENERATED:{name} — do not edit by hand -->"
    end = f"<!-- /GENERATED:{name} -->"
    pattern = re.compile(re.escape(start) + r".*?" + re.escape(end), re.DOTALL)
    if not pattern.search(text):
        return text
    return pattern.sub(f"{start}\n{body}\n{end}", text)


# -------------------------------------------------------------------- main --

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--check", action="store_true",
                    help="CI mode: fail if regeneration would change the file")
    ap.add_argument("--gherkin", action="store_true",
                    help="write acceptance.feature next to the spec")
    args = ap.parse_args()

    path = pathlib.Path(args.path)
    base = path.parent
    text, raw, data = load(path)

    errors, warnings = validate(data, base)
    for w in warnings:
        print(f"warn : {w}")
    for e in errors:
        print(f"ERROR: {e}")

    canonical = re.sub(r"^\s*spec_hash:.*$", "", raw, flags=re.M)
    digest = hashlib.sha256(canonical.encode()).hexdigest()[:8]
    updated = text
    updated = replace_block(updated, "mermaid", mermaid(data))
    updated = replace_block(updated, "matrix", matrix(data))
    updated = replace_block(updated, "assumptions", assumptions_md(data))
    updated = re.sub(r"spec_hash:\s*\S+", f"spec_hash: {digest}", updated, count=1)

    if args.check:
        if errors:
            return 1
        if updated != text:
            print("ERROR: generated sections are stale — run without --check")
            return 1
        print(f"ok: {path} valid, spec_hash {digest}, {len(warnings)} warning(s)")
        return 0

    if updated != text:
        path.write_text(updated, encoding="utf-8")
        print(f"regenerated: {path}")

    if args.gherkin:
        out = base / "acceptance.feature"
        out.write_text(gherkin(data), encoding="utf-8")
        print(f"wrote: {out}")

    print(f"spec_hash: {digest} · {len(errors)} error(s), "
          f"{len(warnings)} warning(s)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
