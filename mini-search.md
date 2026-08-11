# Building a Lessons Memory Extension for Pi

**A lifecycle-first design report — with emphasis on installation and first-startup mechanics**

Researched against **`@earendil-works/pi-coding-agent` v0.84.1** (published 2026-08-07), read from the
actual published npm tarball, not from documentation alone. All timings marked *(measured)* were run
locally on Node v22.22.2 / 1 vCPU Xeon @2.8 GHz — treat them as **shape and ratio**, not absolute
numbers for your laptop (a modern M-series or Ryzen will be roughly 5–10× faster across the board).

---

## 0. Executive summary

> **The answer in one paragraph.** Build `pi-lessons` as a **pre-compiled ESM pi package** (`"type":
> "module"` + `pi.extensions` pointing at `dist/*.js`) with **zero runtime dependencies**, storing
> lessons in a single **SQLite file with an FTS5/BM25 index** via Node's built-in `node:sqlite`.
> Add semantic recall as an *optional, lazily-acquired* second signal fused with **Reciprocal Rank
> Fusion**, never as a startup requirement. Do **no** I/O, **no** database open, and **no** network
> in the extension factory — the factory only registers tools and handlers. This gives you a
> **~4 ms cold extension load** instead of **~1000 ms**, an install that never compiles native code,
> and an extension that degrades gracefully to pure lexical search on any platform Pi runs on,
> including Termux and Pi's Bun-compiled standalone binary.

The three findings that drive every decision in this report:

| # | Finding | Consequence |
|---|---------|-------------|
| **F1** | Pi loads extensions **strictly sequentially**, `await`-ing each factory before the next. Anything slow in your factory is *serial* startup latency added to every `pi` invocation. | Factory must be pure registration. Defer everything to `session_start` or first tool call. |
| **F2** | Pi loads extensions through **jiti**. A ~1200-line TypeScript file costs **~1000 ms** to transpile cold vs **~8 ms** warm from jiti's fs cache — but shipping **pre-compiled ESM** takes the *native import* path at **~4 ms with no cache at all** *(measured)*. | Ship compiled `.js`/`.mjs` with `"type": "module"`. This is the single biggest win available. |
| **F3** | Node ≥22.13 ships SQLite **with FTS5, BM25 ranking, the trigram tokenizer, and `loadExtension`** built in *(verified: SQLite 3.51.2 under Node 22.22)*. Pi requires Node ≥22.19.0. | You get a production-grade BM25 engine at **zero install cost, zero dependencies, ~0.5 ms import** *(measured)*. |

---

# PART I — Pi's extension protocol and installation mechanism

## 1.1 What Pi actually is, as of v0.84.1

```
┌──────────────────────────────────────────────────────────────────────────┐
│  @earendil-works/pi-coding-agent  v0.84.1     (npm, 2026-08-07)          │
│  engines.node  >= 22.19.0        bin: pi -> dist/cli.js                  │
│  Repo: earendil-works/pi  (formerly badlogic/pi-mono, @mariozechner/*)   │
├──────────────────────────────────────────────────────────────────────────┤
│  Sibling packages (version-locked ^0.84.1):                              │
│    @earendil-works/pi-ai           unified multi-provider LLM API        │
│    @earendil-works/pi-agent-core   agent loop, tool calling, state       │
│    @earendil-works/pi-tui          differential-rendering terminal UI    │
│    @earendil-works/pi-protocol     wire protocol types                   │
│    @earendil-works/pi-client       client bindings                       │
│    @earendil-works/pi-telemetry    vendor-neutral telemetry contracts    │
├──────────────────────────────────────────────────────────────────────────┤
│  Key runtime deps:  jiti 2.7.0  ·  typebox 1.3.7  ·  glob 13  ·  yaml    │
│                     minimatch 10  ·  proper-lockfile  ·  undici 8        │
└──────────────────────────────────────────────────────────────────────────┘
```

Two published dist-tags matter: `latest` = **0.84.1**, and `legacy-node20` = 0.74.2 (a maintenance
line for Node 20). If you set `engines.node: ">=22.19.0"` you are aligned with mainline Pi.

**Pi has four run modes** — `interactive` (TUI), `print`/`json` (`-p`), `rpc` (JSONL over
stdin/stdout), and `sdk` (embedded). Your extension code runs in all four, so every UI call must be
guarded. There is also a **standalone Bun-compiled binary** distribution (installed via
`curl -fsSL https://pi.dev/install.sh | sh`), which changes module resolution — see §1.7.

## 1.2 The three ways an extension reaches Pi

```
                    ┌─────────────────────────────────────────┐
                    │         HOW EXTENSIONS GET LOADED       │
                    └─────────────────────────────────────────┘

  (A) AUTO-DISCOVERY                (B) SETTINGS PATHS         (C) PI PACKAGES
      (bare files on disk)              (explicit list)            (npm / git)

  ~/.pi/agent/extensions/           settings.json:             settings.json:
    ├── foo.ts            ◄─ .ts      { "extensions": [          { "packages": [
    ├── bar.js            ◄─ .js          "/abs/path/x.ts",         "npm:@you/pi-lessons@1.2.3",
    └── baz/                              "/abs/path/dir"           "git:github.com/you/repo@v1",
        ├── index.ts      ◄─ index     ] }                          "/local/path"
        └── package.json  ◄─ manifest                             ] }
             { "pi": { "extensions": [...] } }
  .pi/extensions/    (project-local, ONLY after trust)
                                                             CLI: pi install npm:@you/pi-lessons
                                                                  pi -e npm:@you/pi-lessons  (temp)
```

**Critical asymmetry you must know:** bare-file auto-discovery in an `extensions/` directory only
recognises `*.ts` and `*.js`. But a **`package.json` manifest** (`"pi": { "extensions": [...] }`) is
resolved by *literal path existence check* — it will happily load `./dist/index.js`, `./index.mjs`,
or any other path you name. **This is the loophole that lets you ship pre-compiled ESM.** (Verified
in `dist/core/extensions/loader.js :: resolveExtensionEntries` and
`dist/core/package-manager.js :: collectFilesFromManifestEntries`.)

### Source specs Pi accepts

| Spec | Installed to (user scope) | Installed to (project scope, `-l`) |
|------|---------------------------|-------------------------------------|
| `npm:@scope/pkg@1.2.3` | `~/.pi/agent/npm/node_modules/...` | `.pi/npm/node_modules/...` |
| `npm:pkg` (unpinned) | same — but *is* moved by `pi update --extensions` | same |
| `git:github.com/u/r@v1` | `~/.pi/agent/git/<host>/<path>` | `.pi/git/<host>/<path>` |
| `git:git@github.com:u/r@v1` / `ssh://…` | same (uses your `~/.ssh/config`) | same |
| `https://github.com/u/r@v1` | same | same |
| `/abs/path` or `./rel/path` | **not copied** — referenced in place | same |

A **versioned npm spec is pinned**: `pi update --extensions` and `pi update --all` deliberately skip
it. Git refs are likewise pinned but *reconciled* (an existing clone is reset/cleaned to the
configured ref). Identity for deduplication is package name (npm), repo URL without ref (git), or
resolved absolute path (local); **project scope wins over global**, unless the project entry sets
`autoload: false`, in which case it is applied as a *delta* on top of the global entry.

## 1.3 Diagram — the installation mechanism, precisely

This is what actually executes when a user runs `pi install npm:@you/pi-lessons@1.0.0`.
Command lines are transcribed from `DefaultPackageManager` in the shipped `dist/`.

```
 USER                    PI CLI                     PACKAGE MANAGER            FILESYSTEM / NET
  │                        │                              │                          │
  │ pi install npm:@you/   │                              │                          │
  │  pi-lessons@1.0.0      │                              │                          │
  ├───────────────────────►│                              │                          │
  │                        │ parseSource(spec)            │                          │
  │                        │   type=npm  name=@you/…      │                          │
  │                        │   version=1.0.0  pinned=true │                          │
  │                        ├─────────────────────────────►│                          │
  │                        │                              │ assertProjectTrusted…    │
  │                        │                              │ (project scope only)     │
  │                        │                              │                          │
  │                        │                              │ getNpmInstallRoot(scope) │
  │                        │                              │  user → ~/.pi/agent/npm  │
  │                        │                              │  proj → .pi/npm          │
  │                        │                              ├─────────────────────────►│
  │                        │                              │ ensureNpmProject(root)   │
  │                        │                              │   write package.json     │
  │                        │                              │   write .gitignore       │
  │                        │                              │                          │
  │                        │                              │  ┌────────────────────┐  │
  │                        │                              │  │ EXACT COMMAND RUN: │  │
  │                        │                              │  │ npm install \      │  │
  │                        │                              │  │   @you/pi-lessons@1.0.0 \
  │                        │                              │  │   --prefix <root> \│  │
  │                        │                              │  │   --legacy-peer-deps│ │
  │                        │                              │  └────────────────────┘  │
  │                        │                              ├─────────────────────────►│
  │                        │                              │            ▼ network ▼   │
  │                        │                              │◄─────────────────────────┤
  │                        │                              │ addSourceToSettings()    │
  │                        │                              │  → settings.json         │
  │                        │                              │    "packages": [ … ]     │
  │◄───────────────────────┴──────────────────────────────┤                          │
  │   installed                                           │                          │

  ▓▓▓ WHY --legacy-peer-deps MATTERS TO YOU ▓▓▓
  Pi deliberately disables peer resolution for managed installs (npm: --legacy-peer-deps,
  pnpm: --config.auto-install-peers=false, bun: --omit=peer). Rationale from the source
  comment: "Extension packages run inside pi and resolve pi APIs through loader
  aliases/virtual modules… Stale auto-installed pi peers can otherwise block updates."
  ⇒ Declare @earendil-works/pi-* and typebox in peerDependencies with range "*".
    They will NOT be installed. The host injects them. Never bundle them.
```

Git installs take a different, more fragile path:

```
 pi install git:github.com/you/pi-lessons@v1.0.0
  │
  ├─ target = ~/.pi/agent/git/github.com/you/pi-lessons
  ├─ ensureGitIgnore(gitRoot)
  ├─ mkdir -p <parent>;  rm -f <update-marker>
  ├─ git clone <repo> <target>                     ◄── requires git on PATH
  ├─ git checkout <ref>                    (cwd=target, if ref given)
  ├─ if exists(target/package.json):
  │     npm install --omit=dev             (cwd=target)   ◄── PRODUCTION INSTALL
  │     …unless settings.npmCommand is set, then plain `npm install`
  └─ on ANY failure: rm -rf <target>; prune empty parents; rethrow
```

> **Trap:** git installs run `npm install --omit=dev`. Anything you put in `devDependencies` is
> **absent at runtime**. If your extension imports it, the load fails. And because git packages are
> installed from source, you must **commit `dist/`** or the build never happens — Pi does not run
> your `prepare`/`build` script.

## 1.4 Diagram — cold start, end to end

This is the sequence Pi executes on `pi` in a project directory, reconstructed from `dist/main.js`
(the `time(...)` instrumentation labels are Pi's own — set `PI_TIMING=1` to print them).

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║  PI COLD START  —  where your extension's time is spent                          ║
╚══════════════════════════════════════════════════════════════════════════════════╝

  t=0   process spawn ─┬─ node dist/cli.js            (npm install)
                       └─ ./pi standalone binary      (Bun compile; §1.7)
   │
   ├─ parseArgs                                                    [time: parseArgs]
   ├─ runMigrations(cwd)                                           [time: runMigrations]
   ├─ SettingsManager.create(cwd, agentDir)     ← reads ~/.pi/agent/settings.json
   ├─ firstTimeSetup (interactive, first run only)                 [time: firstTimeSetup]
   ├─ createSessionManager(...)                                    [time: createSessionManager]
   │
   ├─ createAgentSessionServices ──► ResourceLoader.reload()       [time: createRuntime]
   │   │
   │   │   ╭──────────────────── PHASE A: TRUST ────────────────────╮
   │   ├──►│ loadProjectTrustExtensions()                           │
   │   │   │   loads USER/GLOBAL + CLI -e extensions ONLY           │
   │   │   │   fires  project_trust  ─► handler may return          │
   │   │   │        { trusted:"yes"|"no"|"undecided", remember? }   │
   │   │   │   first yes/no wins; else trust.json; else             │
   │   │   │        defaultProjectTrust setting                     │
   │   │   ╰────────────────────────────────────────────────────────╯
   │   │        ⚠ project-local .pi/extensions are NOT loaded yet
   │   │
   │   ├─ settingsManager.reload()        (re-read for resolved trust state)
   │   │
   │   │   ╭─────────────── PHASE B: PACKAGE RESOLUTION ────────────╮
   │   ├──►│ packageManager.resolve()                               │
   │   │   │   • merge project.packages + global.packages           │
   │   │   │   • dedupe by identity (project wins / delta)          │
   │   │   │   • MISSING project package? → auto-install now        │
   │   │   │     (this is a NETWORK + npm install on the hot path)  │
   │   │   │   • walk each package root, apply manifest + filters   │
   │   │   │   → ResolvedPaths{ extensions[], skills[],             │
   │   │   │                    prompts[], themes[] }               │
   │   │   ╰────────────────────────────────────────────────────────╯
   │   │
   │   │   ╭──────────── PHASE C: EXTENSION LOAD  (SEQUENTIAL) ─────╮
   │   ├──►│ for (const path of extensionPaths) {        ◄── F1     │
   │   │   │     factory = await jiti.import(path)       ◄── F2     │
   │   │   │     api     = createExtensionAPI(...)                  │
   │   │   │     await factory(api)      ← YOUR CODE RUNS HERE      │
   │   │   │ }                                                      │
   │   │   │ ORDER: project-local first, then global, then          │
   │   │   │        settings paths, then CLI -e (temporary)         │
   │   │   ╰────────────────────────────────────────────────────────╯
   │   │        • pi.registerTool / registerCommand / on(...) → recorded
   │   │        • pi.registerProvider(...)  → QUEUED, flushed after bind
   │   │        • pi.sendMessage / appendEntry / getAllTools → THROW
   │   │            "Extension runtime not initialized"
   │   │
   ├─ createAgentSessionRuntime                       [time: createAgentSessionRuntime]
   ├─ initTheme / resolveModelScope / createAgentSession
   │
   ├──► EMIT  session_start { reason: "startup" }     ◄── first point you may do real work
   ├──► EMIT  resources_discover { reason: "startup" } ◄── contribute skill/prompt/theme paths
   │
   └─ interactiveMode.init                            [time: interactiveMode.init]
          ▼
     ┌──────────────────────────────┐
     │  TUI visible. User can type. │
     └──────────────────────────────┘
```

**Everything between `createRuntime` and `interactiveMode.init` is dead time the user stares at.**
Phase C is where a badly-built extension burns seconds.

## 1.5 The jiti transpile cliff — measured, and how to fall off the right side

Pi loads every extension with `jiti` configured as:

```js
createJiti(import.meta.url, {
  moduleCache: false,                                     // re-evaluate each load
  ...(isBunBinary  ? { virtualModules: VIRTUAL_MODULES, tryNative: false }
    : isTsSource   ? { virtualModules: VIRTUAL_MODULES, tsconfigPaths: true }
                   : { alias: getAliases() }),            // normal npm-installed Node
});
```

jiti decides per file whether to **transpile through Babel** or **hand off to native `import()`**.
That decision is worth ~250×.

```
                         ┌──────────────────────────────────┐
                         │   jiti.import(file, {default})   │
                         └────────────────┬─────────────────┘
                                          │
                       ┌──────────────────┴──────────────────┐
                       │  is it .mjs, OR .js in a package    │
                       │  whose package.json has             │
                       │  "type": "module"  ?                │
                       └───────┬──────────────────┬──────────┘
                          YES  │                  │  NO
                               ▼                  ▼
              ┌────────────────────────┐   ┌──────────────────────────────┐
              │  [native] [import]     │   │  [transpile] via Babel       │
              │  no Babel, no cache    │   │  then cache to fs            │
              │                        │   │                              │
              │  ≈ 4 ms   COLD         │   │  ≈ 1000 ms  COLD  (cache miss)│
              │  ≈ 4 ms   WARM         │   │  ≈    8 ms  WARM  (cache hit) │
              └────────────────────────┘   └──────────────────────────────┘
                     ✔ RECOMMENDED                  ✗ what most pi packages do

   Measured, 403-function / ~1200-line module, Node 22.22, 1 vCPU @2.8GHz:
     ./big.ts                     COLD 1023 ms / 1025 ms (reproducible)   WARM  7.9 ms
     ./big.js  (CJS-ish)          COLD  888 ms                            WARM  8.0 ms
     ./esmpkg/index.js  type:module   COLD 4.8 ms    WARM 4.4 ms   ← no cache file written
     ./mjspkg/index.mjs               COLD 4.3 ms    WARM 4.1 ms   ← no cache file written
     tiny .ts (1 line)            WARM  3.4 ms
```

### Where the warm cache actually lives — and why you cannot rely on it

Reading jiti 2.7.0's `prepareCacheDir`: the fs cache directory is
`resolve(<jiti caller file>, "../node_modules") + "/.cache/jiti"` **if that directory exists**,
otherwise **`os.tmpdir()/jiti`**. Pi's jiti caller is
`…/pi-coding-agent/dist/core/extensions/loader.js`, and `…/dist/core/extensions/node_modules` does
not exist — so:

```
   ┌────────────────────────────────────────────────────────────────────┐
   │  Pi's extension transpile cache lands in  $TMPDIR/jiti             │
   │                                                                    │
   │  Linux   /tmp/jiti          ← wiped on reboot, and by              │
   │                               systemd-tmpfiles age policies        │
   │  macOS   /var/folders/…/T/jiti  ← per-user, periodically reaped    │
   │  CI      fresh every job    ← ALWAYS cold                          │
   │                                                                    │
   │  Cache filename embeds hash(absolutePath) and the trailer embeds   │
   │  hash(source) + a jiti cache-format version tag ("v9-…").          │
   │  ⇒ EVERY version bump of your extension = full re-transpile.       │
   │  ⇒ Every reboot on Linux = full re-transpile.                      │
   └────────────────────────────────────────────────────────────────────┘
```

So "it's only slow the first time" is **false** in practice for TypeScript-shipped pi packages. It
is slow on: first install, every upgrade, every reboot (Linux), every CI run, every container start.
Pre-compiled ESM sidesteps the cache entirely — there is nothing to be cold about.

## 1.6 What the extension protocol gives you

The contract is one default-exported factory. Sync or async; if it returns a Promise, Pi **awaits it
before continuing startup** — before `session_start`, before `resources_discover`, and before
queued `registerProvider` calls are flushed.

```typescript
import type { ExtensionAPI } from "@earendil-works/pi-coding-agent";
export default function (pi: ExtensionAPI) { /* register only */ }
```

### Capability surface (v0.84.1)

```
 pi.registerTool(def)          LLM-callable tool. Works during load AND at runtime;
                               new tools are hot-refreshed without /reload.
 pi.registerCommand(name,opts) /slash command. Handler gets ExtensionCommandContext
                               (superset: newSession/fork/switchSession/reload/waitForIdle).
 pi.registerShortcut(key,opts) Keybinding.        pi.registerFlag(name,opts)   CLI flag.
 pi.registerEntryRenderer(t,r) TUI renderer for custom entries (NOT in LLM context).
 pi.registerMessageRenderer    TUI renderer for custom messages (IS in LLM context).
 pi.registerMarkdownTransformer  display-only markdown rewrite; must be sync + cheap.
 pi.registerProvider / unregisterProvider        model provider injection.
 pi.appendEntry(type, data)    durable, session-persisted, invisible to the LLM.
 pi.sendMessage(msg, {deliverAs:"steer"|"followUp"|"nextTurn", triggerTurn})
 pi.sendUserMessage(content, {deliverAs})
 pi.getAllTools / getActiveTools / setActiveTools    dynamic tool (de)activation.
 pi.exec(cmd, args, {signal, timeout})               child process.
 pi.events.on/emit(channel, data)                    cross-extension bus.
```

### Event lifecycle — the hooks that matter for a memory extension

```
  pi starts
    ├─► project_trust          (global/CLI extensions only; before project resources)
    ├─► session_start   { reason:"startup" }      ◄── OPEN RESOURCES HERE, NOT IN FACTORY
    └─► resources_discover { reason:"startup" }   ◄── contribute skill/prompt/theme paths
        ▼
  user prompt
    ├─► (extension commands checked first — bypass the rest if matched)
    ├─► input              (transform / handle / continue)
    ├─► (skill + prompt-template expansion)
    ├─► before_agent_start ◄── INJECT RECALLED LESSONS HERE  { message, systemPrompt }
    ├─► agent_start
    │     ┌── turn (loops while the LLM calls tools) ──┐
    │     ├─► turn_start
    │     ├─► context               ◄── non-destructive message-list rewrite
    │     ├─► before_provider_headers / before_provider_request / after_provider_response
    │     ├─► tool_execution_start → tool_call (CAN BLOCK) → tool_result (CAN MODIFY)
    │     └─► turn_end
    ├─► agent_end
    └─► agent_settled      ◄── nothing more will auto-run; SAFE PLACE TO HARVEST LESSONS

  /new · /resume · /fork · /clone
    ├─► session_before_switch | session_before_fork   (can cancel)
    ├─► session_shutdown      ◄── CLOSE RESOURCES (idempotent!)
    ├─► session_start { reason:"new"|"resume"|"fork", previousSessionFile }
    └─► resources_discover { reason:"startup" }

  /compact  ├─► session_before_compact (cancel or supply your own summary) ─► session_compact
  /tree     ├─► session_before_tree ─► session_tree
  exit      └─► session_shutdown  { reason:"quit"|"reload"|"new"|"resume"|"fork" }
```

**Pi's own documentation states the rule explicitly**, and it is the crux of this whole report:

> *"Extension factories may run in invocations that never start a session. Do not start background
> resources such as processes, sockets, file watchers, or timers from the factory. Defer background
> resource startup until `session_start` or the command/tool/event that needs the resource.
> Register an idempotent `session_shutdown` handler…"*

## 1.7 The runtime matrix — why "just use better-sqlite3" is wrong

```
┌───────────────────┬──────────────────────────┬───────────────────────────────────┐
│                   │  Node install            │  Bun standalone binary            │
│                   │  npm i -g …pi-coding-… │  curl pi.dev/install.sh | sh      │
├───────────────────┼──────────────────────────┼───────────────────────────────────┤
│ detected by       │ (default)                │ import.meta.url contains          │
│                   │                          │ "$bunfs" / "~BUN" / "%7EBUN"      │
├───────────────────┼──────────────────────────┼───────────────────────────────────┤
│ jiti config       │ alias: {…}               │ virtualModules: {…}               │
│                   │ (resolve to dist files)  │ tryNative: false                  │
├───────────────────┼──────────────────────────┼───────────────────────────────────┤
│ how pi APIs       │ path aliases to the       │ statically bundled ES namespaces  │
│ reach you         │ installed dist/*.js       │ handed over as virtual modules    │
├───────────────────┼──────────────────────────┼───────────────────────────────────┤
│ node:sqlite       │ ✅ built in (3.51.2)      │ Bun implements node:sqlite, but   │
│                   │    FTS5 ✅  bm25 ✅        │ verify at runtime — do not assume │
│                   │    trigram ✅ rtree ✅     │                                   │
│                   │    loadExtension ✅        │                                   │
├───────────────────┼──────────────────────────┼───────────────────────────────────┤
│ native .node      │ works if prebuilds exist  │ ⚠ high risk — N-API addons in a   │
│ addons            │ for the exact ABI         │ compiled Bun executable are a     │
│ (better-sqlite3)  │ else node-gyp compiles    │ known sharp edge                  │
│                   │ (needs a C++ toolchain)   │                                   │
└───────────────────┴──────────────────────────┴───────────────────────────────────┘

  Aliased/virtual module names available to your extension in BOTH modes:
    @earendil-works/pi-coding-agent      @earendil-works/pi-ai   (→ compat entrypoint)
    @earendil-works/pi-agent-core        @earendil-works/pi-ai/oauth
    @earendil-works/pi-tui               @earendil-works/pi-ai/providers/all
    typebox · typebox/compile · typebox/value  (+ @sinclair/typebox aliases)
    …plus every legacy @mariozechner/* name, still aliased for back-compat.
    Node built-ins (node:fs, node:path, node:sqlite, …) always available.
```

Pi also officially supports **Termux on Android** (`aarch64-linux-android`) and Windows. Any
dependency without a prebuilt for that triple is a broken install for those users.

**Prior art confirms the hazard.** The most popular memory extension in Pi's catalog,
`pi-hermes-memory` (v0.9.4, ~22K downloads/mo), does exactly the two things this report advises
against: it depends on `better-sqlite3` (a 27 MB native package) and it points
`pi.extensions` at `./src/index.ts` — so it pays both the native-compile install risk *and* the
~1 s cold transpile. It nonetheless proves the core retrieval choice is right: its search layer is
**SQLite FTS5**. Contrast `@remnic/plugin-pi`, which ships `./dist/index.js`.


---

# PART II — Super-mini, portable retrieval: the landscape

## 2.1 Constraints inherited from Part I

Before comparing libraries, write down what Part I forces on us. This kills most of the field
immediately.

```
  C1  Zero native compilation.   npm install --omit=dev / --legacy-peer-deps must always succeed,
                                 on macOS/Linux/Windows/Termux, with no C++ toolchain.
  C2  Bun-binary safe.           Must work when Pi is a compiled Bun executable.
  C3  Cold-import budget < 20ms. Sequential factory loading; you share the budget with every
                                 other extension the user has installed.
  C4  No factory-time I/O.       No DB open, no model load, no network, before session_start.
  C5  Small install footprint.   Users `pi install` this on a whim; 100 MB of ONNX runtime is
                                 a non-starter.
  C6  Offline-capable.           PI_OFFLINE exists and is respected by Pi itself. Honour it.
  C7  Corpus scale is SMALL.     Lessons are 10² – 10⁴ short records, not 10⁶ document chunks.
```

**C7 is the most underrated.** Almost every "RAG stack" is engineered for the 10⁶-chunk regime,
where an ANN index is mandatory. At lesson scale it is pure overhead:

```
  MEASURED — brute-force cosine over a pre-normalised Float32Array (plain JS, 1 vCPU):

    N =  1,000  D=384   →   2.55 ms/query      1.5 MB resident
    N = 10,000  D=384   →  11.29 ms/query     14.6 MB resident
    N = 50,000  D=384   →  55.78 ms/query     73.2 MB resident
    N = 10,000  D=256   →   7.32 ms/query      9.8 MB resident

  For a lessons corpus you will realistically never exceed ~5,000 records.
  A flat scan is ~5 ms. An HNSW index would save ~4 ms and cost you a native
  dependency, an index file to keep in sync, and a rebuild path. Do not build it.
```

## 2.2 Verified capability probe — what you actually get for free

I ran these against Node v22.22.2 inside a clean container. All results are direct observations,
not documentation claims.

```
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │  node:sqlite  (built in since Node 22.13 unflagged; Pi requires ≥22.19)      │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │  sqlite_version()                        3.51.2            ✅                 │
 │  CREATE VIRTUAL TABLE … USING fts5       works              ✅                 │
 │  bm25(tbl, w1, w2) ranking function      works              ✅                 │
 │  tokenize='porter unicode61'             works              ✅                 │
 │  tokenize='trigram'  (substring/fuzzy)   works              ✅                 │
 │  USING rtree                             works              ✅                 │
 │  db.loadExtension / enableLoadExtension  present            ✅                 │
 │  vec0 built in                           NO — needs sqlite-vec                │
 │  import cost (cold process)              0.47 – 0.70 ms                       │
 │  open file + WAL + 2 CREATE (first ever) 6.51 ms                              │
 │  open file + WAL + 2 CREATE (existing)   0.43 ms                              │
 │  prepare() a statement                   0.05 ms                              │
 │  ⇒ TOTAL cold path to a query-ready DB:  ~1 ms warm / ~7 ms on creation       │
 └──────────────────────────────────────────────────────────────────────────────┘

 ┌──────────────────────────────────────────────────────────────────────────────┐
 │  sqlite-vec 0.1.9  loaded into node:sqlite  — VERIFIED WORKING                │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │  npm i sqlite-vec  →  204 KB on disk total (meta pkg + one platform binary)   │
 │  new DatabaseSync(path, { allowExtension: true })                             │
 │  db.enableLoadExtension(true); sqliteVec.load(db)                             │
 │  select vec_version()                    → "v0.1.9"        ✅                  │
 │  CREATE VIRTUAL TABLE … USING vec0(...)  works              ✅                 │
 │  KNN: WHERE embedding MATCH ? AND k = 2 ORDER BY distance   ✅                 │
 │  FTS5 table in the SAME database file    ✅  ⇒ true single-file hybrid         │
 │  vec_quantize_binary / vec_quantize_int8 present (dim must be %8==0)          │
 │  ⚠ INTEGER PRIMARY KEY must be bound as BigInt (1n), not Number               │
 │  ⚠ PLATFORMS: linux-x64, linux-arm64, darwin-x64, darwin-arm64, windows-x64   │
 │     MISSING: windows-arm64, linux-musl (Alpine), Termux/android-arm64         │
 │  ⚠ still 0.1.x — pre-1.0, treat as optional enhancement, never a hard dep     │
 └──────────────────────────────────────────────────────────────────────────────┘

 ┌──────────────────────────────────────────────────────────────────────────────┐
 │  FTS5 BM25 throughput  (synthetic worst case: 20,000 docs, 25-word vocab,     │
 │  a 3-term OR query that matches nearly every document)                        │
 │       19.95 ms/query.   Real lesson corpora are 1–2 orders more selective     │
 │       and 1–2 orders smaller ⇒ sub-millisecond in practice.                   │
 └──────────────────────────────────────────────────────────────────────────────┘
```

## 2.3 The candidate field, scored against C1–C7

Sizes are npm `unpackedSize` for the latest version as of 2026-08-11.

### Tier A — lexical / BM25 engines

| Option | Latest | Size | Native? | Verdict |
|---|---|---|---|---|
| **`node:sqlite` FTS5 + `bm25()`** | Node built-in | **0 KB** | no | ★ **Pick this.** Real BM25 with field weights, prefix/phrase/NEAR queries, porter + trigram tokenizers, incremental updates, ACID, one file. Import ~0.5 ms. |
| MiniSearch | 7.2.0 | 826 KB | no | Excellent pure-JS BM25, zero deps, fuzzy + prefix. But **in-memory only** — you serialise the whole index to JSON and reload it on every start. Fine ≤2k docs; becomes a startup tax as it grows. Best fallback if `node:sqlite` is unavailable. |
| FlexSearch | 0.8.212 | 2.3 MB | no | Fastest raw JS search, but scoring is not BM25 and the API is idiosyncratic. Overkill here. |
| `@orama/orama` | 3.1.18 | 2.2 MB | no | Full-text + vector + hybrid in one zero-dep package. Genuinely capable. Costs 2.2 MB and an in-memory index with manual persistence. The strongest *single-library* alternative if you want hybrid without SQLite. |
| better-sqlite3 | 13.0.3 | **27 MB** | **yes** | Faster than `node:sqlite` and battle-tested, but violates C1 and C2. Only justified if you need something `node:sqlite` lacks — and for FTS5 + BM25, it doesn't. |
| node-sqlite3-wasm | 0.8.60 | 1.3 MB | no (WASM) | Real fallback for exotic runtimes: SQLite compiled to WASM with fs access, no native build. Slower, but portable everywhere. |
| wink-bm25-text-search | 3.1.2 | 109 KB | no | Textbook BM25, but drags in `wink-nlp` + an English model. Unmaintained since 2022. |

### Tier B — vector stores

| Option | Latest | Size | Native? | Verdict |
|---|---|---|---|---|
| **Flat `Float32Array` scan** | — | **0 KB** | no | ★ **Pick this at lesson scale.** ~5 ms at 5k×384. Store vectors as BLOBs in the same SQLite row; load into one contiguous typed array on demand. |
| **sqlite-vec** | 0.1.9 | 204 KB | prebuilt `.so` (no compile) | ★ **Optional upgrade.** True KNN in the same DB file, with int8/binary quantisation. Verified working with `node:sqlite`. Gate it behind a probe — 3 platforms are uncovered. |
| hnswlib-node | 3.0.0 | 196 KB | **yes, node-gyp** | Violates C1. Also unmaintained (2024). |
| Vectra | 0.15.0 | 2.2 MB | no | File-backed vector DB with built-in hybrid BM25 — conceptually a perfect fit, but pulls grpc/cheerio/openai/turndown into your dependency tree. Too heavy for C5. |
| `@lancedb/lancedb` | 0.37.1 | 1.4 MB + platform binaries | yes (Rust) | Production-grade, but a whole embedded database for ≤5k rows. |
| DuckDB + VSS | 1.4.4 | **61 MB** | yes | Absolutely not. |

### Tier C — how you get the vectors at all

| Approach | Cost to install | Cost at first use | Quality | Portable? |
|---|---|---|---|---|
| **Tier 0 — none (lexical only)** | 0 | 0 | Surprisingly strong for lessons, which are written in the user's own vocabulary | ✅ everywhere |
| **Tier 1 — remote embedding API** (`ctx.modelRegistry.getProviderAuth(id)` → `fetch` the provider's `/embeddings`) | 0 | one HTTP round trip | Highest (`text-embedding-3-small` at $0.02/M tokens; `gemini-embedding`; `voyage`) | ✅ but needs network + a provider that offers embeddings |
| **Tier 2 — static embeddings** (Model2Vec `potion-base-8M`: 30k-token vocab, 384-d, ~32 MB, MTEB avg 56.3, ~1.7 ms/embed) | ~32 MB download, **deferred to first use** | one lazy fetch | Good. Pure lookup + mean-pool + SIF weighting ⇒ implementable in ~100 lines of JS over a pure-JS tokenizer (`@huggingface/tokenizers`, 301 KB, zero deps) | ✅ no ONNX, no native |
| **Tier 3 — local transformer** (EmbeddingGemma-300M, Qwen3-Embedding-0.6B, bge-small, all-MiniLM) | `@huggingface/transformers` 9.5 MB **+ onnxruntime-node + sharp** (hundreds of MB of native binaries) | model download 46 MB–640 MB | Best local quality; EmbeddingGemma runs in <200 MB RAM quantised with Matryoshka truncation 768→512→256→128 | ❌ violates C1, C2, C5 |
| **Tier 3′ — local server** (Ollama / llama.cpp `/v1/embeddings`) | 0 (user already runs it) | one HTTP call | Same as Tier 3 | ✅ if present — probe `localhost` and use it opportunistically |

> **Recommendation:** default **Tier 0**, opportunistically detect **Tier 3′**, offer **Tier 1** as
> an explicit opt-in via `/lessons embeddings on`, and treat **Tier 2** as a v2 feature. Never
> **Tier 3**.

## 2.4 The 2026 argument you should actually settle: do you even need vectors?

This is contested, and the contest matters here more than in most RAG discussions.

**The case against vectors for agent memory.** In May 2025 Anthropic removed vector search from
Claude Code entirely — the embedding pipeline, the local vector DB, and the chunking heuristics —
and replaced it with `grep`; Claude Code's creator reported it *"outperformed everything. By a
lot."* Cursor, Windsurf, Cline, Devin, and Sourcegraph Amp followed with tool-driven search. An
Amazon Science paper at AAAI 2026 measured agentic keyword search at **94.5% of RAG faithfulness
with no vector store at all**. The structural arguments: chunking severs semantic threads; there is
an index to keep in sync; and an agent that can *iterate* on a query beats a single-shot top-k.

**The case for a small vector layer.** LlamaIndex's counter is that lexical search fails on
vocabulary mismatch — "revenue recognition" vs "ASC 606" — and that signal-to-noise collapses as a
corpus grows. The synthesis most 2026 practitioners have landed on is **"small vector layer plus
lots of tools"**, with **Reciprocal Rank Fusion** merging lexical and dense rankings *without*
needing score calibration between them.

**Why this specific corpus tilts lexical.** Lessons are (a) written by the agent and the user in
*their own* project vocabulary, so query and document share terms; (b) short, so chunking never
arises; (c) small, so BM25's precision problems never arise; and (d) *already* going to be consumed
by an agent that can re-query. That is close to the ideal case for BM25 and close to the worst case
for the incremental value of embeddings.

**So: lexical-first, with a fusion slot ready.** Which brings us to the fusion formula.

## 2.5 Fusion: RRF, and the reason it is the right choice here

```
   Lexical ranking (BM25)          Dense ranking (cosine)
   ─────────────────────           ──────────────────────
   rank 1: lesson#42               rank 1: lesson#17
   rank 2: lesson#17               rank 2: lesson#42
   rank 3: lesson#08               rank 3: lesson#93
        │                                 │
        └──────────────┬──────────────────┘
                       ▼
        RRF:   score(d) = Σ   w_r / (k + rank_r(d))          k = 60 (standard)
                          r∈R

        lesson#17 → 1/(60+2) + 1/(60+1) = 0.01613 + 0.01639 = 0.03252   ← 1st
        lesson#42 → 1/(60+1) + 1/(60+2) = 0.01639 + 0.01613 = 0.03252   ← tie
        lesson#08 → 1/(60+3)            = 0.01587
        lesson#93 →            1/(60+3) = 0.01587
```

RRF wins here for a structural reason, not a quality reason: **BM25 scores and cosine similarities
live on incompatible scales**, and SQLite's `bm25()` is additionally *negative* (more negative =
more relevant) and corpus-dependent. Any weighted-sum fusion would require you to normalise scores
whose distributions shift every time a lesson is added. RRF only consumes **ranks**, so it is
immune. It also degrades perfectly: with one retriever it collapses to that retriever's ordering.
Add a third signal (recency, usage-count, scope-match) as another ranked list and the formula is
unchanged.


---

# PART III — The design: `pi-lessons`

## 3.1 Architecture

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║                              pi-lessons                                          ║
╚══════════════════════════════════════════════════════════════════════════════════╝

   PI HOST                          EXTENSION                        STORAGE
   ───────                          ─────────                        ───────

  factory(pi) ────────────────────► index.js
                                      · registerTool  ×3
                                      · registerCommand ×1
                                      · on(...) ×5
                                      · NOTHING ELSE            ← C4
                                          │
  session_start ──────────────────────────┤
    {reason}                              │ compute paths only
                                          │ (no open, no I/O)
                                          │
  before_agent_start ─────────────────────┤
    {prompt, systemPrompt}                ▼
                                    ┌──────────┐   lazy first touch
                                    │  store   │──────────────────────►┌──────────────┐
                                    │  (proxy) │                       │ lessons.db   │
                                    └────┬─────┘                       │  (SQLite)    │
                                         │                             │              │
                            ┌────────────┼────────────┐                │ lessons      │
                            ▼            ▼            ▼                │ lessons_fts  │
                      ┌──────────┐ ┌──────────┐ ┌──────────┐           │ embeddings   │
                      │ lexical  │ │  dense   │ │ recency/ │           │ meta         │
                      │  BM25    │ │ (opt.)   │ │  usage   │           └──────────────┘
                      │ FTS5     │ │ flat scan│ │  prior   │                  ▲
                      └────┬─────┘ └────┬─────┘ └────┬─────┘                  │
                           └────────────┼────────────┘                        │
                                        ▼                                     │
                                  ┌───────────┐                               │
                                  │    RRF    │  k=60, weighted per list      │
                                  │  fusion   │                               │
                                  └─────┬─────┘                               │
                                        │ top-N, token-budgeted               │
  ◄─────────────────────────────────────┘                                     │
    { message: {customType:"lessons", content, display:true} }                │
                                                                              │
  tool_call: lesson_save ───────────────────────────────────────────────────►─┘
  agent_settled  ──────► (optional) harvest candidate lessons
  session_shutdown ────► close db, cancel in-flight, idempotent

  resources_discover ──► { skillPaths: [<pkg>/skills] }   ← progressive disclosure
```

Three tools, one command, one skill. That is the whole public surface:

| Surface | Purpose | Why this shape |
|---|---|---|
| `lesson_search(query, scope?, limit?)` | LLM-driven recall | The agentic-search finding from §2.4: let the model iterate on queries rather than betting on one auto-injection. |
| `lesson_save(title, body, tags?, scope?)` | Explicit capture | Model-callable so the agent can record a lesson the moment it learns one. |
| `lesson_update(uid, ...)` | Correct / supersede | Memory that can't be corrected rots. Soft-supersede, never hard-delete. |
| `/lessons` command | Human surface: list, edit, prune, export, `embeddings on\|off` | Commands get `ExtensionCommandContext` (session control, `waitForIdle`), tools don't. |
| `skills/lessons/SKILL.md` | Teaches the agent *when* to search and *what makes a good lesson* | Only the description sits in the prompt; the body loads on demand. Costs ~30 tokens of standing context. |

## 3.2 Data model

```sql
PRAGMA journal_mode = WAL;        -- multiple pi terminals share ~/.pi/agent
PRAGMA busy_timeout = 5000;       -- ⚠ REQUIRED: concurrent pi processes are normal
PRAGMA foreign_keys = ON;
PRAGMA synchronous = NORMAL;      -- WAL + NORMAL is the right durability/speed point

CREATE TABLE IF NOT EXISTS lessons (
  id            INTEGER PRIMARY KEY,
  uid           TEXT    NOT NULL UNIQUE,      -- ULID: stable across export/import/sync
  title         TEXT    NOT NULL,             -- one line, imperative
  body          TEXT    NOT NULL,             -- the lesson + why + a concrete instance
  scope         TEXT    NOT NULL,             -- 'global' | 'repo:<normalised-remote>' | 'dir:<realpath>'
  tags          TEXT    NOT NULL DEFAULT '',  -- space-separated; indexed as an FTS column
  source_session TEXT,                        -- session id for provenance
  created_at    INTEGER NOT NULL,
  updated_at    INTEGER NOT NULL,
  used_count    INTEGER NOT NULL DEFAULT 0,
  last_used_at  INTEGER,
  confidence    REAL    NOT NULL DEFAULT 0.5,
  superseded_by TEXT,                         -- uid of the lesson that replaces this one
  deleted_at    INTEGER                       -- soft delete; never lose a lesson to a typo
);
CREATE INDEX IF NOT EXISTS lessons_scope_live ON lessons(scope)
  WHERE deleted_at IS NULL AND superseded_by IS NULL;

-- External-content FTS5: no duplicated text, BM25 with per-field weights.
CREATE VIRTUAL TABLE IF NOT EXISTS lessons_fts USING fts5(
  title, body, tags,
  content = 'lessons', content_rowid = 'id',
  tokenize = "porter unicode61 remove_diacritics 2"
);
-- External content requires explicit sync triggers.
CREATE TRIGGER IF NOT EXISTS lessons_ai AFTER INSERT ON lessons BEGIN
  INSERT INTO lessons_fts(rowid,title,body,tags) VALUES (new.id,new.title,new.body,new.tags);
END;
CREATE TRIGGER IF NOT EXISTS lessons_ad AFTER DELETE ON lessons BEGIN
  INSERT INTO lessons_fts(lessons_fts,rowid,title,body,tags)
    VALUES('delete',old.id,old.title,old.body,old.tags);
END;
CREATE TRIGGER IF NOT EXISTS lessons_au AFTER UPDATE ON lessons BEGIN
  INSERT INTO lessons_fts(lessons_fts,rowid,title,body,tags)
    VALUES('delete',old.id,old.title,old.body,old.tags);
  INSERT INTO lessons_fts(rowid,title,body,tags) VALUES (new.id,new.title,new.body,new.tags);
END;

-- Vectors live beside the text. Absent = Tier 0. Never required.
CREATE TABLE IF NOT EXISTS embeddings (
  lesson_id INTEGER PRIMARY KEY REFERENCES lessons(id) ON DELETE CASCADE,
  model     TEXT    NOT NULL,     -- provider/model id: re-embed when this changes
  dim       INTEGER NOT NULL,
  vec       BLOB    NOT NULL,     -- Float32Array buffer, L2-normalised at write time
  created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT NOT NULL);
-- meta: schema_version, embed_model, embed_dim, capability_probe (JSON + timestamp)
```

Ranking query — note `bm25()` is **negative**, most relevant first, and takes per-column weights:

```sql
SELECT l.id, l.uid, l.title, bm25(lessons_fts, 3.0, 1.0, 2.0) AS score
FROM lessons_fts
JOIN lessons l ON l.id = lessons_fts.rowid
WHERE lessons_fts MATCH :q
  AND l.deleted_at IS NULL AND l.superseded_by IS NULL
  AND l.scope IN ('global', :projectScope)
ORDER BY score          -- ascending: more negative = better
LIMIT :k;
```

**Storage location.** Prefer `$PI_CODING_AGENT_DIR` if set, else `~/.pi/agent/`, and put the file at
`<agentDir>/lessons/lessons.db`. For any *project-local* config, import `CONFIG_DIR_NAME` from
`@earendil-works/pi-coding-agent` instead of hard-coding `.pi` — rebranded distributions use a
different directory name — and gate the read behind `ctx.isProjectTrusted()`.

## 3.3 The degradation ladder

Every capability is probed once, cached in `meta`, and never allowed to break the extension.

```
  ┌─ TIER 3′ ── local embedding server (Ollama / llama.cpp /v1/embeddings) ─┐
  │   probe: 200ms-timeout GET to a configured/known localhost port         │
  │   if up → dense list joins the fusion                                   │
  └─────────────────────────────┬───────────────────────────────────────────┘
                                │ unavailable
  ┌─ TIER 1 ── remote embedding API (opt-in only) ──────────────────────────┐
  │   ctx.modelRegistry.getProviderAuth(id) → {apiKey, baseUrl, headers}    │
  │   fetch(baseUrl + "/embeddings", { signal: ctx.signal })                │
  │   requires: user ran `/lessons embeddings on`  AND  !PI_OFFLINE         │
  └─────────────────────────────┬───────────────────────────────────────────┘
                                │ unavailable / declined
  ┌─ TIER 0 ── LEXICAL ONLY (the default, and always sufficient) ───────────┐
  │   node:sqlite + FTS5 + bm25()                                           │
  │   └─ if node:sqlite import throws (exotic runtime):                     │
  │        FALLBACK A: node-sqlite3-wasm  (1.3 MB, no native, optional dep) │
  │        FALLBACK B: JSONL file + in-process MiniSearch-style BM25        │
  │        FALLBACK C: JSONL + naive scan  ← still useful at ≤200 lessons   │
  └─────────────────────────────────────────────────────────────────────────┘

  ORTHOGONAL: sqlite-vec probe.  If vectors exist AND sqlite-vec loads AND the
  corpus exceeds ~20k rows, switch KNN from flat scan to vec0. Below that the
  flat scan wins on simplicity (see §2.1 measurements). Most users never trip it.
```

The probe itself must be cheap and must never run in the factory:

```
  probeCapabilities()            runs at most once per install-version, cached in meta
  ─────────────────────────────────────────────────────────────────────────────────
  1. try { await import("node:sqlite") }              → sqlite: true/false     ~0.5 ms
  2. CREATE VIRTUAL TABLE t USING fts5(x)             → fts5:   true/false     ~0.3 ms
     (in a :memory: db, then discard)
  3. bm25() available?  SELECT bm25(t) …              → bm25:   true/false
  4. (deferred, only when vectors are enabled)
     sqliteVec.load(db); select vec_version()         → vec0:   true/false
  Total budget: < 2 ms. Store {version, results, ts} in meta.capability_probe.
```

## 3.4 CRITICAL PERIOD #1 — Installation

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║  T0 · INSTALL          `pi install npm:pi-lessons`                               ║
╚══════════════════════════════════════════════════════════════════════════════════╝

 STEP                                    WHAT WE DO                    WHY IT'S SAFE
 ────                                    ──────────                    ─────────────
 1  parseSource → npm, unpinned          —                             —
 2  ensureNpmProject(~/.pi/agent/npm)    —                             —
 3  npm install pi-lessons \             tarball ≈ 60 KB               • dependencies: {}
      --prefix ~/.pi/agent/npm \         no postinstall script         • no native addon
      --legacy-peer-deps                 no compilation                • no download
                                                                       • peers not resolved
                                                                         (by design)
 4  settings.json += "npm:pi-lessons"    —                             —

 ┌──────────────────────────────────────────────────────────────────────────────┐
 │  package.json — the exact shape that makes all of the above true             │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │  {                                                                           │
 │    "name": "pi-lessons",                                                     │
 │    "version": "1.0.0",                                                       │
 │    "type": "module",              ◄── F2: makes dist/*.js take jiti's        │
 │    "engines": { "node": ">=22.19.0" },   native-import path (4 ms, no cache) │
 │    "keywords": ["pi-package"],    ◄── required for the pi.dev gallery        │
 │    "files": ["dist", "skills", "README.md"],                                 │
 │    "dependencies": {},            ◄── C1: nothing to build, nothing to fetch │
 │    "optionalDependencies": {                                                 │
 │      "sqlite-vec": "0.1.9"        ◄── OPTIONAL: install failure is tolerated │
 │    },                                                                        │
 │    "peerDependencies": {          ◄── "*" and never bundled; the host        │
 │      "@earendil-works/pi-coding-agent": "*",   injects these via jiti        │
 │      "@earendil-works/pi-ai": "*",             aliases / Bun virtualModules  │
 │      "@earendil-works/pi-tui": "*",                                          │
 │      "typebox": "*"                                                          │
 │    },                                                                        │
 │    "pi": {                                                                   │
 │      "extensions": ["./dist/index.js"],  ◄── manifest path: literal existence│
 │      "skills":     ["./skills"]              check, so ANY filename works    │
 │    }                                                                         │
 │  }                                                                           │
 └──────────────────────────────────────────────────────────────────────────────┘

 ⚠ If you also publish a git-installable tag: COMMIT dist/. Git installs run
   `npm install --omit=dev` and never run your build script.
 ⚠ Do NOT put sqlite-vec in `dependencies`. On Alpine, Termux, and windows-arm64
   there is no prebuilt, and a hard dep turns a soft downgrade into a failed install.
   `optionalDependencies` + a runtime probe is the correct pattern.
```

## 3.5 CRITICAL PERIOD #2 — First startup after install

This is the moment the design lives or dies. Compare the naive build against this one.

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║  T1 · FIRST `pi` RUN AFTER INSTALL                                               ║
╚══════════════════════════════════════════════════════════════════════════════════╝

  NAIVE BUILD (what most pi memory packages do)      THIS DESIGN
  ─────────────────────────────────────────────      ───────────────────────────────
  jiti.import("./src/index.ts")                      jiti.import("./dist/index.js")
    Babel transpile, cache miss                        "type":"module" → native import
    ████████████████████████████ ~1000 ms              ▎ ~4 ms
                                                     
  factory():                                         factory():
    open better-sqlite3 (.node dlopen) ~15 ms          register 3 tools           ~0.2 ms
    run migrations                     ~20 ms          register 1 command
    load 3k lessons into memory        ~40 ms          register 5 handlers
    fetch embedding model list (net)  ~300 ms          ── return ──               ~0.3 ms
    start a file watcher               ~5 ms         
    ██████████████ ~380 ms                             ▎ <1 ms
                                                     
  session_start:                                     session_start:
    build in-memory index             ~60 ms           resolve scope key (git remote
                                                        from a CACHED value, or defer)
                                                       schedule nothing               ~1 ms
                                                     
  ─────────────────────────────────                  ─────────────────────────────────
  ≈ 1.44 s added to EVERY pi start                   ≈ 5 ms added to every pi start
    (and ~440 ms even when warm)                       (and 5 ms when warm — no cache
                                                        dependency at all)
```

The first *actual* database work happens on the first recall or the first `lesson_save`, off the
startup path entirely:

```
  FIRST TOUCH (lazy) — inside before_agent_start or a tool call
  ─────────────────────────────────────────────────────────────
   ensureStore()
     ├─ if (db) return db                                        ~0 ms (subsequent calls)
     ├─ mkdir -p <agentDir>/lessons
     ├─ new DatabaseSync(path)                                   ~0.4 ms
     ├─ PRAGMA journal_mode=WAL; busy_timeout=5000; …            ~0.1 ms
     ├─ migrate():  PRAGMA user_version → apply deltas           ~6 ms on creation
     │                                                            ~0.1 ms thereafter
     ├─ prepare() the 6 hot statements, cache them               ~0.3 ms
     └─ probeCapabilities() if meta is stale                     ~2 ms
   ────────────────────────────────────────────────────────────
   FIRST EVER:  ~9 ms      ALREADY EXISTS:  ~1 ms      CACHED IN-PROCESS:  0 ms
```

### First-run UX: do not prompt during startup

`ctx.ui.confirm()` inside `session_start` blocks the TUI before it is even drawn, and in
`print`/`json` mode `ctx.hasUI` is `false` so it cannot work at all. The correct pattern:

```
  session_start
    ├─ if (isFirstEverRun) {
    │     if (ctx.hasUI) ctx.ui.setStatus("lessons", "lessons: ready · /lessons to configure");
    │     // NO confirm(), NO select(), NO await on user input
    │  }
    └─ return   ← startup continues immediately

  /lessons setup      ← the human opts in when they choose to
    ├─ ctx.ui.select("Enable semantic recall?", ["No (lexical only)", "Local Ollama", "Provider API"])
    └─ writes meta.embed_model, then backfills embeddings in the background
```

### Time budget you should hold yourself to

| Phase | Budget | This design *(measured components)* |
|---|---|---|
| Module load (jiti) | ≤ 10 ms | **~4 ms** (native ESM import) |
| `factory()` | ≤ 2 ms | **<1 ms** (registration only) |
| `session_start` | ≤ 5 ms | **~1 ms** (no I/O) |
| First `ensureStore()` | ≤ 20 ms | **~9 ms** first ever, ~1 ms after |
| `before_agent_start` recall | ≤ 15 ms | BM25 **<2 ms** + optional flat scan **~5 ms** |
| Steady-state per turn | ≤ 5 ms | **~2 ms** |

Verify with `PI_TIMING=1 pi --version` and read the `extensions` timing group — Pi instruments
`<path> module import` and `<path> factory` separately, so you can see both numbers directly.

## 3.6 CRITICAL PERIOD #3 — Steady state: the per-turn hot path

```
  USER TYPES A PROMPT
        │
        ├─► input                (we do not intercept — cheap, avoid the coupling)
        │
        ├─► before_agent_start ─────────────────────────────────────────────────┐
        │     event.prompt, event.systemPrompt, event.systemPromptOptions       │
        │                                                                       │
        │     1. gate:  skip if prompt.length < 12, or if the last N turns      │
        │               already carried an injection (avoid re-injecting        │
        │               the same lessons every turn → prompt-cache churn)       │
        │     2. query = extractTerms(prompt)  (dedupe, drop stopwords,         │
        │               OR-join, escape FTS5 syntax)                            │
        │     3. bm25List  = fts5Search(query, k=20)                 <2 ms      │
        │     4. denseList = enabled ? flatScan(embed(prompt), k=20) : []       │
        │     5. priorList = recency×usage ordering of scope-matched lessons    │
        │     6. fused     = rrf([bm25List×1.0, denseList×0.8, priorList×0.3])  │
        │     7. clip to a TOKEN BUDGET (default 800 tok ≈ 6–8 lessons)         │
        │     8. bump used_count / last_used_at for what was injected           │
        │                                                                       │
        │     return { message: { customType: "lessons",                        │
        │                         content: renderLessons(fused),                │
        │                         display: true } }                             │
        └───────────────────────────────────────────────────────────────────────┘
              ▲
              │  ⚠ RETURN A MESSAGE, NOT A systemPrompt EDIT.
              │  before_agent_start chains: mutating systemPrompt rebuilds the
              │  prompt prefix and can invalidate the provider's prompt cache
              │  every single turn. An injected message appends at the tail,
              │  which is cache-friendly.
        │
        ├─► agent runs, may call lesson_search itself (agentic recall — §2.4)
        │
        └─► agent_settled          ctx.isIdle() === true here
              │
              └─ optional harvest: if the turn contained a corrected mistake
                 (heuristics: a failed tool_result followed by a successful retry
                  on the same target; an explicit "actually…" from the user),
                 queue a candidate and surface it via pi.appendEntry("lesson-candidate")
                 — a durable TUI card that is NOT in LLM context. The human
                 accepts with one keystroke. Never auto-write unreviewed lessons.
```

### Why `agent_settled`, not `agent_end`

`agent_end` fires when a low-level run ends — but Pi may still auto-retry, auto-compact and retry,
or drain queued follow-up messages. `agent_settled` is the only event that guarantees Pi will not
continue on its own. Harvesting on `agent_end` means you harvest mid-recovery, from a transcript
that is about to change.

## 3.7 Pi-specific correctness hazards (the ones that will actually bite)

```
 ┌──────────────────────────────────────────────────────────────────────────────┐
 │ H1 · SESSION BRANCHING                                                       │
 │  /fork, /clone, and /tree navigation rewrite which entries are "live".       │
 │  Pi's documented pattern: store extension state in the TOOL RESULT `details` │
 │  and rebuild it on session_start from ctx.sessionManager.getBranch().        │
 │  ⇒ Our durable data is a DB outside the session, which is CORRECT for        │
 │    lessons (a lesson learned on an abandoned branch is still true). But      │
 │    per-session state — "which lessons did I already inject?" — MUST be       │
 │    rebuilt from the branch, or a fork will re-inject everything.             │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │ H2 · STALE ctx AFTER SESSION REPLACEMENT                                     │
 │  After ctx.newSession/fork/switchSession/reload, a captured `pi` or command  │
 │  `ctx` THROWS. Inside withSession, use only the ctx passed to withSession.   │
 │  A captured ctx.sessionManager reference is likewise a dead object.          │
 │  ⇒ Never close over ctx. Take what you need as plain data (strings, ids).    │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │ H3 · session_shutdown MUST BE IDEMPOTENT                                     │
 │  It fires for quit, reload, new, resume, AND fork — and /reload emits it     │
 │  while your command handler is still running in the old call frame.          │
 │  ⇒ `if (db) { db.close(); db = null; }`  + abort any in-flight fetch.        │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │ H4 · CONCURRENT PI PROCESSES                                                 │
 │  Users run pi in several terminals against the same ~/.pi/agent.             │
 │  ⇒ WAL + PRAGMA busy_timeout=5000. Wrap multi-statement writes in a single   │
 │    transaction. Never hold a write txn across an await.                      │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │ H5 · MODE GUARDS                                                             │
 │  ctx.hasUI is false in print (-p) and json mode; ctx.mode === "tui" is       │
 │  required for ctx.ui.custom() and component factories. RPC mode has UI but   │
 │  some TUI methods are no-ops.                                                │
 │  ⇒ Guard every dialog. A tool that blocks on confirm() in -p mode hangs CI.  │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │ H6 · TOOL OUTPUT TRUNCATION IS MANDATORY                                     │
 │  Built-in limit: 50 KB / 2000 lines. Import truncateHead / truncateTail /    │
 │  DEFAULT_MAX_BYTES / DEFAULT_MAX_LINES from @earendil-works/pi-coding-agent. │
 │  ⇒ lesson_search must cap results AND per-lesson body length, and say so.    │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │ H7 · promptGuidelines ARE FLATTENED                                          │
 │  Bullets are appended to the shared Guidelines section with no tool-name     │
 │  prefix. "Use this tool when…" is ambiguous to the model.                    │
 │  ⇒ Write "Use lesson_search when…", naming the tool explicitly.              │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │ H8 · ENUMS AND GOOGLE                                                        │
 │  Type.Union / Type.Literal break Google's API. Use StringEnum from           │
 │  @earendil-works/pi-ai for every string enum in a tool schema.               │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │ H9 · SCHEMA DRIFT ON RESUMED SESSIONS                                        │
 │  Resuming an old session replays tool calls whose stored arguments may not   │
 │  match your current schema. Implement prepareArguments(args) to fold legacy  │
 │  shapes forward. Keep `parameters` strict; do the compat work in the shim.   │
 ├──────────────────────────────────────────────────────────────────────────────┤
 │ H10 · PI_OFFLINE                                                             │
 │  Pi disables startup network operations when PI_OFFLINE is set. Honour it:   │
 │  skip Tier 1/3′ probes and fall to Tier 0 silently.                          │
 └──────────────────────────────────────────────────────────────────────────────┘
```

## 3.8 CRITICAL PERIOD #4 — Upgrade

```
╔══════════════════════════════════════════════════════════════════════════════════╗
║  T3 · UPGRADE       `pi update --extensions`   /   `pi install npm:pi-lessons@2` ║
╚══════════════════════════════════════════════════════════════════════════════════╝

   pinned "npm:pi-lessons@1.0.0"     ──► SKIPPED by pi update (by design)
   unpinned "npm:pi-lessons"         ──► npm install pi-lessons --prefix … --legacy-peer-deps
   git ref                           ──► fetch + reset --hard + clean, then npm install --omit=dev

        │
        ▼
   NEXT `pi` START
        │
        ├─ module load: still ~4 ms  ← because it's native ESM, there is no cache to miss.
        │  (A .ts-shipping extension pays the FULL ~1000 ms transpile again here, because
        │   the jiti cache key embeds hash(source) — every release invalidates it.)
        │
        └─ first ensureStore():
             migrate():
               v = PRAGMA user_version
               while (v < CURRENT) { apply delta[v]; v++ ; PRAGMA user_version = v }
               ──────────────────────────────────────────────────────────────────
               RULES:
                 • Every migration is a pure SQL delta, forward-only, in one txn.
                 • ADD COLUMN only. Never DROP; never rename in place.
                 • If a migration would rebuild lessons_fts, do it lazily and
                   in the background — INSERT INTO lessons_fts(lessons_fts)
                   VALUES('rebuild') on 5k rows is fast, but do not block a turn.
                 • If embed_model changed: mark existing embeddings stale, do NOT
                   delete them, and re-embed opportunistically (a mixed-model
                   corpus degrades ranking; an empty one loses the signal entirely).
                 • Refuse to open a DB whose user_version > CURRENT (a newer
                   pi-lessons wrote it). Warn, fall back to read-only lexical.

   DOWNGRADE / ROLLBACK
        └─ Because the DB is outside the package directory, `pi remove` and
           version rollback never destroy data. Ship `/lessons export` (JSONL)
           and make it the documented backup path.
```

## 3.9 CRITICAL PERIOD #5 — Removal

```
   pi remove npm:pi-lessons
     ├─ npm uninstall pi-lessons --prefix ~/.pi/agent/npm --legacy-peer-deps
     └─ settings.json -= "npm:pi-lessons"

   ~/.pi/agent/lessons/lessons.db   ── SURVIVES.  This is correct and intentional:
                                       the user's accumulated knowledge must not be
                                       collateral damage of a package uninstall.
                                       Document the path in the README and provide
                                       `/lessons purge --yes-really` for a clean wipe.
```


---

# PART IV — Concrete skeleton

Layout. Source in TypeScript for your own sanity; **ship compiled ESM**.

```
pi-lessons/
├── package.json            ← §3.4  ("type":"module", pi.extensions → dist/index.js)
├── tsconfig.json           ← "module":"NodeNext", "target":"ES2023", "outDir":"dist"
├── src/
│   ├── index.ts            ← factory: registration ONLY
│   ├── store.ts            ← lazy DatabaseSync, migrations, prepared statements
│   ├── search.ts           ← bm25 / flatScan / rrf
│   ├── embed.ts            ← tier probe + provider fetch (abort-aware)
│   ├── render.ts           ← TUI renderers + lesson formatting
│   └── probe.ts            ← capability probe, cached in meta
├── dist/                   ← BUILT, and COMMITTED if you support git installs
└── skills/lessons/SKILL.md ← progressive disclosure
```

### `src/index.ts` — the factory does nothing but register

```typescript
import type { ExtensionAPI, ExtensionContext } from "@earendil-works/pi-coding-agent";
import { truncateHead, DEFAULT_MAX_BYTES, DEFAULT_MAX_LINES } from "@earendil-works/pi-coding-agent";
import { StringEnum } from "@earendil-works/pi-ai";
import { Type } from "typebox";
import { openStore, closeStore, type Store } from "./store.js";
import { recall, saveLesson } from "./search.js";

export default function (pi: ExtensionAPI) {
  // ── module-scope state. NO I/O here. NO awaits here. ─────────────────────
  let store: Store | null = null;
  let scopeKey = "global";
  let injectedThisBranch = new Set<string>();
  const ensure = async (ctx: ExtensionContext): Promise<Store> =>
    (store ??= await openStore(ctx));               // lazy first touch (§3.5)

  // ── session lifecycle ────────────────────────────────────────────────────
  pi.on("session_start", async (_event, ctx) => {
    scopeKey = deriveScopeKey(ctx.cwd);             // pure string work, no exec
    injectedThisBranch = new Set();
    // Rebuild per-branch state (H1) from the live branch, not from memory:
    for (const entry of ctx.sessionManager.getBranch()) {
      if (entry.type === "message" && entry.message.role === "toolResult"
          && entry.message.toolName === "lesson_search") {
        for (const uid of entry.message.details?.uids ?? []) injectedThisBranch.add(uid);
      }
    }
    if (ctx.hasUI) ctx.ui.setStatus("lessons", "lessons ready");   // fire-and-forget
  });

  pi.on("session_shutdown", async () => {           // idempotent (H3)
    closeStore(store); store = null;
  });

  pi.on("resources_discover", async () => ({        // progressive disclosure
    skillPaths: [new URL("../skills", import.meta.url).pathname],
  }));

  // ── automatic recall ─────────────────────────────────────────────────────
  pi.on("before_agent_start", async (event, ctx) => {
    if (event.prompt.length < 12) return;
    const s = await ensure(ctx);
    const hits = await recall(s, event.prompt, { scopeKey, signal: ctx.signal,
                                                 exclude: injectedThisBranch, tokenBudget: 800 });
    if (!hits.length) return;
    for (const h of hits) injectedThisBranch.add(h.uid);
    return {                                         // message, NOT systemPrompt (§3.6)
      message: { customType: "lessons", content: renderForModel(hits), display: true },
    };
  });

  // ── tools ────────────────────────────────────────────────────────────────
  pi.registerTool({
    name: "lesson_search",
    label: "Search Lessons",
    description: "Search previously recorded lessons by keyword or topic. Returns ranked lessons "
               + "with their uid, title and body. Results are truncated; narrow the query to see more.",
    promptSnippet: "Search recorded lessons for prior knowledge about this codebase or task",
    promptGuidelines: [                              // must name the tool (H7)
      "Use lesson_search before proposing an approach in an unfamiliar area of the codebase.",
      "Use lesson_save when you discover a non-obvious constraint that would help a future session.",
    ],
    parameters: Type.Object({
      query: Type.String({ description: "Keywords or a natural-language topic" }),
      scope: Type.Optional(StringEnum(["auto", "project", "global"] as const)),
      limit: Type.Optional(Type.Integer({ minimum: 1, maximum: 20 })),
    }),
    async execute(_id, params, signal, _onUpdate, ctx) {
      const s = await ensure(ctx);
      const hits = await recall(s, params.query,
        { scopeKey, signal, limit: params.limit ?? 8, exclude: new Set() });
      const t = truncateHead(renderForModel(hits),
        { maxLines: DEFAULT_MAX_LINES, maxBytes: DEFAULT_MAX_BYTES });   // H6
      return {
        content: [{ type: "text", text: t.content + (t.truncated ? "\n[truncated]" : "") }],
        details: { uids: hits.map(h => h.uid) },     // H1: branch-rebuildable state
      };
    },
  });

  pi.registerTool({
    name: "lesson_save",
    label: "Save Lesson",
    description: "Record a durable lesson: a non-obvious fact, constraint, or correction that "
               + "should inform future sessions. Title must be one imperative line.",
    parameters: Type.Object({
      title: Type.String({ maxLength: 120 }),
      body:  Type.String({ description: "The lesson, why it matters, and one concrete instance" }),
      tags:  Type.Optional(Type.String({ description: "Space-separated tags" })),
      scope: Type.Optional(StringEnum(["project", "global"] as const)),
    }),
    async execute(_id, params, _signal, _onUpdate, ctx) {
      const s = await ensure(ctx);
      const uid = saveLesson(s, { ...params, scopeKey, sessionId: ctx.sessionManager.getSessionId() });
      return { content: [{ type: "text", text: `Saved lesson ${uid}` }], details: { uid } };
    },
  });

  // lesson_update(uid, …) — same shape; soft-supersede, never hard-delete.

  // ── human surface ────────────────────────────────────────────────────────
  pi.registerCommand("lessons", {
    description: "List, edit, prune, export lessons; configure semantic recall",
    getArgumentCompletions: (p) =>
      ["list", "setup", "export", "prune", "embeddings on", "embeddings off"]
        .filter(v => v.startsWith(p)).map(v => ({ value: v, label: v })) || null,
    handler: async (args, ctx) => { /* ctx here is ExtensionCommandContext */ },
  });
}
```

### `src/store.ts` — the lazy, migrating, concurrency-safe store

```typescript
import { mkdirSync } from "node:fs";
import { homedir } from "node:os";
import { join } from "node:path";

export type Store = { db: any; caps: Caps; stmt: Record<string, any> };
const SCHEMA_VERSION = 1;

export async function openStore(ctx: { cwd: string }): Promise<Store> {
  const { DatabaseSync } = await import("node:sqlite");        // ~0.5 ms
  const agentDir = process.env.PI_CODING_AGENT_DIR ?? join(homedir(), ".pi", "agent");
  const dir = join(agentDir, "lessons");
  mkdirSync(dir, { recursive: true });

  const db = new DatabaseSync(join(dir, "lessons.db"));
  db.exec(`PRAGMA journal_mode=WAL; PRAGMA busy_timeout=5000;
           PRAGMA foreign_keys=ON;  PRAGMA synchronous=NORMAL;`);   // H4

  const cur = db.prepare("PRAGMA user_version").get().user_version as number;
  if (cur > SCHEMA_VERSION) throw new Error("lessons.db written by a newer pi-lessons");
  for (let v = cur; v < SCHEMA_VERSION; v++) {
    db.exec("BEGIN"); db.exec(MIGRATIONS[v]); db.exec(`PRAGMA user_version=${v + 1}`); db.exec("COMMIT");
  }

  const stmt = {                       // prepare once, reuse forever  (~0.05 ms each)
    search: db.prepare(`SELECT l.id,l.uid,l.title,l.body,bm25(lessons_fts,3.0,1.0,2.0) s
                        FROM lessons_fts JOIN lessons l ON l.id=lessons_fts.rowid
                        WHERE lessons_fts MATCH ? AND l.deleted_at IS NULL
                          AND l.superseded_by IS NULL AND l.scope IN ('global', ?)
                        ORDER BY s LIMIT ?`),
    insert: db.prepare(`INSERT INTO lessons(uid,title,body,scope,tags,source_session,
                          created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)`),
    bump:   db.prepare(`UPDATE lessons SET used_count=used_count+1,last_used_at=? WHERE uid=?`),
  };
  return { db, caps: await probe(db), stmt };
}

export function closeStore(s: Store | null) { if (s) { try { s.db.close(); } catch {} } }  // H3
```

### `src/search.ts` — fusion

```typescript
export function rrf(lists: { ids: string[]; weight: number }[], k = 60) {
  const acc = new Map<string, number>();
  for (const { ids, weight } of lists)
    ids.forEach((id, i) => acc.set(id, (acc.get(id) ?? 0) + weight / (k + i + 1)));
  return [...acc.entries()].sort((a, b) => b[1] - a[1]).map(([id]) => id);
}

// FTS5 query construction — escape aggressively, the model's prompt is untrusted input.
export function toMatchQuery(text: string): string {
  const terms = text.toLowerCase().match(/[a-z0-9_][a-z0-9_.\-]{2,}/g) ?? [];
  const uniq = [...new Set(terms)].filter(t => !STOPWORDS.has(t)).slice(0, 24);
  return uniq.map(t => `"${t.replace(/"/g, '""')}"`).join(" OR ");   // quoting neutralises
}                                                                     // FTS5 operators
```

### `skills/lessons/SKILL.md`

```markdown
---
name: lessons
description: Recall and record durable project lessons. Use when starting work in an
  unfamiliar area, when an approach has failed before, or when you discover a
  non-obvious constraint worth remembering for future sessions.
---

# Lessons

## Recall
Call `lesson_search` with the concepts you are about to touch — file names, subsystem
names, error strings. Prefer two or three narrow searches over one broad one.

## Record
Call `lesson_save` when, and only when, all of these hold:
- the fact was **non-obvious** (it cost time to discover),
- it is **durable** (still true next week),
- it is **actionable** (it changes what someone would do).

Good: "Run `npm run build:offline` in CI — the online build hits provider catalogues
and flakes." Bad: "The tests are in `test/`."

Write the title as one imperative line. Put the reason and one concrete instance in
the body. If a lesson is now wrong, use `lesson_update` to supersede it rather than
saving a contradicting one.
```

---

# PART V — Checklist, evaluation, risks

## 5.1 Ship checklist

```
  PACKAGING
  [ ] "type": "module"                                      ← the 250× startup win
  [ ] pi.extensions → ["./dist/index.js"]  (compiled)
  [ ] dependencies: {}     optionalDependencies: sqlite-vec only
  [ ] peerDependencies: @earendil-works/pi-* + typebox, all "*"
  [ ] keywords: ["pi-package"]   (+ optional pi.image / pi.video for the gallery)
  [ ] engines.node >= 22.19.0
  [ ] files: ["dist","skills","README.md"]     — and commit dist/ for git installs
  [ ] no postinstall / prepare script that the install path depends on

  RUNTIME
  [ ] factory does zero I/O, zero awaits, zero timers, zero watchers
  [ ] every resource opened in session_start (or later) is closed in session_shutdown
  [ ] session_shutdown is idempotent and handles reason ∈ {quit,reload,new,resume,fork}
  [ ] all ctx.ui dialogs guarded by ctx.hasUI; custom() guarded by ctx.mode==="tui"
  [ ] tool output passes through truncateHead/truncateTail
  [ ] StringEnum (not Type.Union) for every string enum
  [ ] prepareArguments() shim for any schema you have already shipped
  [ ] PI_OFFLINE respected
  [ ] no captured ctx / sessionManager across session replacement
  [ ] WAL + busy_timeout; no write transaction held across an await

  VERIFY
  [ ] PI_TIMING=1 pi --version   → your "module import" and "factory" lines are single-digit ms
  [ ] pi -p "hello"              → no hang, no UI calls
  [ ] pi -e ./dist/index.js      → loads standalone
  [ ] fresh container, npm-installed pi  → install + first run clean
  [ ] standalone Bun binary pi   → install + first run clean   ← the one people skip
  [ ] Termux / Alpine            → degrades to Tier 0 without an error
```

## 5.2 How to know it works (build this before you tune anything)

The single most repeated piece of advice in the 2026 embedding literature is that leaderboard
numbers do not predict performance on your corpus — a 50–100 query eval set from your own data
"takes an afternoon and saves weeks." For lessons specifically:

```
  eval/queries.jsonl     { "q": "why does the build flake in CI", "relevant": ["01H…","01H…"] }
  ────────────────────────────────────────────────────────────────────────────────
  Metrics:  Recall@8   (did the right lesson make it into the injection budget?)
            MRR        (was it near the top?)
            Injection precision (what fraction of injected lessons were relevant? —
                        this is the one that governs whether users turn the feature off)
  Ablations: Tier 0 alone  vs  Tier 0+dense+RRF  vs  dense alone
             ⇒ if Tier 0 alone is within a couple of points, do not ship embeddings.
```

## 5.3 Residual risks and open questions

| Risk | Severity | Mitigation |
|---|---|---|
| Bun standalone binary's `node:sqlite` differs from Node's (FTS5 present? `loadExtension` allowed?) | **High** — this is the one thing I could not verify locally | The capability probe (§3.3) already covers it; make Tier 0's own fallback chain real (WASM → JSONL), and test on the binary before release |
| jiti's native-import fast path is an implementation detail of jiti 2.7 / Pi's loader config | Medium | It is also the *documented* jiti behaviour for ESM, and Pi pins jiti exactly. Re-measure with `PI_TIMING=1` on each Pi minor bump |
| Auto-injection annoys users / burns context | Medium | Token budget, per-branch dedupe, and a hard off switch. Consider shipping auto-injection **off** and relying on `lesson_search` — that is the agentic-search position from §2.4 |
| Injected messages change the prompt tail every turn | Low–Medium | Injecting a message (not a system-prompt edit) keeps the cached prefix intact; the dedupe gate stops per-turn churn |
| sqlite-vec is pre-1.0 with 3 uncovered platforms | Low | Optional dependency + probe; flat scan is the default anyway |
| Embedding model change invalidates the corpus | Low | `embeddings.model` column, stale-marking, opportunistic re-embed, never delete |
| Lessons rot | **Underrated** | `superseded_by`, `confidence`, `used_count`, and a `/lessons prune` that surfaces never-retrieved lessons older than N days. A memory system without a forgetting story becomes noise |
| Secrets captured into lessons | Medium | Scan `body` on write for high-entropy strings and common key prefixes; refuse or redact. `pi-hermes-memory` ships secret scanning for exactly this reason |

## 5.4 Sources

Primary (read directly): the published `@earendil-works/pi-coding-agent@0.84.1` tarball —
`dist/core/extensions/loader.js`, `dist/core/package-manager.js`, `dist/core/resource-loader.js`,
`dist/main.js`, `dist/core/timings.js`, `dist/config.js`, and the bundled `docs/`. Plus
`jiti@2.7.0`'s `prepareCacheDir` / `evalModule`. Registry metadata for every package in the
comparison tables. Measurements run locally on Node v22.22.2 (SQLite 3.51.2), 1 vCPU Xeon @2.8 GHz.

Secondary: pi.dev documentation and package gallery; Anthropic's removal of vector search from
Claude Code and the subsequent agentic-search literature (incl. the AAAI 2026 Amazon Science
result); LlamaIndex's lexical-vs-semantic counter-argument; Model2Vec / potion-base-8M and
EmbeddingGemma model cards and 2026 embedding-model surveys.
