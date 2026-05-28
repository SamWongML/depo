Here’s the deep research, ending with a drop-in config you can copy.

How Claude Code’s exploration actually works

Two things give Claude Code its repo-exploration edge, and only one of them is the “Explore” subagent itself:

	1.	A dedicated read-only Explore agent. It runs on Haiku by default, has access only to Glob, Grep, Read, and read-only Bash (ls, find, cat, head, tail, git status/log/diff), and is denied any write tool. The Explore type handles read-only file discovery and codebase search, running on Haiku by default for speed and cost. ￼ Its system prompt is short, sharply read-only, and tells it to spawn parallel tool calls for grepping and reading files ￼ and to return findings as a single message (no file writes).
	2.	Aggressive routing from the parent. The main agent is instructed, when exploring the codebase to gather context or to answer a question that is not a needle query for a specific file/class/function, it is CRITICAL that you use the Task tool with subagent_type=Explore instead of running search commands directly ￼. So Explore gets called constantly, not only when you ask. Combined with thoroughness levels: quick for targeted lookups, medium for balanced exploration, or very thorough for comprehensive analysis ￼, this keeps the parent’s context lean while the subagent burns its own tokens on noisy grep/read output.

The performance comes from three properties: (a) a fresh context window per exploration so the parent never sees raw search output, (b) a model/effort tuned for cheap, fast read-heavy work, and (c) a parent that’s been told to prefer delegation over inline search.

What Codex actually gives you

Codex has shipped a real subagent system with a built-in explorer aimed at the same job. From the current docs: Codex ships with built-in agents: default: general-purpose fallback agent. worker: execution-focused agent for implementation and fixes. explorer: read-heavy codebase exploration agent. ￼

But there are two important differences from Claude Code that you must work around:

	•	No auto-routing. Codex doesn’t spawn subagents automatically, and it should only use subagents when you explicitly ask for subagents or parallel agent work. ￼ If you just say “explain this repo,” Codex will grep inline like a single agent. You have to tell it to delegate, or bake that instruction into AGENTS.md.
	•	Custom agents are TOML, not Markdown. To define your own custom agents, add standalone TOML files under ~/.codex/agents/ for personal agents or .codex/agents/ for project-scoped agents. Every standalone custom agent file must define: name, description, developer_instructions. ￼ Optional keys include nickname_candidates, model, model_reasoning_effort, sandbox_mode, mcp_servers, and skills.config ￼, and if a custom agent name matches a built-in agent such as explorer, your custom agent takes precedence. ￼

So the strategy is: override the built-in explorer with a Claude-Code-Explore-shaped TOML, and make AGENTS.md route to it the way Claude Code’s main system prompt does.

The model/effort lever

This is where you actually get parity (or beat it). Codex’s own guidance: Use gpt-5.4-mini for agents that favor speed and efficiency over depth, such as exploration, read-heavy scans, large-file review, or processing supporting documents. It works well for parallel workers that return distilled results to the main agent. ￼ Pair that with low-to-medium reasoning effort. Keep gpt-5.5 at high effort for the parent who synthesizes the explorer’s reports.

Drop-in configuration

Put this in your repo (or ~/.codex/ for a global setup):

.codex/config.toml

[agents]
max_threads = 8          # how many parallel explorers
max_depth = 1            # explorers can't recursively spawn
job_max_runtime_seconds = 600


max_threads defaults to 6 and max_depth to 1; raising depth is rarely worth it because raising this value can turn broad delegation instructions into repeated fan-out, which increases token usage, latency, and local resource consumption. ￼

.codex/agents/explorer.toml (overrides the built-in)

name = "explorer"
description = """
Read-only codebase explorer. Use for any request that needs to understand,
locate, or summarize code rather than modify it: architecture overviews,
finding all call sites of a symbol, mapping a feature's execution path,
locating where a config value is read, or surveying tests for a module.
Do NOT use for needle lookups when the exact file path is already known —
the parent should just Read that file directly.
"""
model = "gpt-5.4-mini"
model_reasoning_effort = "medium"
sandbox_mode = "read-only"
nickname_candidates = ["Scout", "Cartographer", "Ranger", "Surveyor"]

developer_instructions = """
You are a file-search and codebase-exploration specialist. You excel at
quickly navigating unfamiliar repositories and returning precise,
evidence-backed reports to a parent agent.

=== READ-ONLY MODE — STRICT ===
You are STRICTLY PROHIBITED from:
- Creating, editing, deleting, moving, or copying any file
- Using shell redirection (>, >>, |, heredocs) to write to files
- Running any command that mutates the working tree, index, or environment
  (no git add/commit/push, no package installs, no mkdir/touch/rm/mv/cp)
- Writing scratch files to /tmp or anywhere else

Allowed shell commands are read-only only: ls, find, cat, head, tail, wc,
file, which, pwd, echo, git status, git log, git diff, git show, git
ls-files, rg, grep.

=== WORKING STYLE ===
- Default to ripgrep (`rg`) and glob patterns over `find`/`grep`; they are
  faster and respect .gitignore.
- Spawn PARALLEL tool calls aggressively. When you need to check five
  hypotheses, issue five searches in one turn, then read the top hits in
  parallel. Latency, not tokens, is your scarcest resource.
- Read entry points first (package.json, pyproject.toml, Cargo.toml,
  go.mod, main.*, src/index.*, cmd/*, app/*) before deep-diving.
- Follow imports/requires/uses to map call graphs. Cite file:line for
  every concrete claim.
- If the request specifies a thoroughness level — "quick", "medium",
  or "very thorough" — calibrate accordingly:
    * quick   = answer in ≤ 10 tool calls, one focused area
    * medium  = ≤ 30 tool calls, cover the obvious neighbors
    * thorough = exhaustive; trace every relevant code path and
                 cross-reference tests, configs, and docs
- Skip AGENTS.md and parent CLAUDE.md-style context. You are a fresh
  context for a reason; don't reload session state.

=== OUTPUT CONTRACT ===
Return a SINGLE final message (do not create files). Structure it as:

1. **Direct answer** (2–4 sentences) — what the parent actually asked.
2. **Evidence** — bulleted file:line citations with one-line excerpts,
   ABSOLUTE paths. Group by concern.
3. **Mental model** — a short paragraph the parent can paste into its
   own context: entry points, key abstractions, data flow.
4. **Open questions / dead ends** — anything you could not verify, so
   the parent knows what to probe next.

Be concise. The parent is paying tokens for everything you return; only
include what helps it act. Avoid emojis and decorative formatting.
"""


This mirrors the Piebald-extracted Claude Code Explore prompt almost line-for-line, with two upgrades: the explicit output contract (Claude Code’s version is looser) and prefering rg (ripgrep), which is materially faster than recursive grep on large repos.

Optional companion: a “needle finder” for the case where Explore’s broader prompt is overkill:

# .codex/agents/needle.toml
name = "needle"
description = """
Surgical lookup agent. Use when the parent knows what it's looking for
(a symbol, a string, a specific file pattern) and just needs the
location(s) and immediate surrounding context. Returns in seconds.
"""
model = "gpt-5.4-mini"
model_reasoning_effort = "low"
sandbox_mode = "read-only"

developer_instructions = """
Answer one needle question. One or two rg/glob calls, read the matches,
report file:line plus 5 lines of context per hit. No architecture
prose, no recommendations. If the search returns nothing, say so and
suggest two alternative queries.
"""


Make the parent actually delegate

This is the step most people skip, and it’s why their Codex feels worse than Claude Code despite having subagents available. Add this to your repo’s AGENTS.md:

## Codebase exploration policy

When a task requires understanding, locating, or summarizing existing
code (as opposed to modifying a known file), DELEGATE to the `explorer`
subagent rather than running grep/find/read inline. This is critical
even when the answer feels close at hand — keeping search noise off the
main thread directly improves the quality of the plan and patch that
follow.

Use `explorer` for:
- "How does X work?" / "Where is Y handled?"
- Architecture or data-flow questions
- Finding all call sites of a function, or all consumers of a config key
- Surveying tests, fixtures, or migrations before changing them

Use `needle` (not `explorer`) for:
- Single-symbol or single-string lookups where you already know the
  approximate area of the codebase

Skip subagents only for:
- A specific file path the user has already named
- Trivial one-shot reads (< 3 files, no search required)

When invoking `explorer`, always specify a thoroughness level in the
prompt ("quick", "medium", or "very thorough") and the exact question
you need answered. For broad surveys, spawn multiple explorers in
parallel — one per subsystem or concern — and wait for all of them
before synthesizing.


Codex picks up AGENTS.md automatically as Codex discovers project configuration (for example, .codex/ layers and AGENTS.md) by walking up from the working directory until it reaches a project root ￼, so this gets injected into every session in that repo.

The parallel-fanout pattern that closes the gap

Claude Code’s biggest exploration wins come not from one Explore call but from parallel explore agents ￼ hitting different subsystems at once. Codex supports the same pattern; you just have to ask. A prompt template that consistently works:

Before we change anything, I want a clean mental model of this repo.
Spawn 4 explorer subagents in parallel, each with thoroughness=medium:

  1. Entry points and request/response flow (HTTP handlers → services → data)
  2. Persistence: schema, migrations, ORM layer, transaction boundaries
  3. Auth and authorization: where identity is established and checked
  4. Testing: structure, fixtures, what's covered vs. mocked

Wait for all four. Then produce a single consolidated architecture
summary with file:line citations, and list the three highest-leverage
files to read next.


Because Codex handles orchestration across agents, including spawning new subagents, routing follow-up instructions, waiting for results, and closing agent threads. When many agents are running, Codex waits until all requested results are available, then returns a consolidated response ￼, this gives you the same “fan out, distill, return” shape Claude Code uses internally.

Performance-tuning checklist

A few non-obvious knobs that materially close the gap:

	•	Pin model = "gpt-5.4-mini" on explorer. Don’t let it inherit gpt-5.5 from the parent; you’ll pay for reasoning you don’t need on grep-heavy work.
	•	model_reasoning_effort = "medium" on explorer, "low" on needle. High effort on a read-only agent mostly wastes time staring at code.
	•	sandbox_mode = "read-only" is non-optional. It both prevents accidents and lets you --ask-for-approval never the explorer without risk.
	•	Install ripgrep (brew install ripgrep / apt install ripgrep). The explorer prompt biases toward rg because it’s 5–20× faster than recursive grep on real repos.
	•	Raise agents.max_threads to 8 if your repo is big and you fan out often; the default of 6 throttles broad surveys.
	•	Keep agents.max_depth = 1. Nested explorers rarely help and burn tokens unpredictably.
	•	Use /agent in the CLI to inspect a running explorer if it’s taking too long, and steer or kill it without losing the parent’s thread.
	•	Don’t let explorer write reports to disk. The output contract above keeps results as a single message, which is how Claude Code’s Explore works and why its context savings are real. Letting it > notes.md defeats the purpose.

With this setup, repo exploration in Codex behaves like Claude Code’s: the parent stays on gpt-5.5 at high effort focused on planning and patching, while cheap-and-fast gpt-5.4-mini explorers fan out, return distilled file:line evidence, and never pollute the main thread.