# Flow Spec prompts

Three prompts, run in order, each in its **own session**. P2 must not share
context with P1 — a model that authored a document defends it.

---

## P1 — Draft the Flow Spec (multimodal)

> Attach, in this order: (1) this prompt, (2) `AGENTS.md`, (3) `docs/components.md`,
> (4) `tokens.json`, (5) the prototype-overview screenshot, (6) each screen
> screenshot **each preceded by a text line containing its exact filename**,
> (7) the CSS dumps, (8) any designer notes.

```
You are producing a Flow Spec: a single markdown file whose `yaml flowspec`
block is the normative source of truth for how one user flow behaves. A human
will review it, then a coding agent will implement from it. Precision matters
more than completeness; an honest gap is better than a confident invention.

OUTPUT
Return exactly two fenced artifacts and nothing else:
  1. flow.md — following the schema below, verbatim in structure
  2. open-questions.md — a numbered list of everything you could not determine

SCHEMA (obey exactly; no extra keys, no prose inside the YAML)
flow: id, version, status(draft), spec_hash(null), goal, actors[],
      entry_points[], success_criteria[], non_goals[]
screens[]: id (S<n>_<slug>), route, title,
      evidence{png, css, layer}, data_requires[], data_source,
      components[], a11y{focus_on_enter, live_region},
      responsive{breakpoints, notes}, states[]
screens[].states[]: id, entry_condition, copy, evidence, confidence,
      png, terminal(optional)
transitions[]: id (T<n>), from (S<id>#<state>), event (<verb>:<target>),
      guard (boolean expression), effect (none|navigate|mutate|
      open_overlay|close_overlay|replace), to (S<id>#<state> or EXIT),
      optimistic, idempotency, evidence, confidence
rules[]: EARS statements — "WHEN <condition> THE SYSTEM SHALL <behaviour>"
assumptions[]: id, statement, why, blocking(bool)

Leave these three markers in place, empty; they are machine-generated:
  <!-- GENERATED:mermaid — do not edit by hand --> <!-- /GENERATED:mermaid -->
  <!-- GENERATED:matrix — do not edit by hand --> <!-- /GENERATED:matrix -->
  <!-- GENERATED:assumptions — do not edit by hand --> <!-- /GENERATED:assumptions -->

EVIDENCE DISCIPLINE — the most important rule in this prompt
Every state and every transition carries `evidence` with exactly one value:
  observed  — directly visible in a screenshot I gave you
  extracted — present in the CSS I gave you
  named     — derived from a Figma layer or frame name
  inferred  — deduced from a strong convention, not from my inputs
  assumed   — you invented it; there is no evidence at all
Do not label anything `observed` unless you can point to the specific filename.
When in doubt, downgrade. An `assumed` item is not a failure — it is the
question the human needs to answer. Silently promoting an assumption to an
observation IS a failure.

STATE COVERAGE — mandatory
For every screen, consider each of: loading, empty, partial, ready,
submitting, success, error.validation, error.network, error.permission,
error.conflict, offline, disabled, readonly.
Include the ones that apply. For each one you exclude, you must be able to
justify it; if a state plausibly applies but has no design, INCLUDE it with
evidence: assumed and add a blocking assumption. Missing loading, empty and
error states is the single most common defect in generated frontends — do not
reproduce it here.

TRANSITION COMPLETENESS
- Every error state needs a documented recovery edge.
- Every screen reachable by navigation needs a back/cancel/dismiss edge unless
  the flow is deliberately one-way — say which.
- Sibling transitions sharing a `from` and `event` must have guards that are
  mutually exclusive and jointly exhaustive.
- Guards are boolean expressions over paths named in `data_requires`.
  Never write a guard in English.

COMPONENTS
Match every component against docs/components.md and cite the real import path.
If nothing matches, write `UNKNOWN` and add an open question. Never invent a
component name.

DO NOT
- Do not put pixel values, hex colours or spacing in the spec. Screenshots and
  tokens.json own those.
- Do not write prose inside the YAML block.
- Do not describe visual appearance; describe behaviour.
- Do not fill the generated sections.
```

---

## P2 — Adversarial critique (fresh session)

> Attach: the drafted `flow.md`, the same screenshots, `docs/components.md`.

```
You are reviewing a Flow Spec written by someone else. Your job is to find what
is wrong with it, not to improve its prose. Be specific and cite ids.

Run exactly these checks and report findings as a table of
[check, id, severity(blocker|major|minor), finding, suggested fix]:

1. REACHABILITY — list every state with no incoming transition (excluding the
   flow's initial state) and every state with no outgoing transition that is
   not marked terminal.
2. MISSING EXITS — list every screen with no back, cancel or dismiss path.
3. ERROR RECOVERY — for each state whose id starts with `error.`, confirm a
   recovery transition exists and that its guard is satisfiable.
4. GUARD COVERAGE — group transitions by (from, event). For each group with
   more than one member, state whether the guards are mutually exclusive and
   jointly exhaustive. Show the uncovered case if there is one.
5. EVIDENCE AUDIT — for every item tagged `observed`, name the screenshot
   filename that proves it. If you cannot, mark it for downgrade to `assumed`.
   This check matters more than the others; be strict.
6. DANGLING REFERENCES — every `evidence.png` path, every component import
   path, and every `to`/`from` address must resolve against the inputs.

Then list, separately, the three highest-risk ambiguities a coding agent would
most likely resolve differently from the author's intent, and say how each
should be worded to close the gap.

Do not rewrite the spec. Do not add features. Do not comment on wording style.
```

---

## P3 — Implement (coding agent)

> Point at: approved `flow.md`, `tokens.json`, `AGENTS.md`, `docs/components.md`,
> `screens/`, `acceptance.feature`.

```
Implement the approved Flow Spec at specs/flows/<id>/flow.md.

AUTHORITY ORDER when sources conflict:
  1. AGENTS.md (house rules)      2. flow.md (behaviour)
  3. tokens.json (values)          4. screens/*.png (pixels)
Never resolve a conflict silently — stop and ask.

WORK UNIT
One task = one screen and ALL of its declared states. Do not start the next
screen until the current one passes verification. Implement transitions as a
single navigation/state module derived from the transitions[] list, not as
logic scattered through components.

FOR EACH SCREEN
- Build every state in states[], including loading, empty and every error.*.
  A state with no implementation is a failed task, even if it "never happens".
- Use only components listed in that screen's components[]. If one is missing
  from the codebase, stop and ask; do not create a near-duplicate.
- Use only tokens from tokens.json. No literal hex, no literal px spacing.
  If a value has no token, stop and ask.
- Honour a11y.focus_on_enter and any live_region.
- Use the exact strings in each state's `copy`.

VERIFICATION — definition of done, in order
1. Every scenario in acceptance.feature passes. Drive assertions through the
   accessibility tree (roles, labels), not screenshots. Generate durable
   locators; never commit ephemeral snapshot refs.
2. Render each state at each breakpoint in responsive.breakpoints and run a
   deterministic pixel diff against the matching screens/*.png. Report the
   numeric diff ratio. Do not judge visual similarity yourself — read the diff
   image the differ produced and explain which token or layout rule caused it.
3. Confirm every state id in the spec is reachable in the running app and
   every guard branch is exercised by at least one scenario.

REPORT at the end of each task: states implemented, scenarios passing, diff
ratio per screenshot, and any spec statement you could not satisfy.
```

---

## Notes on why these are shaped this way

- **Schema before content.** Locking the output format up front is the most
  consistently reported lever in diagram- and spec-generation prompting.
- **Filenames as text lines before each image.** Without them the model cannot
  reliably bind image *n* to node id *n*, and the ids are the entire contract.
- **Evidence tags.** Fluent prose makes transcription and invention look
  identical. Tagging separates them so review is 15 items, not 15 pages.
- **Fresh session for P2.** Self-review inside the authoring context tends to
  ratify rather than challenge.
- **"Stop and ask" rather than "use your judgement".** The documented failure
  mode is confident silent divergence, not refusal.
