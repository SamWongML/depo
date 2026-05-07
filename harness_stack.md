Good catch — let me verify before assuming.​​​​​​​​​​​​​​​​

So Superpowers does have slash commands, but only three: /superpowers:brainstorm, /superpowers:write-plan, /superpowers:execute-plan. Now let me verify gstack.​​​​​​​​​​​​​​​​

You’re partly right, partly wrong, and the distinction matters for the design. Let me state what’s actually true based on the verification:
The factual correction
Both frameworks have what users type as slash commands. But they aren’t slash commands in the conventional sense — they’re skills the agent invokes. The subtlety:
	•	Superpowers ships exactly three slash commands (/superpowers:brainstorm, /superpowers:write-plan, /superpowers:execute-plan) and they all have disable-model-invocation: true, restricting them to manual user invocation only. The agent cannot invoke these commands via the Skill tool, preventing confusion since they just redirect to skills anyway. Commands serve as convenient aliases for frequently-used skills but are not required - agents primarily invoke skills autonomously based on task recognition ￼. Everything else (test-driven-development, systematic-debugging, verification-before-completion, requesting-code-review, etc.) is a skill, not a command — skills activate automatically when relevant. For example: test-driven-development activates when implementing features · systematic-debugging activates when debugging issues · verification-before-completion activates before claiming work is done ￼.
	•	gstack has no commands in the Claude/opencode plugin sense. The README and tutorials write things like /office-hours and /qa, but when the user’s request matches an available skill, invoke it via the Skill tool. When in doubt, invoke the skill ￼ — these are skill names dispatched through Claude’s Skill tool, not slash commands defined in a commands/ directory. The / is a user convention that gstack’s CLAUDE.md teaches the agent to interpret as “invoke the named skill.”
This changes a lot of the previous design. Several things I wrote — disable-model-invocation: true on gstack-plan-eng-review, blocking gstack roles via permission.task — were wrong because there are no agent-level commands or task targets to block. They’re skills loaded via the Skill tool, so the right control surface is different.
What this changes in the design
Five concrete corrections, in order of importance:
	1.	gstack roles aren’t subagents. They’re skills. So you can’t put them in permission.task. They’re loaded by the agent itself via the Skill tool, in the current context. That means: (a) no nested-context savings — calling /qa runs in the orchestrator’s context, not a fresh one; (b) you don’t gate them through permission.task; (c) you gate them by either renaming the SKILL.md description, removing the symlink, or scoping via gstack’s own config (gstack-config set proactive false).
	2.	Superpowers commands cannot be “denied” by permission.task. They’re slash commands the user types. Claude Code/opencode always honors /plugin install’s registered commands. To suppress, you put a project-level command file with the same name and disable-model-invocation: true — which is what I had — but the purpose is to override the user-typed command, not to block agent task delegation. The skills themselves auto-trigger from prompt context regardless.
	3.	The previous “lock down via permission.task glob” plan only works for genuine subagents (GSD’s gsd-executor, gsd-verifier, etc.). For Superpowers and gstack, the right enforcement levers are different.
	4.	gstack loads in the orchestrator’s context. Calling /autoplan from the GSD orchestrator runs the four reviewer skills in that context, not in fresh subagent contexts. This is actually fine for gstack’s design — the reviewers are short — but it means the token-economics math from the previous answer was off. Calling /autoplan is cheaper than I claimed (no fresh-context overhead) but eats into the orchestrator’s context budget. If you’re worried about orchestrator context rot, you instead invoke gstack via Task to a generic subagent that has the gstack skills available. That’s a real choice, not a bug.
	5.	Both frameworks rely on auto-triggering by prompt content. That means if your AGENTS.md describes the work in language that matches a Superpowers or gstack skill’s trigger phrases, the skill fires whether you wanted it to or not. Suppression has to be done at the skill description level, not at a permission level.
Improved design — the corrected enforcement model
The right control surface is now four layers, not one:



|Lever                                                                                           |What it controls                                                                     |Used for                                                                                                                                      |
|------------------------------------------------------------------------------------------------|-------------------------------------------------------------------------------------|----------------------------------------------------------------------------------------------------------------------------------------------|
|`permission.task` in `opencode.json`                                                            |Which **subagents** an agent can invoke via Task                                     |Locking down GSD subagents (`gsd-executor`, `gsd-verifier`, etc.) — these are real agents                                                     |
|Project-level command override (`.opencode/commands/*.md` with `disable-model-invocation: true`)|Suppressing **user-typed slash commands** that aren’t appropriate for autonomous mode|Disabling `/superpowers:write-plan`, `/superpowers:execute-plan`, `/superpowers:brainstorm` so they don’t run during autonomous loop          |
|Skill availability (filesystem)                                                                 |Which **skills** the agent’s Skill tool can see                                      |Removing or renaming gstack skills that duplicate GSD’s machinery (e.g., delete the symlink for `~/.claude/skills/gstack/plan-eng-review/`)   |
|GSD’s `agent_skills` injection                                                                  |Which skills are *force-loaded* into a specific GSD subagent’s prompt                |Pinning `test-driven-development`, `verification-before-completion`, `cso`, `plan-eng-review` into the right GSD subagents so they always fire|

That’s the corrected model. Everything follows from it.
The corrected install + suppression script

# 1. Install GSD (unchanged)
npx get-shit-done-cc@1.39.0 --opencode --global

# 2. Install Superpowers via opencode plugin
opencode plugin install superpowers

# 3. Override Superpowers' three slash commands so they don't run autonomously.
#    These are user-facing commands; we want manual-only OR fully off in auto mode.
mkdir -p ~/.config/opencode/commands/superpowers
for cmd in brainstorm write-plan execute-plan; do
cat > ~/.config/opencode/commands/superpowers/$cmd.md <<EOF
---
description: "[manual-only] not used in autonomous loop. GSD owns plan/exec."
disable-model-invocation: true
---
This command is disabled for autonomous runs in this stack.
For greenfield kickoff only, you (the human) may invoke /superpowers:brainstorm directly.
After kickoff, see AGENTS.md decision tree.
EOF
done

# 4. Install gstack with prefix so skill names become /gstack-* (avoids conflicts)
git clone --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack
~/.claude/skills/gstack/setup --prefix              # forces /gstack-* skill names
~/.claude/skills/gstack/bin/gstack-config set proactive false   # only fire when explicitly invoked

# 5. Suppress the gstack skills that duplicate GSD's machinery, by removing
#    their availability from the agent's Skill tool view. We do this by
#    renaming SKILL.md so it stops being discoverable, AND keep a copy
#    elsewhere for agent_skills injection.
GSTACK=~/.claude/skills/gstack
mkdir -p "$GSTACK/.injected-only"
for skill in plan-ceo-review plan-eng-review plan-design-review plan-devex-review review investigate cso; do
  if [ -d "$GSTACK/$skill" ]; then
    cp "$GSTACK/$skill/SKILL.md" "$GSTACK/.injected-only/${skill}-SKILL.md"
    mv "$GSTACK/$skill/SKILL.md" "$GSTACK/$skill/SKILL.md.disabled"
  fi
done

# Now these skills are NOT discoverable by the orchestrator's Skill tool,
# but ARE available for agent_skills injection by absolute path.

# 6. Pin everything
cat > .opencode/versions.lock <<EOF
opencode=$(opencode --version)
gsd=1.39.0
superpowers=$(cat ~/.claude/plugins/superpowers/.claude-plugin/plugin.json | jq -r .version)
gstack=$(git -C ~/.claude/skills/gstack rev-parse HEAD)
EOF


The key trick in step 5: by renaming SKILL.md to SKILL.md.disabled, the skill becomes invisible to the agent’s auto-discovery — Claude/opencode only enumerates skills with a valid SKILL.md. But the file content remains on disk for agent_skills injection where we explicitly want it.
The corrected .planning/config.json
The agent_skills paths now point at the actual on-disk locations, not at “skill names” that the runtime would auto-trigger:

{
  "version": "1.39",
  "workflow": {
    "tdd_mode": true,
    "auto_advance": true,
    "research": true,
    "plan_checker": true,
    "verifier": true,
    "parallelization": { "enabled": true, "max_wave_size": 3 },

    "agent_skills": {
      "gsd-phase-researcher": [
        "global:gstack/.injected-only/cso-SKILL.md",
        "global:gstack/.injected-only/plan-ceo-review-SKILL.md"
      ],
      "gsd-planner": [
        "global:superpowers/skills/writing-plans/SKILL.md",
        "global:gstack/.injected-only/plan-eng-review-SKILL.md"
      ],
      "gsd-plan-checker": [
        "global:superpowers/skills/verification-before-completion/SKILL.md"
      ],
      "gsd-executor": [
        "global:superpowers/skills/test-driven-development/SKILL.md",
        "global:superpowers/skills/systematic-debugging/SKILL.md",
        "global:superpowers/skills/verification-before-completion/SKILL.md"
      ],
      "gsd-verifier": [
        "global:superpowers/skills/requesting-code-review/SKILL.md",
        "global:superpowers/skills/verification-before-completion/SKILL.md",
        "global:gstack/.injected-only/review-SKILL.md"
      ],
      "gsd-debugger": [
        "global:superpowers/skills/systematic-debugging/SKILL.md",
        "global:gstack/.injected-only/investigate-SKILL.md"
      ]
    },

    "context": {
      "include_project_claude_md": true,
      "include_codebase_map": true
    }
  },

  "model_profile": "balanced",
  "model_overrides": {
    "gsd-planner":          "anthropic/claude-opus-4-7",
    "gsd-plan-checker":     "anthropic/claude-opus-4-7",
    "gsd-phase-researcher": "anthropic/claude-sonnet-4-6",
    "gsd-executor":         "anthropic/claude-sonnet-4-6",
    "gsd-verifier":         "anthropic/claude-opus-4-7",
    "gsd-debugger":         "anthropic/claude-opus-4-7"
  },

  "verification_commands": {
    "backend":  ["uv run pytest -x", "uv run ruff check .", "uv run mypy ."],
    "frontend": ["pnpm typecheck", "pnpm lint", "pnpm test --run", "pnpm playwright test"]
  },
  "verification_auto_fix":  true,
  "verification_max_retries": 3,

  "milestone_gates": {
    "require_autoplan_pass":     true,
    "require_plan_checker_pass": true,
    "require_qa_when_ui_scope":  true
  },

  "features": {
    "playwright_mcp_verify": true,
    "research_gate":         true
  }
}


The corrected opencode.json agent block
The old permission.task denies for gstack and Superpowers come out, because those aren’t task targets:

{
  "agent": {
    "gsd-orchestrator": {
      "mode": "primary",
      "model": "anthropic/claude-opus-4-7",
      "temperature": 0.2,
      "permission": {
        "task": {
          // GSD's real subagents (these are the only legitimate Task targets)
          "gsd-map-codebase":     "allow",
          "gsd-phase-researcher": "allow",
          "gsd-planner":          "allow",
          "gsd-plan-checker":     "allow",
          "gsd-spike":            "allow",
          "gsd-executor":         "allow",
          "gsd-verifier":         "allow",
          "gsd-debugger":         "allow",
          "*": "deny"
        }
      }
    },
    // GSD subagents — unchanged from previous answer
    "gsd-executor":  { /* ...same... */ },
    "gsd-verifier":  { /* ...same... */ },
    // No agent entries for gstack-* or superpowers-*; they aren't agents.
  }
}


Notice all the gstack-* and superpowers-* entries are gone from the agent map. They never belonged there. The earlier design treated them as agents because that’s how the conversation framed it — but they’re skills, and the right place to control them is filesystem visibility + agent_skills injection.
The decision tree, corrected
AGENTS.md:

## How the stack actually works

Three layers of mechanism, each used for what it's good at:

1. **GSD subagents** (real agents): `gsd-orchestrator`, `gsd-executor`, `gsd-verifier`, etc.
   Each runs in a fresh context, dispatched via Task. Locked down via `permission.task`.

2. **Skills** (always run in the dispatcher's context): Superpowers methodology files,
   gstack reviewer files. Loaded into a specific GSD subagent's prompt via
   `.planning/config.json` `agent_skills`, by absolute SKILL.md path.

3. **Slash commands** (user-typed only): `/superpowers:brainstorm`,
   `/superpowers:write-plan`, `/superpowers:execute-plan` — disabled in autonomous mode
   via project-level overrides. The human may type them at greenfield kickoff;
   the agent never invokes them autonomously.

## Decision tree (autonomous mode)

ON kickoff (HUMAN runs ONE branch manually):
  greenfield: human types `/superpowers:brainstorm` → produces docs/specs/<topic>.md
  brownfield: human runs `/gsd-map-codebase` → produces .planning/codebase/

ON `/gsd-new-project`:
  GSD reads whichever artifact exists, produces .planning/ROADMAP.md.

ON `bash bin/run-autonomous.sh`:
  GSD's /gsd-autonomous loops every phase. Inside each phase:

  /gsd-discuss-phase N
    └ gsd-phase-researcher loaded with cso + plan-ceo-review skills (injected)
    └ produces CONTEXT.md

  IF unknowns flagged: /gsd-spike
  IF UI scope AND no DESIGN.md: gstack /design-consultation skill
    (the orchestrator invokes this skill explicitly via the Skill tool —
     it's allowed because design-consultation isn't disabled)

  /gsd-plan-phase N
    └ gsd-planner loaded with writing-plans + plan-eng-review skills (injected)
    └ produces PLAN.md

  Orchestrator invokes /gstack-autoplan skill (one chained call)
    └ appends GSTACK REVIEW REPORT to PLAN.md
    └ this skill IS available; only the four individual reviewers were disabled

  gsd-plan-checker
    └ loaded with verification-before-completion (injected)
    └ produces PASS/FAIL

  /gsd-execute-phase N
    └ gsd-executor loaded with TDD + systematic-debugging + verification (injected)

  /gsd-verify-work N
    └ gsd-verifier loaded with requesting-code-review + verification + gstack-review (injected)

  IF UI scope: orchestrator invokes /gstack-qa skill
  IF release:  orchestrator invokes /gstack-document-release skill

NEVER invoke (because they're suppressed at the filesystem layer):
  /plan-ceo-review, /plan-eng-review, /plan-design-review, /plan-devex-review,
  /review (gstack), /investigate (gstack), /cso (gstack)
  — these are `injected-only` and not discoverable by the Skill tool.

NEVER invoke autonomously (because they have disable-model-invocation):
  /superpowers:brainstorm, /superpowers:write-plan, /superpowers:execute-plan
  — humans may type these manually at kickoff; the agent never invokes them.


Verification commands, corrected

# 1. GSD's real subagents are present
opencode agent list
# Expect: gsd-orchestrator (primary), all gsd-* subagents (hidden).
# No gstack-* or superpowers-* entries — those aren't agents.

# 2. The disabled gstack skills are NOT discoverable
ls ~/.claude/skills/gstack/plan-ceo-review/SKILL.md*
# Expect: SKILL.md.disabled (not SKILL.md)

# 3. The active gstack skills ARE discoverable
ls ~/.claude/skills/gstack/{autoplan,qa,document-release,design-consultation}/SKILL.md
# Expect: all present

# 4. Injection paths exist
ls ~/.claude/skills/gstack/.injected-only/
# Expect: cso-SKILL.md, plan-ceo-review-SKILL.md, plan-eng-review-SKILL.md, etc.

# 5. The Superpowers commands are suppressed in autonomous mode
opencode run --agent gsd-orchestrator "Try to invoke /superpowers:write-plan"
# Expect: command resolves to disabled override; no plan written.

# 6. The injection actually works inside a GSD subagent
opencode run --agent gsd-executor "Quote the first line of your TDD skill."
# Expect: actual first line from superpowers/skills/test-driven-development/SKILL.md.

# 7. The orchestrator can still call /gstack-autoplan via the Skill tool
opencode run --agent gsd-orchestrator "Run /gstack-autoplan on the current PLAN.md"
# Expect: review report appended.


What I got wrong before, summarized
	1.	I treated gstack skills and Superpowers skills as if they were opencode agents with permission.task entries. They aren’t — gstack has no agents at all, and Superpowers’ three slash commands are user-facing aliases that auto-disable model invocation.
	2.	I wrote a permission.task deny list including gstack-plan-eng-review and superpowers-writing-plans as if these were Task targets. They’re skills.
	3.	The token-economics table assumed gstack reviewers run in fresh subagent contexts. They don’t — they run in whatever context invoked them. Calling them via Task in a fresh subagent IS possible (and is what the previous CI gate was effectively doing) but it’s a deliberate choice, not the default.
	4.	The “gstack-orchestrator” topology was wrong: there is no gstack orchestrator agent. There’s a CLAUDE.md/AGENTS.md routing rule plus a SKILL.md with descriptions that auto-trigger.
What stays correct
	•	The role split (GSD = lifecycle, Superpowers = methodology, gstack = strategic gates).
	•	No external Ralph loop — GSD’s /gsd-autonomous is sufficient.
	•	Back-pressure as opencode plugins (autonomy-guards.ts).
	•	The supervisor as a 30-line bash script.
	•	agent_skills injection as the way methodology rides into GSD subagents.
	•	The decision tree’s content (just not the enforcement mechanism).
Caveat: the agent_skills config schema in GSD currently accepts skill names like global:superpowers/skills/test-driven-development (without the /SKILL.md suffix). Both forms appear in the GSD docs across versions; v1.39 favors the path-with-SKILL.md form. Run gsd config validate after editing — if it complains, drop /SKILL.md from the entries. The injection effect is the same either way.​​​​​​​​​​​​​​​​