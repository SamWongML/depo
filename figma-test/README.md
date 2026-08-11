# figma-probe

Determines **which routes into your Figma file are actually open**, and at what cost,
without burning the scarce Tier-1 REST budget to find out.

Stdlib-only Python 3.9+. No `pip install`. Nothing is sent anywhere except
`api.figma.com` and `www.figma.com`.

## Run it

```bash
export FIGMA_TOKEN='figd_...'          # never pass --token on the CLI; it lands in shell history

# 1. See the plan and its cost. Calls nothing.
python3 figma_probe.py --url 'https://www.figma.com/design/KEY/App?node-id=24626-100' --dry-run

# 2. Safe run. Tier 1 is gated OFF, so your monthly quota cannot be spent.
python3 figma_probe.py --url 'https://www.figma.com/design/KEY/App?node-id=24626-100'

# 3. Only after step 2 tells you the seat class, spend Tier 1 deliberately.
python3 figma_probe.py --url '...' --allow-tier1
```

Run step 2 first. It costs 2 Tier-3 calls and 4 Tier-2 calls — both cheap even on
the worst seat — and it tells you whether Tier 1 is worth touching at all.

To prove the clipboard route: select frames in Figma, press ⌘C, then re-run.

## Safety properties (all covered by `test_probe.py`)

| Property | Mechanism |
| --- | --- |
| Monthly quota cannot be spent by accident | Tier 1 gated behind `--allow-tier1`; a second gate (`--force`) if a `low` bucket was already detected |
| A 429 never causes a second charge | No retry logic anywhere. First `429` + `rate-limit-type: low` opens a circuit breaker that skips all remaining API probes |
| The token never reaches disk | Only a `sha256[:12]` fingerprint is logged; response bodies are passed through `redact()` |
| A crash mid-run loses nothing | Every record is `write` + `flush` + `fsync`ed before the next probe starts |
| Probes run cheapest-first | Phase 0 local → Phase 1 Tier 3 → Phase 2 Tier 2 → Phase 3 Tier 1 |

## Outputs

```
figma-audit/<run-id>/
├── audit.jsonl     append-only, one JSON object per probe
├── verdict.json    machine-readable route feasibility (feed this to pi)
├── summary.md      human-readable report
└── raw/            every response body, content-addressed by sha256
```

## Probe catalog

| ID | Tier | Question it answers |
| --- | --- | --- |
| E01 | — | Host, arch, Python |
| E02 | — | Proxy / CA trust — will TLS to api.figma.com survive the corporate MITM? |
| E03 | — | Figma desktop version; is the CDP port expected to be stripped (≥126.1.x)? |
| E04 | — | Is node/npm present? Required for any kiwi decoding |
| A01 | 3 | Does the token authenticate via `X-Figma-Token`, and as whom? |
| A02 | 3 | Fallback: does it work as `Authorization: Bearer` instead? (only on 401) |
| A03 | 3 | Does the file exist, can this account see it, **which plan owns it**? |
| B01 | 2 | Version history — the correct cache key for an offline pipeline |
| B02 | 2 | Comments (often document flow intent) |
| B03 | 2 | Dev resources — are design→code links already mapped? |
| B04 | 2 | Local variables — the state machine behind CONDITIONAL navigation |
| C01 | **1** | Page enumeration at `depth=1`, and the size cost of a full dump |
| C02 | **1** | Does the REST payload actually carry prototype routing, or the lossy shape? |
| D01 | — | Is any CDP endpoint listening on 9222/9223/9225/3845? |
| D02 | — | Is a `.fig` on disk, and does it structurally parse? |
| D03 | — | Is a kiwi payload sitting on the clipboard right now? |
| D04 | — | Figma desktop cache (informational) |
| D05 | — | Does the design URL challenge an anonymous automated client? |
| D06 | — | Same for the prototype presentation view |

## `audit.jsonl` record schema

Record `seq: 0` is always a `run_header` carrying the full invocation context, so
a log file is self-describing with no external state.

Every subsequent record is `type: "probe"`:

```jsonc
{
  "seq": 11,                       // monotonic; a gap means a lost write
  "type": "probe",
  "run_id": "20260811T161221Z-30515e",
  "ts_utc": "...",                 // wall clock
  "t_offset_ms": 143,              // monotonic offset from run start
  "probe_id": "C01",
  "phase": "api",                  // env | api | local
  "name": "File tree, depth=1",
  "question": "...",               // what this probe was for
  "cost": {
    "rate_limit_tier": 1,
    "units_charged": 1,
    "budget_if_low_seat": "6 per MONTH",
    "budget_if_high_seat": "10-20 per minute"
  },
  "outcome": "BLOCKED",            // PASS|FAIL|BLOCKED|SKIPPED|ERROR|INFO
  "evidence": { },                 // structured facts extracted
  "inference": "...",              // what this MEANS
  "next_action": "...",            // what to do about it
  "error": null,
  "http": {
    "url": "...", "final_url": "...", "method": "GET",
    "auth_header_used": "X-Figma-Token",
    "status": 429, "elapsed_ms": 0, "transport_error": null,
    "response_headers": { },       // ALL of them, verbatim
    "figma_diag": {                // the four that decide everything
      "retry-after": "34200",
      "x-figma-plan-tier": "starter",
      "x-figma-rate-limit-type": "low"
    },
    "body_bytes": 15,
    "body_ref": { "file": "raw/C01.a6857b637486.bin", "sha256": "...", "bytes": 15 },
    "body_excerpt": "..."          // token-redacted, 600 bytes
  }
}
```

## Reading the verdict

`X-Figma-Rate-Limit-Type` is the single most important field in the whole log:

- `low` → View or Collab seat. Tier 1 is **6 per month**. REST is not a viable
  transport; go to the `.fig` route.
- `high` → Dev or Full seat. Tier 1 is per-minute. If you were still getting
  9-hour backoffs, look at `X-Figma-Plan-Tier` instead.

`X-Figma-Plan-Tier` reflects the plan that **owns the file**, not your best seat
elsewhere. A file sitting in personal drafts gets `starter` limits even for an
Enterprise member — that alone produces exactly the symptom you described.

## Tests

```bash
python3 test_probe.py     # 74 assertions, spins up a mock Figma on localhost
```

Covers: six URL shapes, token redaction, `.fig` structural parsing (zip+zstd and
raw+deflate containers, plus a corrupt file), interaction counting, and six
end-to-end runs — high seat, low seat with a 9.5h 429, Tier-1 gating, the
low-seat guard, Bearer fallback, and fully offline.
