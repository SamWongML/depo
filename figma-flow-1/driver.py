#!/usr/bin/env python3
"""
driver.py — REFERENCE ONLY (not executed here; needs an API key + network).

Shows the request shape that makes the harness token-lean, wiring together the
four moves from the report:

  1. images once        — screenshots only on the draft turn
  2. cache the prefix    — cache_control on the stable blocks, volatile last
  3. structured output   — constrain generation to flowspec.schema.json
  4. id-keyed edits      — the edit turn sends the current YAML, gets a PARTIAL

Then hand the model's JSON to flow_kit:
    python flow_kit.py ingest flow.md --from out.json            # draft
    python flow_kit.py ingest flow.md --from out.json --merge     # edit

Docs: prompt caching  https://platform.claude.com/docs/en/build-with-claude/prompt-caching
"""

import base64
import json
import pathlib

import anthropic  # pip install anthropic

MODEL = "claude-sonnet-4-6"   # cache entries are model-specific; pin it
client = anthropic.Anthropic()

HERE = pathlib.Path(__file__).parent
SCHEMA = json.loads((HERE / "flowspec.schema.json").read_text())
GRAMMAR = (HERE / ".flowkit" / "grammar-card.md").read_text()   # from emit-prompt


def _img_block(path: str) -> dict:
    """A downscaled contact sheet. Keep the long edge <= ~1568px before sending;
    resize upstream so you are not paying for retina density."""
    data = base64.standard_b64encode(pathlib.Path(path).read_bytes()).decode()
    return {"type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": data}}


def _stable_prefix(component_index: str, tokens_json: str) -> list[dict]:
    """Blocks 1-2 of the request. Byte-identical across turns and marked
    cacheable, so every turn after the first reads them at ~10% of input price.
    The LAST block in this list carries the cache breakpoint; everything up to
    it is cached."""
    return [
        {"type": "text", "text": GRAMMAR},                       # schema/grammar card
        {"type": "text", "text": f"# Component index\n{component_index}"},
        {"type": "text",
         "text": f"# Design tokens (DTCG)\n{tokens_json}",
         "cache_control": {"type": "ephemeral"}},                # <-- breakpoint
    ]


# JSON Schema handed to the provider's structured-output mode. Some strict modes
# ignore `pattern`; flow_kit re-enforces id/address patterns regardless.
OUTPUT_FORMAT = {"type": "json_schema", "schema": SCHEMA}


def draft_turn(component_index, tokens_json, contact_sheets: list[str],
               brief: str) -> dict:
    """Turn 1. Images are attached HERE and only here. Returns schema-valid JSON."""
    content = _stable_prefix(component_index, tokens_json)
    for png in contact_sheets:                                   # volatile: images
        content.append({"type": "text", "text": f"Screen file: {pathlib.Path(png).name}"})
        content.append(_img_block(png))
    content.append({"type": "text", "text": f"Task: {brief}"})   # volatile: brief

    resp = client.messages.create(
        model=MODEL,
        max_tokens=8000,
        system="Emit one JSON object matching the schema. No prose.",
        messages=[{"role": "user", "content": content}],
        output_format=OUTPUT_FORMAT,          # constrained decoding -> valid first try
    )
    _report_usage("draft", resp)
    return json.loads(resp.content[0].text)


def edit_turn(component_index, tokens_json, current_yaml: str,
              change_request: str) -> dict:
    """Turn N. NO images. Send the current YAML (cheap to read, cached) and ask
    for a PARTIAL: only the entities that change, keyed by id. flow_kit upserts."""
    content = _stable_prefix(component_index, tokens_json)
    content.append({"type": "text",
                    "text": f"# Current spec (YAML)\n{current_yaml}",
                    "cache_control": {"type": "ephemeral"}})     # cache the spec too
    content.append({"type": "text",
                    "text": ("Emit ONLY the entities that change, keyed by id "
                             "(screens[]/transitions[]/assumptions[]/rules[]). "
                             "Do not resend unchanged entities.\n\n"
                             f"Change: {change_request}")})

    resp = client.messages.create(
        model=MODEL,
        max_tokens=3000,
        system="Emit one JSON object (a partial) matching the schema. No prose.",
        messages=[{"role": "user", "content": content}],
        output_format=OUTPUT_FORMAT,
    )
    _report_usage("edit", resp)
    return json.loads(resp.content[0].text)


def _report_usage(label: str, resp) -> None:
    u = resp.usage
    # cache_read_input_tokens billed at ~10%; creation at ~1.25x; and cache
    # reads don't count against ITPM rate limits.
    print(f"[{label}] input={u.input_tokens} "
          f"cache_read={getattr(u, 'cache_read_input_tokens', 0)} "
          f"cache_write={getattr(u, 'cache_creation_input_tokens', 0)} "
          f"output={u.output_tokens}")


if __name__ == "__main__":
    print(__doc__)
    print("Reference only — import the functions, or adapt to your SDK.")
