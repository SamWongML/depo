# Harnessing Matt Pocock's Skills in Pi

### A design study of small, high-leverage Pi extensions that turn prompt-layer discipline into harness-layer mechanism

**Research date:** 7 August 2026 · **Pi:** `@earendil-works/pi-coding-agent@0.84.0` (published 6 Aug 2026) · **Skills:** `mattpocock-skills@1.2.3`, repo HEAD 6 Aug 2026

---

## 0. The thesis, in one sentence

> **A Matt Pocock skill states an invariant in English. A Pi extension can make that invariant structurally impossible to violate. The highest-leverage extensions are the ones that take a single load-bearing sentence out of a `SKILL.md` and move it from the prompt layer to the harness layer.**

Everything below follows from that. The skills are excellent *descriptions* of engineering discipline. They are enforced by nothing. Pi is the only mainstream harness where you can enforce them, because Pi extensions run **in-process with the harness API in hand** rather than as out-of-process observers.

Three findings frame the whole report:

| # | Finding | Consequence |
|---|---|---|
| **F1** | Six of Matt's 25 promoted skills structurally depend on **sub-agents**, which Pi deliberately does not ship. | Without a sub-agent layer, `grilling`, `research`, `code-review`, `wayfinder`, `improve-codebase-architecture` and `codebase-design` silently degrade to single-context approximations. This is the #1 blocker. |
| **F2** | The skills chain to each other through **prose invocation** (`Run a /grilling session`). Prose is not dispatch. On Pi the real name is `/skill:grilling`, and nothing guarantees the model actually reads the file. | The composition backbone of the set is a suggestion. One small router extension repairs it. |
| **F3** | Pi's `input` event exposes `event.source` as `"interactive" \| "rpc" \| "extension"`. **The harness can tell a real human from the agent talking to itself.** | This is the mechanism that fixes the most damaging reported failure — [`#785 Wayfinder on Pi never asked me a single question`](https://github.com/mattpocock/skills/issues/785) — and it has no equivalent in Claude Code. |

---

# Part 1 — Ground truth

## 1.1 The skill set, as it actually ships

`mattpocock/skills` at HEAD contains **35 `SKILL.md` files**. Only **25 are "promoted"** — listed in `.claude-plugin/plugin.json` and shipped in the Claude Code plugin. The remaining 10 live in `in-progress/` and `misc/` and are reachable only through the `skills.sh` installer.

The set splits on exactly one axis, documented in `.agents/invocation.md`: **who may invoke it.**

- **User-invoked** — `disable-model-invocation: true` (Claude Code) + `policy.allow_implicit_invocation: false` (Codex). Reachable *only* by a human typing the name. Their job is to **orchestrate**.
- **Model-invoked** — the default. Reachable by human *or* model. Their job is to hold **reusable discipline**.
- The hard rule: *a user-invoked skill may invoke model-invoked skills, but never another user-invoked one.*

### Promoted skills — Engineering (18)

| Skill | Invocation | Size | What it does | Depends on |
|---|---|---|---|---|
| `ask-matt` | 👤 user | 90L | Router over the whole set; documents the main flow, on-ramps, phase boundaries | — |
| `grill-with-docs` | 👤 user | **7L** | Grilling session that also builds the domain model | `grilling`, `domain-modeling` |
| `wayfinder` | 👤 user | 128L | Chart a huge effort as a map of **decision tickets** on the tracker; resolve one per session | `grilling`, `domain-modeling`, `research`, `prototype`, tracker |
| `to-spec` | 👤 user | 75L | Synthesise the conversation into a spec, publish to tracker (no interview) | tracker, `codebase-design` |
| `to-tickets` | 👤 user | 105L | Break a plan into tracer-bullet vertical slices with blocking edges | tracker |
| `implement` | 👤 user | **15L** | Build the work; drive `/tdd` at agreed seams; close with `/code-review`; commit | `tdd`, `code-review` |
| `triage` | 👤 user | 112L | Move incoming issues through a five-role state machine | tracker, labels |
| `improve-codebase-architecture` | 👤 user | 71L | Scan for deepening opportunities → HTML report → grill the chosen one | `grilling`, `codebase-design`, sub-agents |
| `setup-matt-pocock-skills` | 👤 user | 116L | Run-once per repo: tracker, labels, domain-doc layout → `docs/agents/*.md` | — |
| `tdd` | 🤖 model | 38L | Red→green loop; seams; anti-patterns; "test only at pre-agreed seams" | `codebase-design` |
| `code-review` | 🤖 model | 87L | **Two parallel sub-agents**: Standards (+ Fowler smell baseline) and Spec | sub-agents, tracker |
| `diagnosing-bugs` | 🤖 model | 140L | 6-phase loop; Phase 1 is "build a tight red-capable feedback loop" and is gated | `improve-codebase-architecture` |
| `domain-modeling` | 🤖 model | 74L | Actively sharpen the ubiquitous language; write `CONTEXT.md` + ADRs inline | — |
| `codebase-design` | 🤖 model | 114L | Deep-module vocabulary: module, interface, depth, seam, adapter, leverage, locality | sub-agents |
| `research` | 🤖 model | **12L** | Spin up a **background agent** to investigate primary sources → cited markdown | sub-agents |
| `prototype` | 🤖 model | 26L | Throwaway code answering one design question; kept on a `prototype/<name>` branch | — |
| `resolving-merge-conflicts` | 🤖 model | 14L | Resolve hunk-by-hunk by traced intent; never `--abort` | — |
| `wizard` | 🤖 model | 44L | Generate an interactive bash script for steps only a human can take | `template.sh` |

### Promoted skills — Productivity (7)

| Skill | Invocation | Size | What it does |
|---|---|---|---|
| `grill-me` | 👤 user | **7L** | Stateless relentless interview |
| `handoff` | 👤 user | 16L | Compact the conversation into a portable handoff doc in the OS temp dir |
| `teach` | 👤 user | 140L | Multi-session stateful teaching workspace |
| `to-questionnaire` | 👤 user | 53L | Interviews you about the **send**, not the subject; produces a doc for someone else |
| `wait-what` | 👤 user | **7L** | One-word corrective: re-pitch that in ASD-STE100 using `CONTEXT.md` vocabulary |
| `grilling` | 🤖 model | 22L | **The primitive.** Design tree, frontier, rounds, numbered questions with recommendations |
| `writing-for-agents` | 🤖 model | 81L | Reference for writing documents agents consume |

> **Note the sizes.** `grill-me` is 7 lines. `grill-with-docs` is 7 lines. `implement` is 15 lines. `research` is 12 lines. These are not thin because they're unfinished — they are thin because they are **composition statements**: "Run a `/grilling` session, using the `/domain-modeling` skill." The entire value is in whether that composition actually happens. On Pi, by default, **it doesn't reliably**. See §2.2.

### Non-promoted (10)

`in-progress/`: `loop-me`, `claude-handoff`, `setup-ts-deep-modules`, `writing-beats`, `writing-fragments`, `writing-shape`
`misc/`: `git-guardrails-claude-code`, `setup-pre-commit`, `migrate-to-shoehorn`, `scaffold-exercises`

A Pi package must **exclude these deliberately** — Pi's convention scanner would otherwise expose drafts and personal skills as first-class. (This is exactly the problem statement in closed issue [#623](https://github.com/mattpocock/skills/issues/623).)

---

## 1.2 The two canonical chains

`ask-matt` defines one **main flow** with two **on-ramps**. The user asked about both endpoints of the interesting pair. Here they are, exactly as the repo describes them.

### Chain A — the main flow: `grill-with-docs → … → implement`

```
                     ┌──────────────── ONE UNBROKEN CONTEXT WINDOW ────────────────┐
                     │        (do not /compact or /clear until after step 3)       │
                     │                 limit: the "smart zone" ~150k               │
                     │                                                             │
   loose idea ──────►│  1. /grill-with-docs                                        │
                     │        └─► /grilling  (design tree · frontier · rounds)     │
                     │        └─► /domain-modeling → CONTEXT.md + docs/adr/        │
                     │                    │                                        │
                     │        ┌───────────┴─── ungrillable question?               │
                     │        │   /handoff ─► /prototype ─► /handoff back          │
                     │        └───────────┬───  (own directory, own session)       │
                     │                    │                                        │
                     │  2. multi-session build?                                    │
                     │        ├─ NO  ─────────────────────────────┐                │
                     │        └─ YES                              │                │
                     │  3.     /to-spec   → spec on tracker       │                │
                     │         /to-tickets → tracer-bullet tickets│                │
                     └───────────────┬───────────────────────────┬┘
                                     │  /clear between each      │
                                     ▼                           ▼
                              4. /implement (per ticket, fresh context)
                                     ├─► /tdd        red → green, one vertical slice
                                     └─► /code-review  ┌─ Standards sub-agent ─┐
                                                       └─ Spec sub-agent ──────┘
                                     ▼
                                  commit
```

### Chain B — the wayfinder on-ramp: `wayfinder → … → implement`

```
  huge foggy effort
  (greenfield / multi-session)
        │
        ▼
  ╔═══════════════════════ SESSION 1 — CHART ═══════════════════════╗
  ║ 1 name the DESTINATION      (/grilling + /domain-modeling)      ║
  ║ 2 map the frontier          (/grilling, breadth-first)          ║
  ║   └─ no fog surfaced? → you don't need a map. STOP.             ║
  ║ 3 create MAP issue          label wayfinder:map                 ║
  ║ 4 create tickets, THEN wire blocking edges (2nd pass)           ║
  ║ 5 fire /research sub-agents in parallel → research/<name>       ║
  ║ 6 STOP. Charting resolves nothing.                              ║
  ╚═════════════════════════════════════════════════════════════════╝
        │
        ▼   ◄──────────────────────────────────────────┐
  ╔═══════════ SESSION N — WORK ONE TICKET ═══════════╗ │
  ║ 1 load MAP (low-res: Decisions-so-far index)      ║ │
  ║ 2 pick first FRONTIER ticket · CLAIM IT FIRST     ║ │  repeat until
  ║ 3 resolve by TYPE:                                ║ │  frontier empty
  ║     research  🤖 AFK  → /research sub-agent       ║ │
  ║     prototype 👥 HITL → /prototype                ║ │
  ║     grilling  👥 HITL → /grilling+/domain-modeling║ │
  ║     task      👥/🤖   → manual unblocking work    ║ │
  ║ 4 resolve: comment + close + append to map index  ║ │
  ║ 5 graduate fog → new tickets; scope-out strays    ║ │
  ║ ⛔ NEVER resolve >1 ticket per session (except    ║ │
  ║    research)                                      ║ │
  ╚═══════════════════════════════════════════════════╝ │
        │                                               │
        └───────────────────────────────────────────────┘
        │
        ▼  map clear → HAND OFF, DO NOT BUILD
     /to-spec  ──► /to-tickets ──► /implement ──► /tdd ──► /code-review
     (collapses the map's linked decisions into a buildable plan)
```

> `ask-matt` is emphatic about the last step: *"Looping the map straight into `/implement` skips that collapse and throws the linked detail away."* An extension that lets you jump the gap would be actively harmful. **Any harness we build must respect the collapse.**

---

## 1.3 Pi 0.84.0 — what it is, and what it deliberately isn't

Pi is a **minimal agent harness** from Mario Zechner (badlogic), now stewarded by Earendil Inc., MIT-licensed. Its positioning is a product strategy in one line: *"There are many agent harnesses, but this one is yours."*

**First-party packages, all at `0.84.0`:**

| Package | Layer |
|---|---|
| `@earendil-works/pi-ai` | Unified multi-provider LLM API, model discovery, OAuth |
| `@earendil-works/pi-agent-core` | Agent runtime: loop, tool calling, state |
| `@earendil-works/pi-coding-agent` | **The CLI + tools + sessions + compaction + extension host + SDK** |
| `@earendil-works/pi-tui` | Terminal UI with differential rendering |
| `@earendil-works/pi-protocol` · `pi-client` · `pi-server` | Transport-neutral CBOR protocol for remote sessions |
| `@earendil-works/pi-telemetry` | Vendor-neutral telemetry contracts |

**Built-in tools (7):** `read`, `write`, `edit`, `bash`, `grep`, `find`, `ls`. That is the whole list.

**Deliberately omitted — and this is the crux:**

| Omitted | Pi's answer | Impact on Matt's skills |
|---|---|---|
| **Sub-agents** | "build one, or install a package" | 🔴 **Critical** — 6 skills depend on it |
| Plan mode | "write plans to files" | 🟡 `wayfinder`/`to-spec` *are* plan mode; no conflict |
| MCP | "build CLI tools with READMEs, or install `pi-mcp-adapter`" | 🟢 Skills use `gh`/`glab` CLI — no MCP needed |
| Built-in todos | extension territory | 🟡 relevant to `to-tickets`/`implement` sequencing |
| Permission system | "containerize or sandbox Pi" | 🟡 `git-guardrails` skill has no teeth on Pi |
| Background bash | extension territory | 🔴 `research` says "background agent" |

**What Pi has that Claude Code does not** — and that matters enormously here:

| Capability | Why it matters for these skills |
|---|---|
| `.agents/skills/` **native discovery** (global `~/.agents/skills/`, project `.agents/skills/` + ancestors) | Matt's repo tagline is literally *"Straight from my .agents directory."* Installation is nearly free. |
| `disable-model-invocation` **honoured** | The user-invoked/model-invoked axis survives the port intact. |
| `session_before_compact` hookable | You can **replace** compaction, not just configure it. OpenClaw does exactly this. |
| Session **tree** with `/fork`, `/clone`, `/tree`, labels | A decision log can become navigable history. |
| `ctx.newSession({setup, withSession})` | `/clear between each ticket` becomes an API call. |
| `input` event with `event.source` | **Distinguish human from agent.** No Claude Code equivalent. |
| In-process `ExtensionAPI` | Register tools, commands, providers, renderers, editors, flags. |

---

## 1.4 The Pi extension surface — six levers

Everything in Part 4 is built from these. This is the complete set of mechanisms an extension can pull.

| Lever | API | What it buys you |
|---|---|---|
| **Intercept** | `pi.on("tool_call")` → `{block, reason, terminate}`; `event.input` is **mutable** | Refuse or rewrite any tool call. The enforcement primitive. |
| **Register** | `pi.registerTool` (+ `promptSnippet`, `promptGuidelines`, `prepareArguments`, `renderCall/renderResult`) | Give the model a real verb with a schema, streaming, and custom rendering. Can **override built-ins** by name. |
| **Persist** | `pi.appendEntry(customType, data)` + `pi.registerEntryRenderer` + `pi.setLabel` | Durable state that **does not enter LLM context** but survives `/reload`, compaction and session restore. |
| **Shape context** | `before_agent_start` (inject message, **chain-modify system prompt**), `context` (rewrite message list), `session_before_compact` (**replace** the summary) | Own the context window. |
| **Drive UI** | `ctx.ui.setWidget / setStatus / setFooter / notify / confirm / select / input / custom() / addAutocompleteProvider / setWorkingMessage` | Show state that today lives only in scrollback. |
| **Control session** | `ctx.newSession`, `ctx.fork`, `ctx.switchSession`, `ctx.navigateTree`, `ctx.compact`, `ctx.reload`, `pi.sendUserMessage` | Turn phase boundaries into one keystroke. |

Plus the lifecycle: `project_trust → session_start → resources_discover → [input → before_agent_start → agent_start → (turn_start → context → tool_call → tool_result → turn_end)* → agent_end → agent_settled]* → session_shutdown`.

---

## 1.5 The official package landscape

There are two distinct things people mean by "the official list of Pi extension packages."

**(a) First-party** — the seven `@earendil-works/*` packages in §1.3. There are **no first-party extension packages**; Earendil ships the harness and nothing on top of it. What *is* shipped first-party is the **bundled example corpus** at `packages/coding-agent/examples/extensions/` (~75 examples), which functions as the canonical reference implementation set:

| Category | Examples that matter for this project |
|---|---|
| Tools | `question.ts`, `questionnaire.ts`, `todo.ts`, `dynamic-tools.ts`, `structured-output.ts`, `truncated-tool.ts`, `tool-override.ts` |
| Commands | `handoff.ts`, `summarize.ts`, `qna.ts`, `send-user-message.ts`, `reload-runtime.ts` |
| Gates | **`permission-gate.ts`**, `protected-paths.ts`, `confirm-destructive.ts`, `dirty-repo-guard.ts`, `input-transform.ts` |
| Context | **`custom-compaction.ts`**, `trigger-compact.ts`, `claude-rules.ts`, `prompt-customizer.ts` |
| UI | `status-line.ts`, `widget-placement.ts`, **`github-issue-autocomplete.ts`**, `custom-footer.ts` |
| Complex | **`plan-mode/`**, **`subagent/`**, `preset.ts`, `sandbox/`, `gondolin/` |

**(b) The Package Catalog** at [pi.dev/packages](https://pi.dev/packages) — the community gallery, indexed off the `pi-package` npm keyword. As of this research it lists **5,430 packages** across 109 pages. Anything tagged `pi-package` appears automatically; there is no curation gate, and Pi's own docs warn that `pi install` from a stranger is `curl | sh` with extra steps.

**Top of catalog by monthly downloads** (relevant subset, with the ones that matter to us flagged):

| Package | ~DL/mo | Type | Relevance here |
|---|---|---|---|
| `pi-mcp-adapter` | 285K | ext | MCP, if you want it |
| `pi-web-access` | 208K | ext | Web search/fetch → feeds `/research` |
| **`pi-subagents`** | 191K | pkg | 🎯 delegation, chains, parallel — the F1 dependency |
| **`@juicesharp/rpiv-ask-user-question`** | 48K | ext | 🎯 typed-option questionnaire — closest existing thing to `/grilling` UI |
| **`@tintinweb/pi-subagents`** | 43K | ext | 🎯 Claude-Code-style autonomous sub-agents, `.pi/agents/*.md` convention |
| **`pi-lens`** | 42K | ext | 🎯 real-time LSP/lint/typecheck → `/tdd` feedback loop |
| `@juicesharp/rpiv-todo` | 40K | ext | live todo overlay surviving `/reload` + compaction |
| `@plannotator/pi-extension` | 36K | pkg | interactive plan review with annotations |
| `@gotgenes/pi-permission-system` | 31K | ext | permission enforcement |
| **`@quintinshaw/pi-dynamic-workflows`** | 31K | pkg | 🎯 fan-out across subagents, **git-worktree isolation**, resume |
| `pi-goal-list-loop-audit` | 28K | ext | goals + audited task queue + long loops |
| `bigpowers` | 23K | skill | 73 competing engineering skills |
| `@mjasnikovs/pi-task` | 22K | ext | deterministic task pipelines with verify/enforce gates |
| `@narumitw/pi-plan-mode` | 17K | ext | read-only `/plan` mode |
| `@narumitw/pi-lsp` | 16K | ext | language-agnostic LSP tools |
| `gentle-pi` | 15K | pkg | SDD/OpenSpec + subagents + **strict TDD evidence** + review guardrails |
| `@hypabolic/pi-hypa` | 15K | pkg | deterministic tool-output compression |
| `pi-crew` | 11K | pkg | coordinated teams, workflows, worktrees |

**The single most important thing on this list:** several packages already solve the *generic* machinery (sub-agents, worktrees, ask-user UI, LSP feedback). The work proposed in Part 4 is therefore deliberately a **thin, Matt-Pocock-specific layer that composes with them** — not a re-implementation. See §6.

---

# Part 2 — The gap analysis

## 2.1 The central asymmetry

```
 ┌──────────────────────────────────────────────────────────────────────┐
 │  PROMPT LAYER  (SKILL.md)                                            │
 │  "Claim it: assign it to yourself before any work."                  │
 │  "Red before green."                                                 │
 │  "Never resolve more than one ticket per session."                   │
 │  "The agent never stands in for the human's side of it."             │
 │  "No red-capable command, no Phase 2."                               │
 │                                                                      │
 │             ▲  a statistical hope, re-rolled every turn              │
 └─────────────┼────────────────────────────────────────────────────────┘
               │   ← THE GAP. Everything in Part 4 lives here.
 ┌─────────────┼────────────────────────────────────────────────────────┐
 │             ▼  a state machine that returns {block:true}             │
 │  HARNESS LAYER  (Pi extension)                                       │
 │  tool_call · registerTool · appendEntry · before_agent_start ·       │
 │  session_before_compact · ctx.ui · ctx.newSession                    │
 └──────────────────────────────────────────────────────────────────────┘
```

## 2.2 Structural gaps: what the skills assume vs. what Pi provides

| # | Skill assumption | Where it's stated | Pi status | Severity |
|---|---|---|---|---|
| G1 | **Sub-agents exist** | `code-review` ("parallel sub-agents so neither pollutes the other"), `research` ("background agent"), `grilling` ("dispatch a sub-agent to find facts"), `wayfinder` ("fire the research subagents"), `improve-codebase-architecture`, `codebase-design` | ✗ not built in | 🔴 Critical |
| G2 | **Prose `/skill` references dispatch** | `grill-with-docs` (7 lines, entirely a reference), `grill-me`, `implement`, `to-spec`, `wayfinder` `## Notes` | Partial: `/skill:name` exists; bare `/name` does not; prose ≠ dispatch | 🔴 Critical |
| G3 | **Only promoted skills are visible** | `.claude-plugin/plugin.json` allowlist | ✗ Pi's convention scan takes the whole tree | 🟠 High |
| G4 | **Background execution** | `research`: "so you keep working while it reads" | ✗ no background bash / async tool | 🟠 High |
| G5 | Tracker via `gh`/`glab` CLI | `issue-tracker-github.md` | ✓ `bash` works | 🟢 OK |
| G6 | Skills discoverable from `.agents/` | repo tagline | ✓ **native** | 🟢 Better than CC |
| G7 | `disable-model-invocation` honoured | `.agents/invocation.md` | ✓ **native** | 🟢 OK |
| G8 | Compaction at phase boundaries only | `ask-matt` → `PHASE-BOUNDARIES.md` | ✓ hookable, ✗ unenforced | 🟡 **Opportunity** |
| G9 | Context stays in the "smart zone" (~150k) | `ask-matt`; `grill-me.md` "dumb zone" | ✓ `ctx.getContextUsage()`, ✗ unused | 🟡 **Opportunity** |

## 2.3 Enforcement gaps: rules nothing enforces on *any* harness

These are the ones worth building for, because the extension isn't porting a Claude Code feature — it's adding something **neither** harness has. Each row is backed by a real, upvoted report.

| Rule in the skill | Reported failure | 👍 |
|---|---|---|
| *"A HITL ticket only resolves through that live exchange; the agent never stands in for the human's side of it."* (`wayfinder`) | [#785](https://github.com/mattpocock/skills/issues/785) **"Wayfinder on Pi never asked me a single question."** Weak local model went straight to writing code, the map, and tickets. | — |
| *"Ask the whole frontier in one round"* / one-at-a-time debate | [#663] "Don't replace grilling with batch-grill-me. Explicitly support both modes." · *"'Not bundling questions / grill one question at a time' not honored by claude. Strengthen that line?"* | 4 |
| Progress through the decision tree is visible | PR #593 comment: *"the single biggest QOL improvement I've had is instructing grilling to ask with: Question X of Y … If I see 2 of 15, it's time for a coffee."* | — |
| Resolved answers survive into the build | **"grill-with-docs: resolved answers are not traceable through PRD, issues, and implementation"** | 15 |
| *"Use /tdd where possible, at pre-agreed seams"* (`implement`) | **"/implement skips /tdd invocation and writes tests directly"** | 6 |
| A ticket is done when it's done | **"/implement has no completion step, finished tickets are never marked done"** | 8 |
| Tickets are independently workable | **"/implement should use git worktrees to isolate parallel sessions"** · "auto-sequencing multiple tickets via subagents" | 8 / 10 |
| Sub-issues are native, not body text | "to-issues: attach children as native sub-issues, not just body references" · "parent issue remains pickable after decomposition" | 12 / 5 |
| ADRs record *in-force* decisions | "grill-with-docs can write ADRs for unimplemented work, **poisoning `docs/adr/` as in-force context**" | 3 |
| `/code-review` is invocable | **"/code-review name clash with Claude Code built-in"** | 28 |
| Ship as a Pi package | **"Advertise the repo as a Pi package"** — a community member already built one | 5 |

> **Read the pattern.** Almost every top-voted issue is a request for *mechanism*, filed against a *document*. Matt is right to refuse most of them — `question-limits.md` in `.out-of-scope/` explains why a cap doesn't belong in the skill. But "doesn't belong in the skill" is not "doesn't belong anywhere." **It belongs in the harness.** That is the entire opportunity.

---

# Part 3 — A taxonomy of harnesses

Six archetypes. Every design in Part 4 is one of these, or a named composition of two.

| Archetype | Primary lever | Converts | Failure mode it kills |
|---|---|---|---|
| 🚧 **Gate** | `tool_call → {block:true, reason}` | "you must X before Y" → structurally impossible to skip | Silent step-skipping |
| 📒 **Ledger** | `appendEntry` + `registerEntryRenderer` + `setLabel` | ephemeral chat state → durable, compaction-proof, replayable artifact | State lost to compaction; untraceable decisions |
| 🔧 **Instrument** | `registerTool` | a described procedure → one atomic callable verb | Multi-step shell recipes half-executed |
| 🔭 **Lens** | `setWidget` / `setStatus` / `custom()` / autocomplete | invisible state → visible state | "How many questions left?" |
| 🧵 **Curator** | `before_agent_start` / `context` / `session_before_compact` | manual context hygiene → automatic, phase-aware | Drift into the dumb zone |
| 🔀 **Router** | `registerCommand` / `input` / `resources_discover` / `newSession` | prose invocation → real dispatch; phase boundary → one keystroke | Composition that never composes |

```
                        THE SIX LEVERS, MAPPED TO THE PIPELINE

  input ──► before_agent_start ──► turn ──► tool_call ──► exec ──► tool_result ──► turn_end
    │              │                          │                        │              │
    🔀 Router      🧵 Curator                 🚧 Gate                  📒 Ledger      🔭 Lens
    alias &        inject glossary,           block writes             persist        widgets,
    dispatch       system prompt,             until precondition       decisions      status,
    skills         compaction policy          holds                    & answers      progress
                                              │
                                              └──► 🔧 Instrument (registerTool)
                                                   grill_frontier · wayfinder_map ·
                                                   code_review · subagent briefs
```

### The design rule

> **A good harness is smaller than the skill it serves.** If your extension needs to restate the skill's content, you've built a competing framework — precisely the thing Matt's README rejects about GSD/BMAD/Spec-Kit ("they take away your control and make bugs in the process hard to resolve"). A good harness enforces *one sentence* and stays silent about everything else. The skill remains the source of truth; the extension is a clamp on one joint.


---

# Part 4 — Fifteen harnesses

## 4.0 Where they attach

### Chain A annotated — `grill-with-docs → … → implement`

```
        /grill-with-docs                /to-spec      /to-tickets       /implement
              │                            │              │                 │
   ┌──────────┴──────────┐            ┌────┴────┐    ┌────┴────┐   ┌────────┴─────────┐
   │                     │            │         │    │         │   │                  │
 [H4] grill-frontier   [H5] hitl    [H4] export│  [H11] ticket │ [H9] tdd-loop   [H10] two-axis
 📒🔭 tree · frontier   🚧 gate       cited      │   lifecycle  │ 🚧🔭 red→green   🔧🚧 review
    · progress · labels    agent may   decision  │   📒🚧 bind, │  state machine   parallel,
                           not answer  log ──────┼──► close,    │                  cannot
 [H14] context-glossary    its own              │   scope-gate │                  collapse
 🧵 inject CONTEXT.md      questions            │              │
    terms, challenge                     [H15] adr-guard  [H11] auto-sequence via
    conflicts                            🚧 no ADR for      ctx.newSession()
                                            unbuilt work       = "/clear between each"
   ──────────────── [H6] context-guard 🧵 smart-zone gauge · compact only at phase boundaries ────────────────
   ──────────────── [H2] skill-dispatch 🔀 makes every /grilling · /tdd · /code-review reference resolve ─────
```

### Chain B annotated — `wayfinder → … → implement`

```
   ╔═ CHART ══════════════════════════════╗      ╔═ WORK ONE TICKET ════════════════╗
   ║ destination · frontier · map · wire  ║      ║ load · claim · resolve · graduate║
   ╚══════════════┬═══════════════════════╝      ╚══════════┬═══════════════════════╝
                  │                                         │
        [H7] wayfinder-map 🔧📒🔭                  [H7] frontier widget · #-autocomplete by NAME
          chart() ticket.create() block()             claim() gate: no work before claim
          — resolves blocker DB ids for you           resolve() = comment+close+index, ATOMIC
          — atomic 3-step resolve                     🚧 second resolve() in a session → blocked
                  │
        [H8] wayfinder-fanout 🔧                   [H5] hitl-gate 🚧
          N research tickets → N pi processes         grilling/prototype/task tickets are HITL
          → N git worktrees on research/<slug>        → agent cannot write until a REAL human
          → cited md + auto-resolve                      message arrives (event.source check)
                  │
                  ▼   map clear
        ┌─────────────────────────────────────────────┐
        │ ⚠ THE COLLAPSE — /to-spec is mandatory here │
        │   [H7] export: map + Decisions-so-far →     │
        │   structured input for /to-spec.            │
        │   No harness may shortcut map → implement.  │
        └──────────────────┬──────────────────────────┘
                           ▼
                    /to-spec → /to-tickets → /implement  (as Chain A)
```

---

## Tier 0 · Foundation — *nothing else works without these*

### H1 · `pi-mp-skills` — the distribution package
**Archetype:** — (packaging) · **Size:** ~25 lines of JSON, zero TypeScript · **Effort:** S

**Fixes:** G3. Pi's convention scanner would surface `in-progress/`, `misc/` and `deprecated/` skills alongside the supported 25.

```jsonc
{
  "name": "pi-mp-skills",
  "keywords": ["pi-package"],
  "pi": {
    "skills": [
      "skills/engineering/**",
      "skills/productivity/**",
      "!skills/in-progress/**", "!skills/misc/**", "!skills/deprecated/**"
    ]
  }
}
```
Install: `pi install git:github.com/<you>/pi-mp-skills`. Because Pi honours `disable-model-invocation`, the user-invoked/model-invoked split survives untouched — `/skill:grill-with-docs` works, and the model can't self-fire it.

> **Note:** a community member has already attempted this (issue [#624](https://github.com/mattpocock/skills/issues/624)); the linked repo is no longer reachable. Publishing a maintained one under the `pi-package` keyword is the single cheapest contribution available.

---

### H2 · `pi-skill-dispatch` — make prose invocation actually dispatch 🔀
**Archetype:** Router · **Size:** ~120 lines · **Effort:** S · **Impact:** 🔴 highest ratio in the report

**Fixes:** G2. `grill-with-docs` is seven lines: *"Run a `/grilling` session, using the `/domain-modeling` skill."* On Pi that string resolves to nothing. The model may read `grilling/SKILL.md`, or may improvise an interview from the description alone. `implement` (15 lines) has the same shape and the same exposure — hence the reported "`/implement` skips `/tdd`".

**Mechanism:**
1. `resources_discover` → enumerate loaded Matt skills, build a name→path map.
2. `registerCommand(name)` for each promoted skill → alias `/grilling` to `/skill:grilling`, so the bare names in the SKILL bodies are real commands. (Also sidesteps the 28👍 `/code-review` name clash: on Pi, **you choose the name**.)
3. `registerTool("use_skill", {name, args})` → the model can *call* a skill instead of hoping to remember to read it. Returns the full `SKILL.md` body as tool content, so it lands in context deterministically.
4. `before_agent_start` → inject a ~200-token alias table so the model knows the mapping without a lookup.

```ts
pi.registerTool({
  name: "use_skill",
  label: "Use Skill",
  promptSnippet: "Load a named skill's full instructions before following them",
  promptGuidelines: [
    "Call use_skill whenever a skill body refers to another skill by /name — " +
    "never paraphrase a skill you have not loaded.",
  ],
  parameters: Type.Object({ name: Type.String(), args: Type.Optional(Type.String()) }),
  async execute(_id, { name, args }) {
    const body = await loadSkill(name);              // from resources_discover map
    return { content: [{ type: "text", text: args ? `${body}\n\nUser: ${args}` : body }] };
  },
});
```

**Why it's the best ratio in the report:** it costs a day and repairs the composition backbone of all 25 skills at once.

---

### H3 · `pi-mp-agents` — typed sub-agent briefs 🔧
**Archetype:** Instrument · **Size:** ~200 lines + 5 agent `.md` files · **Effort:** M · **Impact:** 🔴 critical

**Fixes:** G1, G4. **Do not build a sub-agent framework** — `pi-subagents` (191K/mo), `@tintinweb/pi-subagents` (43K/mo) and `@quintinshaw/pi-dynamic-workflows` already exist, and the first-party `examples/extensions/subagent/` shows the spawn pattern. Build the **typed briefs** on top, because Matt's skills specify each agent's brief and isolation requirement *exactly*, and a generic `subagent(task)` tool lets the model collapse them.

| Agent | From skill | Tools | Non-negotiable constraint |
|---|---|---|---|
| `mp:fact-finder` | `grilling` — *"Finding facts is your job, never the user's"* | `read grep find ls bash` (RO) | **May not ask questions.** Returns facts or "unknown". |
| `mp:researcher` | `research` | RO + web | **Primary sources only.** Writes one cited `.md`, returns its path. |
| `mp:standards` | `code-review` axis 1 | RO + `git diff` | Receives the **Fowler smell baseline pasted in full** — the skill says the sub-agent has no other access to it. <400 words. |
| `mp:spec` | `code-review` axis 2 | RO + `git diff` | Receives the fetched spec. Reports missing / scope-creep / wrong. <400 words. |
| `mp:deepener` | `improve-codebase-architecture` | RO | Surfaces deepening opportunities in `codebase-design` vocabulary. |

The critical property is **isolation as a hard guarantee**, not a request. `code-review`'s "Why two axes" section exists because one axis masks the other; a shared context defeats the whole design. H3 spawns two processes and never lets them see each other.

---

## Tier 1 · The grilling harnesses

### H4 · `pi-grill-frontier` — the decision tree, made real 📒🔭
**Archetype:** Ledger + Lens · **Size:** ~350 lines · **Effort:** M · **Impact:** 🔴 flagship

`grilling` is the primitive under `grill-me`, `grill-with-docs`, `wayfinder`, `triage` and `improve-codebase-architecture`. It defines a **design tree**, a **frontier** (decisions whose prerequisites are settled), and **rounds**. All of it currently lives in the model's head — so it drifts, dies at compaction, and cannot be cited later.

**Fixes:** the 15👍 traceability issue, the "Question X of Y" request, the batch-vs-one-at-a-time argument (#663), and the dumb-zone drift.

**Mechanism:**

```ts
// one tool, five verbs — the model cannot fake the frontier because the tool computes it
grill_frontier({ action: "open",     destination })
grill_frontier({ action: "ask",      questions: [{ id, title, body, recommendation, dependsOn }] })
grill_frontier({ action: "answer",   id, text })
grill_frontier({ action: "graduate", parentId, questions })   // fog → frontier
grill_frontier({ action: "export" })                          // → cited decision log
```

- **Ledger:** `pi.appendEntry("mp:grill-tree", tree)` — custom entries **do not enter LLM context**, so the tree costs nothing per turn but survives `/reload`, `/compact`, and session restore.
- **Frontier is computed, not claimed.** `dependsOn` edges are the input; the tool returns the askable set. A question whose prerequisite is open is *rejected* — enforcing "a question whose answer depends on another question still open in this round belongs to a later round."
- **Lens:** `ctx.ui.setWidget` →
  ```
  ┌ grilling · destination: partial order cancellation ───────────┐
  │ round 3   ● Q2 of 5 open   ✔ 12 resolved   ○ 4 in fog         │
  │ ▸ Q2 Cancellation window — per-item or per-order?             │
  └───────────────────────────────────────────────────────────────┘
  ```
  This is verbatim the highest-value QoL request from the community.
- **Mode as a setting, not a fork:** `"mp.grill.mode": "batch" | "one-at-a-time"` resolves #663 without touching the skill.
- **The killer feature — labelled decisions in the session tree.** On each `answer`, call `pi.setLabel(entryId, "decision:Q7 cancellation-window")`. Pi's `/tree` now lets you **jump back to the exact moment a decision was made and branch a different answer**, with all history preserved in one file. No other harness can offer this, and it is the natural home for "what if we'd said per-order?"
- **`export`** emits a stable-ID markdown decision log. `/to-spec` cites it; `/to-tickets` inherits the IDs; `/code-review`'s Spec axis can trace an implementation line back to `Q7`. **That is the 15👍 issue, closed.**

---

### H5 · `pi-hitl-gate` — the agent may not answer its own questions 🚧
**Archetype:** Gate · **Size:** ~150 lines · **Effort:** S · **Impact:** 🔴 critical · **Pi-only**

**Fixes:** issue #785 directly — *"Wayfinder on Pi never asked me a single question. It immediately wrote a bunch of code and created the foundation, map, initial tickets."*

`wayfinder` states the rule plainly: *"A HITL ticket only resolves through that live exchange; the agent never stands in for the human's side of it (a grilling agent that answers its own questions has broken this)."* Nothing enforces it. On a weaker or cheaper model, nothing ever will.

**Mechanism — and this is the one that cannot be built on Claude Code:**

```ts
let awaitingHuman = false;

pi.on("input", (event) => {
  // ONLY a real human clears the gate. Extension-injected and agent-driven
  // messages carry source "extension" / "rpc" and are ignored.
  if (event.source === "interactive") awaitingHuman = false;
});

pi.on("tool_call", async (event) => {
  if (!awaitingHuman) return;
  if (MUTATING.has(event.toolName) || isMutatingBash(event.input)) {
    return {
      block: true,
      reason:
        "HITL gate: a grilling round is open and the human has not answered. " +
        "wayfinder/SKILL.md: 'the agent never stands in for the human's side of it.' " +
        "Ask, then wait.",
    };
  }
});
```

`awaitingHuman` is set when H4 records an `ask`, or when the active skill is HITL (`grill-me`, `grill-with-docs`, `grilling`) and the assistant message ends in a question. Read-only tools stay open — the agent should keep finding facts while it waits, exactly as `grilling` instructs.

**Second rule, same extension:** wayfinder's *"never resolve more than one ticket per session — with the exception of research."* Count `wayfinder_map.resolve()` calls per session; block the second with a pointer to `/mp-handoff` (H12).

---

### H6 · `pi-context-guard` — the smart zone, instrumented 🧵🔭
**Archetype:** Curator + Lens · **Size:** ~250 lines · **Effort:** M · **Impact:** 🟠 high · **Pi-only**

`ask-matt` is precise: keep grilling → spec → tickets in **one unbroken window**; the **smart zone** is ~150k tokens on SOTA models; make the compact/clear/handoff decision **at a phase boundary**, never mid-phase. `grill-me.md` names the failure: *"Very long sessions also drift into the dumb zone, where the context window is full enough that the questions get worse."*

Today that's a document you're supposed to remember. Pi makes it a control system.

| Behaviour | Mechanism |
|---|---|
| Smart-zone gauge, not a token count | `ctx.getContextUsage()` at `turn_end` → `ctx.ui.setStatus("zone", "smart 61% · dumb zone in ~48k")` |
| Know which phase you're in | capture `/skill:<name>` from `input`; phase = grill \| spec \| tickets \| implement \| review |
| **Refuse to compact mid-phase** | `session_before_compact` → `{cancel: true}` while a grilling round is open; notify "finish the round first" |
| Compact *well*, per phase | `session_before_compact` → return a custom summary: during grilling, preserve the H4 tree verbatim; during implement, preserve ticket + seams + the current failing test |
| Offer the boundary decision | at a phase transition above threshold: `ctx.ui.select("Phase boundary: to-tickets → implement", ["continue", "compact", "handoff", "new session"])` — the five-option tree from `PHASE-BOUNDARIES.md`, as a menu |

Replacing compaction is the capability OpenClaw shipped on and Claude Code does not expose. Here it's used to make sure the decision tree is the last thing to be thrown away, not the first.

---

## Tier 2 · The wayfinder harnesses

### H7 · `pi-wayfinder-map` — the map as a first-class object 🔧📒🔭
**Archetype:** Instrument + Ledger + Lens · **Size:** ~500 lines · **Effort:** L · **Impact:** 🔴 highest absolute value

`wayfinder` is the most mechanically demanding skill in the set. Per session the model must, in shell:

- find the map issue by the `wayfinder:map` label
- list its children, drop any with `issue_dependencies_summary.blocked_by > 0` or an assignee, take the first **in map order**
- assign `@me` **before any other write**
- on resolve: comment the answer, **close**, **and** append a context pointer to Decisions-so-far — three calls, all or nothing
- to wire a blocker: `gh api --method POST .../dependencies/blocked_by -F issue_id=<numeric DATABASE id>` — *not* the `#number`, *not* the `node_id`

That last one is a genuine footgun buried in a reference doc. Issue #785 shows what happens when a model improvises this.

**Mechanism:** one tool, tracker-abstracted, reading `docs/agents/issue-tracker.md` — the file `/setup-matt-pocock-skills` already writes. **The extension is configured by the existing skill; it introduces no competing config.**

| Verb | Guarantees |
|---|---|
| `chart(destination, notes)` | Creates the map with the exact body template and `wayfinder:map` label |
| `ticket.create(question, type)` | Child issue + `wayfinder:<research\|prototype\|grilling\|task>` label |
| `ticket.block(child, blocker)` | Resolves the blocker's **database id** automatically; falls back to a body line where dependencies are unavailable |
| `frontier()` | open ∧ unblocked ∧ unclaimed, **first in map order** — computed, not guessed |
| `claim(id)` | Assign `@me`. **Gate: every other verb refuses until a claim exists this session.** |
| `resolve(id, answer)` | comment + close + append-to-index, **atomically** |
| `graduate(patch, tickets)` | Creates tickets *and* clears the patch from Not-yet-specified — so a decision lives in exactly one place |
| `scope_out(id, reason)` | Closes the ticket, writes one line to **Out of scope**, and **never** to Decisions-so-far |
| `export()` | The collapse input for `/to-spec` |

Backends: `github` (gh) · `gitlab` (glab) · `local` (markdown per `issue-tracker-local.md`).

**Lens:**
```
┌ wayfinder · Course: AI Coding Crash Course ──────────────────────┐
│ frontier (3)                                    12 resolved · 5 fog│
│  ▸ Which runtime for the exercise sandbox?      🔍 research        │
│    Lesson ordering: depth-first or breadth?     💬 grilling        │
│    Provision Vercel team seat                   🔨 task            │
│ blocked (4)   claimed by you (1)                                   │
└────────────────────────────────────────────────────────────────────┘
```
Plus `ctx.ui.addAutocompleteProvider` so `#` completes open tickets **by name** — honouring the skill's own "Refer by name" rule (*"A wall of `#42, #43, #44` is illegible"*). The first-party `github-issue-autocomplete.ts` example is the template.

---

### H8 · `pi-wayfinder-fanout` — research tickets, actually in parallel 🔧
**Archetype:** Instrument · **Size:** ~250 lines atop H3 · **Effort:** M · **Impact:** 🟠 high

Charting step 5: *"For each `research` ticket you just created, spin up a `/research` subagent to resolve it **in parallel**, capturing its findings on a throwaway `research/<name>` branch with a context pointer from the ticket."*

That is N processes × N git worktrees × N issue comments. No model does it reliably by hand; most do one, sequentially, and forget the branch.

```
wayfinder_fanout()
   │
   ├─ read map children where label = wayfinder:research and state = open
   │
   ├─ for each, in parallel (bounded concurrency):
   │     git worktree add ../.wf/<slug> -b research/<slug>
   │     spawn pi --agent mp:researcher  (cwd = worktree)   [H3]
   │     stream progress → onUpdate() → widget row
   │     on done:  commit cited .md → wayfinder_map.resolve(id, summary + branch ptr)
   │
   └─ Ctrl+C → ctx.signal propagates → all children killed, worktrees left for inspection
```

Worktree isolation is the same primitive the 8👍 `/implement` request asks for; build it once here and reuse it in H11.

---

## Tier 3 · The implement harnesses

### H9 · `pi-tdd-loop` — red before green, as a state machine 🚧🔭
**Archetype:** Gate + Lens · **Size:** ~300 lines · **Effort:** M · **Impact:** 🔴 highest per-line value

`tdd` says two things that nothing enforces, and whose violation is both consequential and invisible:

1. *"**Test only at pre-agreed seams.** No test is written at an unconfirmed seam."*
2. *"**Red before green.** Write the failing test first, then only enough code to pass it."*

Plus the anti-pattern: *"**Horizontal slicing** — writing all tests first, then all implementation."* Reported failure: "/implement skips /tdd invocation and writes tests directly" (6👍).

**Mechanism — the loop becomes a real state machine, and the gate is the transition function:**

```
        seams.declare() → human confirms via ctx.ui
  IDLE ─────────────────────────────► SEAMS_AGREED
                                            │  write test file at an agreed seam
   ▲                                        ▼
   │                                   TEST_WRITTEN ── ✗ impl writes BLOCKED
   │                                        │  bash: test command observed FAILING
   │                                        ▼
   │                                    RED ── ✓ impl writes now allowed
   │                                        │  bash: test command observed PASSING
   │                                        ▼
   └──────── next slice ◄──────────── GREEN ── ✗ a SECOND test file BLOCKED
                                              (that's horizontal slicing)
```

- `tool_call` on `write`/`edit` where the path matches test globs → blocked unless a confirmed seam covers it.
- `tool_call` on implementation paths in `TEST_WRITTEN` → blocked with: *"tdd/SKILL.md: Red before green. Run the test and watch it fail first."*
- `tool_result` on `bash` → parse the configured test command's output to drive `RED`/`GREEN`. (Compose with **`pi-lens`** — 42K/mo — for typecheck/LSP signal rather than reimplementing it.)
- Widget: `● RED  slice 3/7  seam: OrderService.cancel  ⏱ 1.8s`.

The reason to build this above all others: TDD is the skill whose violation costs the most and shows the least.

---

### H10 · `pi-two-axis-review` — a review that cannot collapse 🔧🚧
**Archetype:** Instrument + Gate · **Size:** ~250 lines atop H3 · **Effort:** M · **Impact:** 🟠 high

`code-review` runs Standards and Spec as **parallel sub-agents "so they don't pollute each other's context"**, then aggregates **without merging or reranking** — *"Don't pick a single winner across axes — that's the reranking the separation exists to prevent."*

Given a single context and a generic subagent tool, a model will nearly always collapse this into one pass. H10 makes collapse impossible.

- **Preflight gate, exactly as specified:** validate `git rev-parse <fixed-point>` and a non-empty `git diff <fp>...HEAD` **before** spawning. The skill is explicit: *"A bad ref or empty diff should fail here — not inside two parallel sub-agents."* A perfect gate, already written down.
- Spawns `mp:standards` (with the Fowler baseline pasted in full) and `mp:spec` (with the fetched spec) in parallel; if no spec is found, skips the Spec agent and says so.
- `renderResult` prints two columns under `## Standards` and `## Spec`, verbatim, with a per-axis worst-issue line and **no cross-axis ranking**.
- Names itself `/mp-review` → the 28👍 name-clash issue is a non-problem on Pi.

---

### H11 · `pi-ticket-lifecycle` — close the loop `implement` never closes 📒🚧🔀
**Archetype:** Ledger + Gate + Router · **Size:** ~300 lines · **Effort:** M · **Impact:** 🟠 high

Three upvoted issues, one extension: *no completion step* (8👍), *no worktree isolation* (8👍), *no auto-sequencing* (10👍), plus *parent issue remains pickable* (5👍).

| Capability | Mechanism |
|---|---|
| Bind session ↔ ticket | On `/skill:implement <ref>`, fetch the ticket, `pi.setSessionName("#123 cancel partial orders")`, `appendEntry("mp:ticket", …)` |
| Scope gate | Block `git commit` when the diff touches files outside the ticket's declared surface without a `ctx.ui.confirm` — catching scope creep *before* the Spec axis has to |
| Completion | On `agent_settled` after a green commit: check acceptance criteria, then `confirm` → close ticket, comment SHA, tick the boxes |
| Parent hygiene | After `to-tickets` decomposition, mark the parent `blocked-by` all children so the frontier query stops offering it |
| **Auto-sequencing** | `implement_next()` → read the ticket frontier → `ctx.newSession({ setup: seed ticket, withSession: sendUserMessage("/skill:implement") })` |

That last row deserves emphasis. `ask-matt` says: *"kick off `/implement` per ticket, **`/clear`ing context between each one**. Each ticket is self-contained, so the last one's context is disposable."* Pi's `ctx.newSession({setup, withSession})` **is that sentence, as an API**. The mapping is exact, and it's the cleanest example in the report of a skill instruction that Pi happens to have a purpose-built primitive for.

Optional worktree isolation reuses H8's machinery.

---

### H12 · `pi-mp-handoff` — the phase boundary as one keystroke 🔀
**Archetype:** Router · **Size:** ~180 lines · **Effort:** S · **Impact:** 🟡 medium-high

`handoff` writes a markdown file to the OS temp dir; you then open a fresh session and point it at the file. On Pi that whole ritual collapses:

```
/mp-handoff "prototype the cancellation state machine"
   │
   ├─ generate the doc per handoff/SKILL.md (redact secrets, reference artifacts by path/URL,
   │  include the "suggested skills" section)
   ├─ pi.setLabel(currentLeaf, "handoff:prototype-cancellation")   ← findable in /tree
   └─ ctx.newSession({
        setup:       sm => sm.appendMessage(doc),
        withSession: ctx => ctx.sendUserMessage("/skill:prototype …"),
      })
```

Zero copy-paste, zero context loss, and the fork point is a labelled node you can return to. Addresses the 6👍 "respond with a copyable prompt for the next agent" by making the copy unnecessary.

---

## Tier 4 · Small delights

### H13 · `pi-wait-what` 🔀🔭 — ~40 lines · Effort: XS
`wait-what` is a 7-line skill and the repo's own answer to model verbosity. Two additions: bind it to `ctrl+w` via `registerShortcut` so it's a reflex rather than a command; and register a `markdownTransformer` that dims any capitalised term in assistant output that is **not** in `CONTEXT.md`. The skill is the cure; this is the smoke alarm.

### H14 · `pi-context-glossary` 🧵 — ~150 lines · Effort: S
`CONTEXT.md` exists to save tokens, but making the agent `read` the whole file every session costs a tool call and the full file. Instead, `before_agent_start` injects **only the glossary term list** (~200 tokens) into the system prompt, always-on and free of tool calls. Then implement `domain-modeling`'s *"Challenge against the glossary"* mechanically: when a transcript term collides with a defined one, inject a one-line nudge. Multi-context repos are handled by reading `CONTEXT-MAP.md` and injecting only the context matching `cwd`.

### H15 · `pi-adr-guard` 🚧 — ~120 lines · Effort: XS
Fixes the reported *"grill-with-docs can write ADRs for unimplemented work, poisoning `docs/adr/` as in-force context."* Gate `write` to `docs/adr/**` on two conditions: the three-part test from `domain-modeling` is answered via `ctx.ui.confirm` (hard to reverse? surprising without context? a real trade-off?), and a `Status:` field is set. `proposed` ADRs are filtered out of injected context by H14 until they're `accepted`. Small, surgical, closes a real bug.


---

# Part 5 — Prioritisation and build order

## 5.1 The full matrix

| ID | Extension | Type | Effort | Impact | Pi-only? | Evidence | Score |
|---|---|---|---|---|---|---|---|
| **H2** | `pi-skill-dispatch` | 🔀 | S | 🔴 5 | partly | G2 · "/implement skips /tdd" | **★★★★★** |
| **H5** | `pi-hitl-gate` | 🚧 | S | 🔴 5 | **yes** | #785 · "not honored by claude" | **★★★★★** |
| **H1** | `pi-mp-skills` | pkg | S | 🟠 3 | n/a | #623 · #624 | **★★★★★** |
| **H9** | `pi-tdd-loop` | 🚧🔭 | M | 🔴 5 | no | 6👍 | **★★★★★** |
| **H4** | `pi-grill-frontier` | 📒🔭 | M | 🔴 5 | mostly | 15👍 · PR#593 · #663 | **★★★★☆** |
| **H7** | `pi-wayfinder-map` | 🔧📒🔭 | L | 🔴 5 | no | #785 · 12👍 | **★★★★☆** |
| **H3** | `pi-mp-agents` | 🔧 | M | 🔴 5 | no | G1 | **★★★★☆** |
| **H11** | `pi-ticket-lifecycle` | 📒🚧🔀 | M | 🟠 4 | mostly | 8👍 · 10👍 · 5👍 | **★★★★☆** |
| **H6** | `pi-context-guard` | 🧵🔭 | M | 🟠 4 | **yes** | G8 · G9 · "dumb zone" | **★★★★☆** |
| **H10** | `pi-two-axis-review` | 🔧🚧 | M | 🟠 4 | partly | 28👍 (naming) | **★★★☆☆** |
| **H12** | `pi-mp-handoff` | 🔀 | S | 🟡 3 | **yes** | 6👍 | **★★★☆☆** |
| **H8** | `pi-wayfinder-fanout` | 🔧 | M | 🟠 4 | no | wayfinder step 5 | **★★★☆☆** |
| **H15** | `pi-adr-guard` | 🚧 | XS | 🟡 3 | no | 3👍 | **★★★☆☆** |
| **H14** | `pi-context-glossary` | 🧵 | S | 🟡 3 | partly | `domain-modeling` | **★★★☆☆** |
| **H13** | `pi-wait-what` | 🔀🔭 | XS | 🟡 2 | partly | `wait-what` | **★★☆☆☆** |

## 5.2 Dependency graph

```
                      ┌──────────────────┐
                      │ H1 pi-mp-skills  │  (packaging — install first)
                      └────────┬─────────┘
                               │
                      ┌────────▼─────────┐
                      │ H2 skill-dispatch│  ◄── the backbone. Everything reads better after this.
                      └───┬─────────┬────┘
              ┌───────────┘         └────────────┐
     ┌────────▼────────┐               ┌─────────▼────────┐
     │ H3 mp-agents    │               │ H4 grill-frontier│
     │ (typed briefs)  │               │  (decision tree) │
     └──┬────┬─────┬───┘               └────┬────────┬────┘
        │    │     │                        │        │
   ┌────▼─┐┌─▼───┐┌▼───────┐         ┌──────▼──┐  ┌──▼────────────┐
   │H8 fan││H10  ││(future)│         │H5 hitl  │  │H6 context-    │
   │ -out ││2-axis│        │         │  gate   │  │   guard       │
   └───┬──┘└──┬──┘└────────┘         └────┬────┘  └───────────────┘
       │      │                            │
   ┌───▼──────▼────────────────────────────▼───┐
   │ H7 wayfinder-map ◄── needs H3(research),  │
   │                      H4(grilling tickets),│
   │                      H5(HITL gate)        │
   └───────────────────┬───────────────────────┘
                       │
              ┌────────▼──────────┐
              │ H11 ticket-       │ ◄── needs H8 (worktrees), H9 (green check)
              │     lifecycle     │
              └───────────────────┘

   Independent, any time:  H9 tdd-loop · H12 handoff · H13 wait-what
                           H14 glossary · H15 adr-guard
```

## 5.3 Three shippable bundles

Rather than 15 packages, ship **three**. Each is coherent, independently useful, and installable alone.

```
╔══════════════════════════════════════════════════════════════════════╗
║  BUNDLE 1 · pi-mp-core                      ~1 week · unblocks all   ║
║  H1 skills package · H2 skill-dispatch · H3 typed agent briefs       ║
║  ─────────────────────────────────────────────────────────────────── ║
║  "Matt Pocock's skills, correctly installed and actually composable  ║
║   on Pi."  Nothing here is opinionated. Pure gap-closing.            ║
╚══════════════════════════════════════════════════════════════════════╝
                                  ▼
╔══════════════════════════════════════════════════════════════════════╗
║  BUNDLE 2 · pi-mp-discipline                ~2 weeks · the value     ║
║  H5 hitl-gate · H9 tdd-loop · H4 grill-frontier · H6 context-guard   ║
║  H15 adr-guard · H14 glossary                                        ║
║  ─────────────────────────────────────────────────────────────────── ║
║  "The rules in the SKILL.md files, enforced."                        ║
║  This is the bundle that justifies the whole exercise. It is also    ║
║  the one that works on weak/local models, where prompt adherence     ║
║  collapses and mechanism is the only thing left. (cf. #785)          ║
╚══════════════════════════════════════════════════════════════════════╝
                                  ▼
╔══════════════════════════════════════════════════════════════════════╗
║  BUNDLE 3 · pi-mp-flow                      ~3 weeks · the ambition  ║
║  H7 wayfinder-map · H8 fanout · H11 ticket-lifecycle ·               ║
║  H10 two-axis-review · H12 handoff · H13 wait-what                   ║
║  ─────────────────────────────────────────────────────────────────── ║
║  "The tracker operations and session choreography, automated."       ║
║  Highest absolute value, highest maintenance cost — it tracks three  ║
║  tracker APIs and Matt's fastest-moving skill.                       ║
╚══════════════════════════════════════════════════════════════════════╝
```

**If you build exactly one thing:** H5 `pi-hitl-gate`. It is small, it is Pi-only, it fixes a documented Pi-specific failure, and it is the difference between these skills working and not working on anything other than a frontier model.

**If you build exactly three:** H1 + H2 + H5. One weekend, and the set goes from "mostly works if the model cooperates" to "works."

---

# Part 6 — Composing with the existing ecosystem

The catalogue already contains 5,430 packages. Rebuilding generic machinery is the fastest way to make this project pointless. The proposed layer is deliberately thin.

| Need | ❌ Don't build | ✅ Compose with | What *you* add |
|---|---|---|---|
| Sub-agent execution | a framework | `pi-subagents` (191K/mo) · `@tintinweb/pi-subagents` (43K) · first-party `examples/extensions/subagent/` | **H3** — the five typed briefs and the isolation guarantee |
| Typed question UI | a picker | `@juicesharp/rpiv-ask-user-question` (48K) · `pi-ask-user` | **H4** — the frontier computation, the tree ledger, session labels |
| Type/lint feedback | an LSP client | **`pi-lens`** (42K) · `@narumitw/pi-lsp` (16K) | **H9** — the red→green state machine that consumes that signal |
| Worktree fan-out | a scheduler | `@quintinshaw/pi-dynamic-workflows` (31K) · `pi-crew` | **H8/H11** — wayfinder/ticket semantics on top |
| Issue autocomplete | from scratch | first-party `github-issue-autocomplete.ts` | **H7** — complete by *name*, per the skill's own rule |
| Todo/progress overlay | a widget lib | `@juicesharp/rpiv-todo` (40K) | **H4/H7** — but the frontier is not a todo list; keep it separate |
| Context compression | a compressor | `@hypabolic/pi-hypa` (15K) | **H6** — phase-aware *summaries*, a different concern |
| Permissions | a policy engine | `@gotgenes/pi-permission-system` (31K) | **H5** — a semantic gate, not an access gate |
| MCP | an adapter | `pi-mcp-adapter` (285K) | nothing — the skills use `gh`/`glab` |

⚠️ **Watch for overlap and philosophy clash.** `gentle-pi` ("SDD/OpenSpec, subagents, strict TDD evidence, review guardrails") and `bigpowers` (73 prescriptive skills) occupy adjacent ground with a *different* philosophy — they own the process, which is exactly what Matt's README positions against ("GSD, BMAD, and Spec-Kit … take away your control"). Installing them alongside these harnesses will produce two agents arguing about method. Pick one worldview.

---

# Part 7 — Risks, and the honest counterarguments

| Risk | Weight | Mitigation |
|---|---|---|
| **Skills move fast.** v1.0 → v1.2.3 in ~2 months, with renames (`to-prd`→`to-spec`, `to-issues`→`to-tickets`, `writing-great-skills`→`writing-for-agents`), graduations, and deprecations. Gates keyed to skill text will rot. | 🔴 High | Key gates to **artifacts and tool calls**, never to prose. H9 watches file paths and test exit codes; H7 watches tracker state. Neither greps a `SKILL.md`. Pin the skills package version. |
| **Over-enforcement kills the thing that makes the skills good.** Matt's stated design value is *"small, easy to adapt, composable … Hack around with them."* A gate you can't override is a framework. | 🔴 High | Every gate must be **overridable in one gesture** — a `ctx.ui.confirm` escape, a `--no-mp-gates` flag, a settings toggle. Blocked calls return the *reason with a skill citation*, so the model can argue back. Never silently block. |
| **Wayfinder is a moving target.** It's the newest headline skill (v1.1, July 2026) and the most likely to be reshaped. H7 is 500 lines against it. | 🟠 Med | Ship H7 last. Keep the tracker abstraction behind `docs/agents/issue-tracker.md` so a tracker change costs one adapter, not a rewrite. |
| **Supply chain.** Pi's docs are blunt: extensions run with full system permissions; `pi install` from a stranger is `curl \| sh` with extra steps. These extensions gate tool calls and spawn processes. | 🟠 Med | Single-purpose packages, small diffs, no network at load, `peerDependencies: "*"` for the five bundled core packages. Publish the source, not a bundle. |
| **Weak models make gates louder, not smarter.** A blocked model may thrash rather than comply. | 🟡 Low | `terminate: true` on the third consecutive identical block; escalate to `ctx.ui` and ask the human. |
| **Matt may ship a native Pi package.** ADR-0002 defers a Codex plugin; a Pi one is plausible. | 🟡 Low | H1 would be superseded — which is a *good* outcome. H2–H15 remain valuable regardless; they're harness work, not packaging. |

### The strongest counterargument, stated fairly

> *These skills were designed for frontier models with strong instruction adherence. On Opus- or GPT-class models the compliance rate is high enough that gates mostly fire on false positives, and you've added 3,000 lines of TypeScript to fix a problem the next model release fixes for free.*

That's partly true, and it's why the report ranks **H2 (dispatch)** and **H1 (packaging)** alongside the gates: those close *structural* gaps that no model improvement touches. But the counterargument is weakest exactly where Pi is strongest. Pi's whole reason for existing is model-agnosticism — 15+ providers, local llama.cpp, mid-session switching. Issue #785 is a Qwen3-Coder-30B user watching wayfinder skip the interview entirely. **On the models Pi exists to support, mechanism is not redundant with the prompt. It is the only thing that works.**

---

# Part 8 — What to do on Monday

1. **Install and measure the baseline.** `pi install git:github.com/mattpocock/skills`, run `/skill:grill-with-docs` on a real change, and log: did `/grilling` actually load? Did `/domain-modeling` fire? Did it ask before writing? That's your control group, and it takes an hour.
2. **Ship H1 + H2** (a weekend). Re-run the same test. This is the measurable delta that justifies everything after it.
3. **Ship H5** (a day). Re-run on a *cheap* model. This is where the graph moves.
4. Then H9, then H4, then decide whether the wayfinder tier is worth it for your team — it's the highest value and the highest maintenance, and that trade is genuinely yours to make.

---

## Appendix — Sources

**Primary (read directly, at HEAD):**
- `github.com/mattpocock/skills` — full repo clone, 6 Aug 2026: all 35 `SKILL.md`, `.claude-plugin/plugin.json` (v1.2.3), `CHANGELOG.md`, `.agents/invocation.md`, `.agents/adr/0002`, `docs/`, `setup-matt-pocock-skills/issue-tracker-{github,gitlab,local}.md`, `.out-of-scope/question-limits.md`
- `github.com/earendil-works/pi` — full repo clone: `packages/coding-agent/docs/{extensions,skills,packages,sdk,prompt-templates}.md` (2,988-line extension reference), `examples/extensions/` (~75), all 10 workspace `package.json`
- npm registry API — `@earendil-works/pi-coding-agent` dist-tags and publish times (0.84.0, 2026-08-06)
- GitHub API — `mattpocock/skills` open issues sorted by reactions (292 open), issues #218/#623/#624/#785
- `pi.dev/packages` — the Package Catalog (5,430 packages, top 50 by downloads)

**Secondary:**
- Matt Pocock, `aihero.dev/skills` — per-skill docs for `wayfinder`, `setup-matt-pocock-skills`, `grill-me`; v1.1 and v1.2 changelogs
- Matt Pocock on X — v1.1 announcement (8 Jul), wayfinder breakdown (30 Jul), *"you are the one in charge"* on grill-me question volume
- Skillselion, *"Matt Pocock's skills, mapped"* — positioning quotes, install counts, deprecation history
- AI Jason (aibuilderclub), *"Pi Agent Extensions: Change the Harness, Not Just the Prompt"* — the hooks-vs-extensions ceiling argument, and its published self-correction on `updatedToolOutput`
- Pragmatic Engineer podcast — Zechner & Ronacher on Pi's design philosophy
- explainx.ai — Pi harness overview incl. the reported Databricks benchmark and Shopify `pi-autoresearch` case

**Reliability note:** several high-ranking secondary articles quote wildly inconsistent figures for the skills repo (48K, 135K, 176K stars; "40+ skills"; "7.5M downloads"). None matched the live repo. All counts in this report come from the GitHub and npm APIs read on 7 Aug 2026 — **~205K stars, ~17.7K forks, 35 skill files, 25 promoted** — and will drift.
