---
name: figma
description: Read Figma designs with the `figma` CLI — layout and typography specs, design token and style names, rendered screenshots, icon export, published components. Use whenever the user pastes a figma.com link or asks to implement, match, inspect, or export a design, screen, component, or mockup, even if they never say "Figma". Fast and free; call it freely.
---

# Figma

`figma <command> <url>` — run bare for the command list. Reads are a single HTTPS
request against Figma's REST API, cached for two hours. Cheap. Do not ration them.

Links come from browser Figma and must contain `?node-id=` — right-click the layer →
Copy link to selection. Without a node id, ask the user for a better link.

## Implementing a design

1. `figma tree "<url>"` — structure at depth 2. Orient yourself, pick the real target node.
2. `figma spec "<url>"` — the workhorse. One line per node: size, flex direction, gap,
   padding, alignment, font stack, colors, radius, shadows, and **style names**
   (`fill=surface/card`) where the design uses published styles. Read those names as your
   token names and map them to this repo's theme. Never hard-code a hex that has a style name.
3. `figma png "<url>" -o ref.png` when layout fidelity matters. Build, then compare.
4. `figma assets "<url>" ./src/assets` for icons — SVG by default. Never hand-draw one.
5. `figma comps "<url>"` / `figma styles "<url>"` for the published library inventory.

`vars:fills` in the output means that property is bound to a Figma variable. Resolve values
with `figma vars "<url>"` — instant on Enterprise, otherwise it falls back to Codex.

## Two slow commands

`figma vars` (non-Enterprise) and `figma ctx` shell out to Codex, which spins up an agent and
costs model tokens and several seconds. Use `ctx` only when `spec` genuinely isn't enough —
an unusual layout you can't infer. For ordinary work, `spec` plus your own judgment produces
better code anyway, because you know this codebase and Codex doesn't.

## Depth control

`--depth` defaults to 4. Deep component trees explode; start shallow and re-run deeper on the
one subtree you care about rather than pulling a whole page at depth 10.
