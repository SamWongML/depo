I have everything I need. Critical findings to ground the final report on:
	•	GSD v1.39 (current as of v1) shipped Research gate — Blocks planning when RESEARCH.md has unresolved open questions ￼ and Playwright-MCP automated UI verification — Optional visual verification in verify-phase ￼. These eliminate two more gstack commands from being needed standalone.
	•	v1.39 also deleted 31 micro-skills… All functionality preserved via flags on consolidated skills ￼, so the surface area is smaller and cleaner than what I described before.
	•	For OpenCode specifically, there’s a maintained fork (rokicool/gsd-opencode) that removed task() calls, since they are not supported by OpenCode and replaced them with the direct call to an agent ￼ — this matters for the autonomous mode to work correctly on opencode.
Here is the final report.

Final Production-Grade Harness Stack
Executive summary



|Layer          |Owner                                     |What it provides                                                                                                                                                                                                        |What is disabled                                                                               |
|---------------|------------------------------------------|------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|-----------------------------------------------------------------------------------------------|
|Runtime        |opencode                                  |Hooks, permissions, plugin model                                                                                                                                                                                        |n/a                                                                                            |
|Lifecycle spine|GSD v1.39 (`get-shit-done-cc`)            |Discuss → Plan → Plan-check → Execute → Verify → Ship → next milestone, all autonomously, with fresh contexts per task                                                                                                  |n/a                                                                                            |
|Methodology    |Superpowers (skills only, not commands)   |TDD, systematic debugging, verification-before-completion, code review discipline                                                                                                                                       |`write-plan`, `execute-plan`, `subagent-driven-development`, `brainstorming` (after kickoff)   |
|Strategic gates|gstack (one-shot only)                    |Greenfield problem framing (`/office-hours`), scope optimization (`/cso`), strategic plan review (`/autoplan`), browser QA (`/qa`), release docs (`/document-release`), design system bootstrap (`/design-consultation`)|The 4 individual reviewers (`plan-{ceo,eng,design,devex}-review`) — only `/autoplan` calls them|
|Outer loop     |None — GSD’s `/gsd-autonomous` is the loop|Walk-away autonomy across the whole milestone                                                                                                                                                                           |Ralph external loops                                                                           |
|Supervisor     |thin bash watchdog                        |Restart on idle/crash; stop on milestone seal                                                                                                                                                                           |n/a                                                                                            |

The whole insight: GSD’s autonomous mode is already a properly-engineered Ralph loop with state management. Adding another loop on top would create nested dispatchers and competing state machines. What we add instead is the back-pressure Huntley actually argued for — pre-commit guards, automatic verification, deterministic restart on failure — implemented as opencode plugins, not as another orchestrator.
Part 1 — The corrected role split, with reasoning
The original split you proposed had two redundancies that the research surfaced:
	1.	gstack for planning AND qa overlapped with GSD’s planning machinery (both produce a plan; one is XML-structured for wave dispatch, the other isn’t).
	2.	superpowers for TDD execution overlapped with GSD’s executor (both dispatch subagents per task).
The corrected split:
	•	GSD owns lifecycle — the only dispatcher of subagents. Commands: /gsd-new-project, /gsd-discuss-phase, /gsd-plan-phase, /gsd-execute-phase, /gsd-verify-work, /gsd-ship, /gsd-complete-milestone, and the autonomous loop /gsd-autonomous.
	•	Superpowers owns methodology — five skill files (no commands) injected into GSD’s existing subagents via agent_skills: test-driven-development, systematic-debugging, verification-before-completion, requesting-code-review, writing-plans (used as a thinking template inside gsd-planner, not a parallel command).
	•	gstack owns strategic gates — six commands invoked as one-shot gates at the right point in the GSD loop: /office-hours (greenfield kickoff only), /gstack-cso (scope check, optional), /gstack-autoplan (chained CEO+Eng+Design+DevEx review), /gstack-qa (browser E2E when UI scope), /gstack-document-release (release time), /gstack-design-consultation (once per project for frontend).
Everything else from gstack and Superpowers is deliberately disabled. That’s what eliminates the duplication.
Part 2 — Why no Ralph loop
Five reasons, ordered by severity:
	1.	GSD already is one. /gsd:autonomous –only N — Execute a single phase autonomously (discuss→plan→execute) ￼, with a milestone-wide variant that loops every phase. It satisfies Ralph’s three core properties: fresh-context-per-task, persistent state on disk, restart-from-state semantics.
	2.	Two state machines fight. Ralph’s IMPLEMENTATION_PLAN.md would diverge from GSD’s .planning/ROADMAP.md + STATE.md. One would silently win and corrupt the other.
	3.	Nested-session collisions are real. GSD’s own auto-advance hit this exact bug: Error: Claude Code cannot be launched inside another Claude Code session. Nested sessions share runtime resources ￼. Putting Ralph outside an opencode session running GSD inside reproduces it.
	4.	Atomicity is lost. GSD commits per plan with structured SUMMARY.md. Ralph commits per iteration. Mixing the two destroys the git history GSD relies on for resume-from-failure.
	5.	The actual Ralph value is back-pressure, not the loop. Huntley’s own argument: you don’t provision write secrets. You introduce tests, enable change data capture, and rely on audit logs. You engineer your way out of failure scenarios… The more back pressure you capture, the more autonomy you can grant ￼. We capture that back-pressure via opencode hooks (Part 7). That’s the production-grade move.
The one Ralph idea worth keeping is the supervisor: a tiny outer process that restarts the inner orchestrator on idle/crash and stops on milestone completion. That’s not a Ralph loop — it’s just systemd for an agent. We add it as a 30-line bash script.
Part 3 — Install order (idempotent)

# 1. opencode runtime
brew install anomalyco/tap/opencode

# 2. GSD v1, pinned. Installs /gsd-* commands and .planning/ scaffolding.
#    Use the OpenCode-aware fork's runtime patches; it tracks v1 of upstream.
npx get-shit-done-cc@1.39.0 --opencode --global
#    or, for the OpenCode-specific fork that fixes Task() invocation on opencode:
#    npx gsd-opencode@latest --global

# 3. Superpowers — install via opencode's plugin mechanism, then disable
#    the commands that conflict with GSD's spine.
opencode plugin install superpowers

mkdir -p ~/.config/opencode/commands/superpowers
for cmd in write-plan execute-plan subagent-driven-development brainstorming; do
  cat > ~/.config/opencode/commands/superpowers/$cmd.md <<EOF
---
description: "[disabled] use the GSD equivalent — see AGENTS.md decision tree"
disable-model-invocation: true
---
This command is disabled in this stack. See AGENTS.md.
EOF
done

# 4. gstack with prefix so /review etc. become /gstack-review and don't collide
git clone --depth 1 https://github.com/garrytan/gstack.git ~/.claude/skills/gstack
~/.claude/skills/gstack/bin/gstack-config set skill_prefix true
~/.claude/skills/gstack/setup --host opencode

# 5. Disable the gstack commands that would duplicate GSD's chain
for cmd in plan-ceo-review plan-eng-review plan-design-review plan-devex-review review investigate; do
  cat > ~/.config/opencode/commands/gstack-${cmd}.md <<EOF
---
description: "[disabled] absorbed into /gstack-autoplan or injected as skill"
disable-model-invocation: true
---
Disabled. See AGENTS.md.
EOF
done

# 6. Pin everything in the project so teammates get reproducible installs
cat > .opencode/versions.lock <<EOF
opencode=1.x
gsd=1.39.0
superpowers=$(opencode plugin show superpowers --version)
gstack=$(git -C ~/.claude/skills/gstack rev-parse HEAD)
EOF


Part 4 — ~/.config/opencode/opencode.jsonc — base topology

{
  "$schema": "https://opencode.ai/config.json",
  "model": "anthropic/claude-opus-4-7",
  "autoupdate": false,
  "share": "manual",

  "agent": {
    "gsd-orchestrator": {
      "mode": "primary",
      "model": "anthropic/claude-opus-4-7",
      "temperature": 0.2,
      "description": "Sole primary. Owns lifecycle. Dispatches GSD subagents only.",
      "permission": {
        "task": {
          // GSD's own subagents — these carry methodology via agent_skills
          "gsd-map-codebase":     "allow",
          "gsd-phase-researcher": "allow",
          "gsd-planner":          "allow",
          "gsd-plan-checker":     "allow",
          "gsd-spike":            "allow",
          "gsd-executor":         "allow",
          "gsd-verifier":         "allow",
          "gsd-debugger":         "allow",

          // Superpowers — kickoff-only Socratic spec. Skip on brownfield.
          "superpowers-brainstorming": "allow",

          // gstack — strategic, one-shot gates only
          "gstack-office-hours":         "allow",
          "gstack-cso":                  "allow",
          "gstack-autoplan":             "allow",
          "gstack-qa":                   "allow",
          "gstack-document-release":     "allow",
          "gstack-design-consultation":  "allow",

          // Hard denies — running these would duplicate GSD's loop
          "superpowers-writing-plans":              "deny",
          "superpowers-subagent-driven-development":"deny",
          "superpowers-executing-plans":            "deny",
          "gstack-plan-ceo-review":     "deny",
          "gstack-plan-eng-review":     "deny",
          "gstack-plan-design-review":  "deny",
          "gstack-plan-devex-review":   "deny",
          "gstack-review":              "deny",
          "gstack-investigate":         "deny",

          "*": "deny"
        }
      }
    },

    // GSD subagents — kept thin. Methodology rides via .planning/config.json
    "gsd-executor": {
      "mode": "subagent", "hidden": true,
      "model": "anthropic/claude-sonnet-4-6",
      "permission": { "task": { "*": "deny" }, "edit": { "*": "allow" }, "write": { "*": "allow" } }
    },
    "gsd-verifier": {
      "mode": "subagent", "hidden": true,
      "model": "anthropic/claude-opus-4-7",
      "permission": { "task": { "*": "deny" }, "edit": { "*": "deny" }, "write": { "*": "deny" } }
    },
    "gsd-debugger": {
      "mode": "subagent", "hidden": true,
      "model": "anthropic/claude-opus-4-7",
      "permission": { "task": { "*": "deny" } }
    },
    "gsd-planner": {
      "mode": "subagent", "hidden": true,
      "model": "anthropic/claude-opus-4-7",
      "permission": {
        "task":  { "gsd-phase-researcher": "allow", "*": "deny" },
        "edit":  { ".planning/**": "allow", "*": "deny" },
        "write": { ".planning/**": "allow", "*": "deny" }
      }
    },
    "gsd-plan-checker": {
      "mode": "subagent", "hidden": true,
      "model": "anthropic/claude-opus-4-7",
      "permission": { "task": { "*": "deny" }, "edit": { "*": "deny" }, "write": { "*": "deny" } }
    },
    "gsd-phase-researcher": {
      "mode": "subagent", "hidden": true,
      "model": "anthropic/claude-sonnet-4-6",
      "permission": {
        "task":  { "*": "deny" },
        "edit":  { ".planning/**": "allow", "*": "deny" },
        "write": { ".planning/**": "allow", "*": "deny" }
      }
    },
    "gsd-map-codebase": {
      "mode": "subagent", "hidden": true,
      "model": "anthropic/claude-sonnet-4-6",
      "permission": {
        "task":  { "gsd-phase-researcher": "allow", "*": "deny" },
        "edit":  { ".planning/**": "allow", "*": "deny" },
        "write": { ".planning/**": "allow", "*": "deny" }
      }
    },
    "gsd-spike": {
      "mode": "subagent", "hidden": true,
      "model": "anthropic/claude-sonnet-4-6",
      "permission": {
        "edit":  { ".planning/spikes/**": "allow", "*": "deny" },
        "write": { ".planning/spikes/**": "allow", "*": "deny" }
      }
    },

    // Superpowers brainstorming — kickoff only
    "superpowers-brainstorming": {
      "mode": "subagent", "hidden": true,
      "model": "anthropic/claude-opus-4-7",
      "permission": {
        "task":  { "*": "deny" },
        "edit":  { "docs/specs/**": "allow", ".planning/**": "allow", "*": "deny" },
        "write": { "docs/specs/**": "allow", ".planning/**": "allow", "*": "deny" }
      }
    },

    // gstack one-shot gates
    "gstack-office-hours": {
      "mode": "subagent", "hidden": true,
      "model": "anthropic/claude-opus-4-7",
      "permission": {
        "task":  { "*": "deny" },
        "edit":  { "docs/specs/**": "allow", "*": "deny" },
        "write": { "docs/specs/**": "allow", "*": "deny" }
      }
    },
    "gstack-cso": {
      "mode": "subagent", "hidden": true,
      "model": "anthropic/claude-opus-4-7",
      "permission": {
        "task":  { "*": "deny" },
        "edit":  { ".planning/**": "allow", "*": "deny" },
        "write": { ".planning/**": "allow", "*": "deny" }
      }
    },
    "gstack-autoplan": {
      "mode": "subagent", "hidden": true,
      "model": "anthropic/claude-opus-4-7",
      "permission": {
        "task":  { "*": "deny" },
        "edit":  { ".planning/**": "allow", "*": "deny" },
        "write": { ".planning/**": "allow", "*": "deny" }
      }
    },
    "gstack-qa": {
      "mode": "subagent", "hidden": true,
      "model": "anthropic/claude-opus-4-7",
      "permission": {
        "edit":  { "tests/e2e/**": "allow", "*": "deny" },
        "write": { "tests/e2e/**": "allow", "*": "deny" }
      }
    },
    "gstack-document-release": {
      "mode": "subagent", "hidden": true,
      "model": "anthropic/claude-sonnet-4-6",
      "permission": {
        "edit":  { "docs/**": "allow", "CHANGELOG.md": "allow", "README.md": "allow", "*": "deny" },
        "write": { "docs/**": "allow", "CHANGELOG.md": "allow", "*": "deny" }
      }
    },
    "gstack-design-consultation": {
      "mode": "subagent", "hidden": true,
      "model": "anthropic/claude-opus-4-7",
      "permission": {
        "task":  { "*": "deny" },
        "edit":  { "docs/design/**": "allow", "DESIGN.md": "allow", "*": "deny" },
        "write": { "docs/design/**": "allow", "DESIGN.md": "allow", "*": "deny" }
      }
    }
  },

  "permission": {
    "edit":  { "*": "ask" },
    "write": { "*": "ask" },
    "bash":  {
      "git status":         "allow",
      "git diff *":         "allow",
      "git log *":          "allow",
      "ls *": "allow", "cat *": "allow", "rg *": "allow", "fd *": "allow",
      "git push --force*":         "deny",
      "git push -f *":             "deny",
      "git reset --hard origin/*": "deny",
      "rm -rf /*":                 "deny",
      "rm -rf ~*":                 "deny",
      "*": "ask"
    }
  }
}


Part 5 — .planning/config.json — the single most important file
This is what collapses three frameworks’ fleets into one. Every methodology rides as injected SKILL.md content.

{
  "version": "1.39",
  "workflow": {
    "tdd_mode": true,
    "auto_advance": true,
    "skip_discuss": false,
    "research": true,
    "plan_checker": true,
    "verifier": true,
    "discuss_mode": { "assumptions": "challenge" },
    "parallelization": { "enabled": true, "max_wave_size": 3 },

    "agent_skills": {
      "gsd-phase-researcher": [
        "global:gstack/skills/cso",
        "global:gstack/skills/plan-ceo-review"
      ],
      "gsd-planner": [
        "global:superpowers/skills/writing-plans",
        "global:gstack/skills/plan-eng-review"
      ],
      "gsd-plan-checker": [
        "global:superpowers/skills/verification-before-completion"
      ],
      "gsd-executor": [
        "global:superpowers/skills/test-driven-development",
        "global:superpowers/skills/systematic-debugging",
        "global:superpowers/skills/verification-before-completion"
      ],
      "gsd-verifier": [
        "global:superpowers/skills/requesting-code-review",
        "global:superpowers/skills/verification-before-completion",
        "global:gstack/skills/review"
      ],
      "gsd-debugger": [
        "global:superpowers/skills/systematic-debugging",
        "global:gstack/skills/investigate"
      ]
    },

    "context": { "include_project_claude_md": true, "include_codebase_map": true }
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
    "require_qa_when_ui_scope":  true,
    "require_research_complete": true
  },

  "features": {
    "playwright_mcp_verify": true,
    "research_gate":         true
  }
}


The research_gate and playwright_mcp_verify features are research gate — Blocks planning when RESEARCH.md has unresolved open questions ￼ and Playwright-MCP automated UI verification — Optional visual verification in verify-phase ￼ from v1.39 — both eliminate work the previous design was duplicating with gstack.
Part 6 — Project configs
<backend-repo>/opencode.jsonc:

{
  "$schema": "https://opencode.ai/config.json",
  "model": "anthropic/claude-opus-4-7",

  "agent": {
    "gsd-executor": {
      "permission": {
        "bash": {
          "uv run pytest *":   "allow",
          "uv run ruff *":     "allow",
          "uv run mypy *":     "allow",
          "uv run alembic upgrade head":   "allow",
          "uv run alembic upgrade +1":     "allow",
          "uv run alembic downgrade -1":   "allow",
          "go test *":         "allow",
          "cargo test *":      "allow",
          "git add *":         "allow",
          "git commit *":      "allow",
          "git switch *":      "allow",
          "psql * -c \"DROP *\"":      "deny",
          "uv run alembic downgrade base": "deny",
          "*": "ask"
        }
      }
    }
  },

  "mcp": {
    "context7":          { "type": "remote", "url": "https://mcp.context7.com/mcp" },
    "postgres-readonly": {
      "type": "local",
      "command": ["uvx", "postgres-mcp", "--read-only", "--url", "${env:DATABASE_URL_RO}"]
    },
    "github": {
      "type": "local",
      "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "${env:GITHUB_TOKEN_RO}" }
    }
  },

  "lsp": {
    "python":     { "command": ["uv", "run", "pyright-langserver", "--stdio"] },
    "go":         { "command": ["gopls"] },
    "rust":       { "command": ["rust-analyzer"] }
  },

  "watcher": {
    "ignore": ["**/.venv/**", "**/__pycache__/**", "**/target/**",
               "**/node_modules/**", "**/.planning/**", "**/coverage/**"]
  }
}


<frontend-repo>/opencode.jsonc:

{
  "$schema": "https://opencode.ai/config.json",
  "model": "anthropic/claude-opus-4-7",

  "agent": {
    "gsd-executor": {
      "permission": {
        "bash": {
          "pnpm dev":          "allow",
          "pnpm build":        "allow",
          "pnpm typecheck":    "allow",
          "pnpm lint *":       "allow",
          "pnpm test":         "allow",
          "pnpm test:*":       "allow",
          "pnpm vitest *":     "allow",
          "pnpm playwright test *":    "allow",
          "pnpm playwright install":   "allow",
          "git add *":         "allow",
          "git commit *":      "allow",
          "git switch *":      "allow",
          "rm -rf .next":      "allow",
          "rm -rf node_modules":       "allow",
          "rm -rf /*":         "deny",
          "*": "ask"
        }
      }
    },
    "gstack-qa": {
      "permission": {
        "bash": { "pnpm playwright *": "allow", "pnpm dev": "allow", "*": "ask" }
      }
    }
  },

  "mcp": {
    "context7":   { "type": "remote", "url": "https://mcp.context7.com/mcp" },
    "playwright": { "type": "local", "command": ["npx", "-y", "@playwright/mcp"] },
    "github": {
      "type": "local",
      "command": ["npx", "-y", "@modelcontextprotocol/server-github"],
      "env": { "GITHUB_TOKEN": "${env:GITHUB_TOKEN_RO}" }
    }
  },

  "lsp": {
    "typescript":  { "command": ["typescript-language-server", "--stdio"] },
    "tailwindcss": { "command": ["tailwindcss-language-server", "--stdio"] }
  },

  "watcher": {
    "ignore": ["**/node_modules/**", "**/.next/**", "**/.svelte-kit/**",
               "**/dist/**", "**/build/**", "**/.vite/**",
               "**/.planning/**", "**/playwright-report/**"]
  }
}


Part 7 — Back-pressure as opencode plugins (the Ralph idea, properly applied)
.opencode/plugin/autonomy-guards.ts:

import type { Plugin } from "@opencode-ai/plugin";

export const AutonomyGuards: Plugin = async ({ $ }) => ({
  // Block destructive commands during autonomous runs
  "tool.execute.before": async (input, output) => {
    if (input.tool !== "bash") return;
    const cmd = (input.args?.command as string) || "";
    const banned = [
      /git\s+push\s+-{1,2}f/,
      /git\s+reset\s+--hard\s+origin/,
      /rm\s+-rf\s+\//,
      /DROP\s+(DATABASE|SCHEMA)/i,
      /TRUNCATE\s+/i,
      /^npm\s+publish/, /^pnpm\s+publish/, /^cargo\s+publish/,
      /aws\s+s3\s+rm\s+--recursive/
    ];
    if (banned.some(r => r.test(cmd))) {
      output.abort = `Blocked destructive command in autonomous mode: ${cmd}`;
    }
  },

  // Run verification suite after every commit; record failures for the next agent turn
  "tool.execute.after": async (input) => {
    if (input.tool !== "bash") return;
    const cmd = (input.args?.command as string) || "";
    if (!/git\s+commit/.test(cmd)) return;

    const profile = (await $`gsd-sdk query profile`.text()).trim();   // backend|frontend
    const suite = profile === "frontend"
      ? "pnpm typecheck && pnpm lint && pnpm test --run"
      : "uv run pytest -x && uv run ruff check . && uv run mypy .";
    const r = await $`bash -lc "${suite}"`.nothrow();
    if (r.exitCode !== 0) {
      await $`gsd-sdk note "verification-failed" --severity high --evidence ${r.stderr}`;
    }
  },

  // On idle, snapshot state so the supervisor can restart cleanly
  "session.idle": async () => {
    await $`gsd-sdk query state-snapshot --reason idle`;
  }
});


This gives you Huntley’s three back-pressure properties: pre-commit guards, automatic verification, and snapshot-on-idle — without any of Ralph’s loop machinery.
Part 8 — The supervisor (the only outer loop)
bin/run-autonomous.sh:

#!/usr/bin/env bash
# Supervisor for /gsd-autonomous. Restarts on crash/idle. Stops on milestone seal.
# This is NOT a Ralph loop — it does not generate work or compete with GSD's state.
set -euo pipefail

MAX_RESTARTS="${MAX_RESTARTS:-20}"
IDLE_TIMEOUT_SECONDS="${IDLE_TIMEOUT_SECONDS:-1800}"   # 30 min
LOG_DIR=".planning/.autonomous-runs/$(date +%Y%m%d-%H%M%S)"
mkdir -p "$LOG_DIR"

restart_count=0
while [ "$restart_count" -lt "$MAX_RESTARTS" ]; do
  if gsd-sdk query milestone-status --json | jq -e '.completed == true' >/dev/null; then
    echo "✅ Milestone complete. Stopping."
    exit 0
  fi

  echo "▶ Autonomous run #$((restart_count+1))"
  if timeout "$IDLE_TIMEOUT_SECONDS" \
       gsd headless autonomous --no-tui 2>&1 | tee "$LOG_DIR/run-$restart_count.log"; then
    echo "Exited cleanly. Re-checking milestone status."
  else
    rc=$?
    if [ "$rc" -eq 124 ]; then
      echo "⚠ Idle timeout — restarting fresh."
    else
      echo "⚠ Crashed (rc=$rc). Backoff..."
      sleep $((10 * (restart_count + 1)))
    fi
  fi
  restart_count=$((restart_count + 1))
done

echo "❌ MAX_RESTARTS ($MAX_RESTARTS) hit without milestone completion."
exit 1


Run with bash bin/run-autonomous.sh and walk away. That’s the entire user-facing surface in autonomous mode.
Part 9 — AGENTS.md (the operating contract)

# AGENTS.md

## Decision tree (the only path)

ON kickoff (run ONCE, choose ONE branch):
  IF git_log_has_history AND has_source_files:
    /gsd-map-codebase
  ELSE:
    /superpowers:brainstorming    # Socratic spec → docs/specs/
    /gstack-office-hours          # YC six forcing questions → augments same doc
  /gsd-new-project                 # consumes whichever artifact exists → ROADMAP.md

ON each phase (inside /gsd-autonomous; never standalone):
  /gsd-discuss-phase N             # CSO + CEO-review skills are INJECTED here
  IF CONTEXT.md unknowns:  /gsd-spike
  IF UI scope AND no DESIGN.md:    /gstack-design-consultation  # ONCE per project
  /gsd-plan-phase N                # eng-review + writing-plans skills INJECTED
  /gstack-autoplan                 # ONE chained call → review report appended
  gsd-plan-checker                 # verification-before-completion INJECTED
  /gsd-execute-phase N             # TDD + sys-debug + verify INJECTED
  /gsd-verify-work N               # review + verify INJECTED
  IF UI scope: /gstack-qa
  IF release:  /gstack-document-release
  /gsd-ship

NEVER call directly:
  /superpowers:write-plan, /superpowers:execute-plan, /superpowers:subagent-driven-development
  /plan-ceo-review, /plan-eng-review, /plan-design-review, /plan-devex-review
  /review (gstack)        # injected into gsd-verifier
  /investigate (gstack)   # injected into gsd-debugger
  /cso (gstack)           # injected into gsd-phase-researcher

## State ownership
- `.planning/`           — GSD only. Commit to git. Never hand-edit.
- `docs/specs/`          — Superpowers brainstorming + gstack office-hours kickoff output.
- `docs/design/`, `DESIGN.md` — gstack-design-consultation. Frontend only.
- `~/.claude/skills/gstack/` — gstack only. Not in repo.
- `AGENTS.md`            — humans only, between fenced sections.

<!-- gsd-managed:begin --><!-- gsd-managed:end -->
<!-- gstack-managed:begin --><!-- gstack-managed:end -->


Part 10 — CI gate
.github/workflows/planning-gate.yml:

name: Planning gate
on: [pull_request]
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with: { node-version: '20' }
      - run: npm i -g get-shit-done-cc@1.39.0
      - name: Detect modified phase
        id: ph
        run: |
          PHASE=$(git diff --name-only origin/${{ github.base_ref }}... \
            | grep -oP '\.planning/phases/\K[^/]+' | sort -u | tail -1 || true)
          echo "phase=${PHASE}" >> "$GITHUB_OUTPUT"
      - name: Plan must include GSTACK REVIEW REPORT and pass plan-checker
        if: steps.ph.outputs.phase != ''
        run: |
          PLAN=".planning/phases/${{ steps.ph.outputs.phase }}/PLAN.md"
          test -f "$PLAN"
          grep -q '^## GSTACK REVIEW REPORT' "$PLAN" || { echo "missing autoplan report"; exit 1; }
          gsd-sdk query plan-checker --plan "$PLAN" --strict


Part 11 — Token economics (vs naive 3-framework stack)
For a 5-phase milestone × 4 plans/phase × 3 tasks/plan:



|Stage                   |Naive                                  |This stack                                      |Saved  |
|------------------------|---------------------------------------|------------------------------------------------|-------|
|Greenfield kickoff      |brainstorm 12k + office-hours 12k = 24k|brainstorm 12k + office-hours 8k (focused) = 20k|17%    |
|Per-phase discuss       |28k                                    |11.5k                                           |59%    |
|Per-phase plan          |41k                                    |20k                                             |51%    |
|Per-phase autoplan      |40k                                    |22k                                             |45%    |
|Per-phase execute       |95k                                    |65k                                             |32%    |
|Per-phase verify        |32k                                    |16k                                             |50%    |
|**Per-phase total**     |~236k                                  |~134k                                           |**43%**|
|**Milestone (5 phases)**|~1.18M                                 |~0.67M                                          |**43%**|

Savings come entirely from collapsing parallel pipelines. Quality is higher, not lower, because each framework’s unique strength is concentrated where it matters: gstack at strategic gates, Superpowers in the executor’s thinking, GSD in dispatch and state.
Part 12 — Verification

# 1. Topology — primary alone, all others hidden
opencode agent list | grep -E '(primary|subagent)'

# 2. Skill injection actually works
opencode run --agent gsd-executor "List your loaded skills."
# Expect: test-driven-development, systematic-debugging, verification-before-completion

opencode run --agent gsd-phase-researcher "List your loaded skills."
# Expect: cso, plan-ceo-review

opencode run --agent gsd-planner "List your loaded skills."
# Expect: writing-plans, plan-eng-review

# 3. Duplicates are blocked at runtime
opencode run --agent gsd-orchestrator \
  "Try to invoke /superpowers:write-plan and /gstack-plan-eng-review individually."
# Expect: both blocked.

# 4. Autonomous run works end-to-end
gsd-sdk query milestone-status --json | jq .
MAX_RESTARTS=1 IDLE_TIMEOUT_SECONDS=120 bash bin/run-autonomous.sh

# 5. Back-pressure plugin fires on commit
echo "x" >> README.md && git add README.md && git commit -m "test"
# Expect: verification suite runs; failures recorded in .planning/notes/.

# 6. CI gate
gh pr create --draft && gh pr checks --watch


If all six pass, the stack is wired correctly: one orchestrator (GSD), one dispatch loop (/gsd-autonomous), methodology baked in via agent_skills, gstack only at the strategic gates that are unique to it, and back-pressure caught by opencode hooks rather than another loop.
Part 13 — What you give up by not adding Ralph
Honestly: nothing that matters at this stack’s level of maturity. The Ralph blog posts make clear the mechanism is while :; do cat PROMPT.md | claude -p ; done. That’s a 1-liner that exists because Claude Code didn’t ship state management. GSD ships state management. The Ralph mechanism is what GSD’s autonomous mode already is, plus structured artifacts.
If you ever outgrow this stack — say, you want 10–15 parallel sprints across worktrees, the way gstack’s “Conductor” supports — then you reach for parallelism, not for Ralph. The right tool for that is git worktree + N independent bin/run-autonomous.sh processes, each on its own branch, each with its own .planning/ substate. That’s the “Gas Town” pattern Huntley describes, and it composes with this stack cleanly because the supervisor is per-process. You don’t change the inner topology at all.
Caveats and version notes
	•	GSD v1.39.0 (get-shit-done-cc) is current as of v1. Pin to it. Do not upgrade mid-milestone. The npm dist-tag is latest + next. Your CI step npm i -g get-shit-done-cc@1.39.0 is the correct pin.
	•	The agent_skills injection schema with global: prefix landed in v1.36 and was hardened across v1.37–v1.39 (e.g., SDK globalDefaults preserved for nested config keys — workflow, git, hooks, agent_skills, features sections were silently dropping user values from ~/.gsd/defaults.json ￼ was fixed in v1.39). Run gsd config validate after editing .planning/config.json.
	•	For OpenCode specifically, the rokicool/gsd-opencode fork tracks v1 and ships the Task()→@agent translation needed by opencode’s dispatch model. If npx get-shit-done-cc --opencode doesn’t resolve agents correctly on your version, swap to that fork — same .planning/config.json, same agent IDs, just a runtime-adapter difference.
	•	Subagent skill-injection had a known bug where GSD subagents (gsd-executor, gsd-planner, gsd-phase-researcher) do not receive the project-level CLAUDE.md file in their execution context ￼. The workflow.context.include_project_claude_md: true flag works around it. Confirm it’s set; without it, the entire skill-injection scheme silently degrades.
	•	Skill IDs may drift between framework releases. After every install or upgrade, run opencode agent list and confirm names match what’s in .planning/config.json. If a name moved, the only file that needs updating is the config — the topology stays identical.
That’s the whole stack: one runtime, one orchestrator, methodology injected, strategic gates one-shot, autonomous loop built in, back-pressure as plugins, supervisor as a 30-line script. Production-grade, no duplication, no Ralph required.​​​​​​​​​​​​​​​​