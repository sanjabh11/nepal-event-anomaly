# P0 Baseline Ledger — v0

**Recorded:** 2026-09-13. Engineering-proof baseline only; no scientific
claim is implied by anything here.

| Field | Value |
|---|---|
| Canonical integration root | `/Users/sanjayb/nepal-event-anomaly-worktrees/full-framework-v1-20260912-144802/integration` |
| HEAD at baseline | `5ef43c292ce41a93c9adec55185973caa7045285` |
| Working tree at baseline | clean (`git status --porcelain` empty) |
| Runtime | pinned `.venv` at worktree root (`.venv/bin/python`, interpreter ends in `/python`); `ruptures==1.1.9` installed from `requirements.txt` |
| Test invocation | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python -B -m pytest tests/ -q` |
| Disk | ~8.2 GiB free at baseline; dipped to ~7.1 GiB mid-cycle (guard correctly refused writes); **~13 GiB at re-check 2026-09-14 (round-6)**. Pre/post-write checks required on every future write; serial acquisition aborts below 8 GiB |
| Frozen preregistration | `preregistration.md` untouched; frozen 2026-09-10; single-event retrospective Langtang hindcast |
| N5 / gates | B strict path hash-bound fail-closed; C/D intentionally blocked; E needs verified A+B; F strict needs A+B+E; FMX blocked (no verified external freeze token) |
| Authorization flags | `warning_path_authorized=false`, `production_authorized=false`, `promotion_eligible=false` everywhere |
| Empty Git shell (DO NOT USE) | `/Users/sanjayb/Documents/ChatGPT/nepal-event-anomaly` — no commits; not the implementation root |

## Path/HEAD guard (G01)

Every future command against this project must first resolve the
canonical integration root and exact HEAD above. Abort on any mismatch.
The empty Git shell is never the implementation root.

## Test results (exact, as run)

- `pytest tests/test_research_v0_*.py`: **116 passed** (2026-09-14,
  hardened contracts incl. adversarial A/B/C/D/E-series probes).
- `pytest tests/test_p5_io_contract.py`: **28 passed** (2026-09-14,
  P5 I/O contract — payload normalization, cell tie, year-range,
  JJA universe, edge censoring, full-chain layout).
- Full suite under pinned `.venv`: **1276 passed, 5 skipped,
  0 failed** — both prior environmental failures resolved
  (interpreter ends in `/python`; `ruptures` installed). The 5 skips
  are `rasterio`-dependent frozen-package tests — optional-dependency
  gates, not waived coverage; they are disclosed, not hidden:
  `test_framework_v1_adapters.py:80`, `:92`, `:109`, `:128`,
  `test_framework_v1_screen.py:123`.
- Content commit: bound via `content_head` in the manifest
  (currently `81bf660`; superseded by the commit containing this
  line and rebound in the manifest-only commit that follows).
- CLI smoke: `validate-envelope` re-verifies flag/status/digest shape,
  self-hash, real matrix/policy bytes, and record payloads; execution
  statuses refuse no-bundle validation; `claim-scan` clean on all docs;
  `horizons`/`embargo` fail closed on invalid input.

## What was added this cycle (additive only)

- `docs/science/` — this ledger, decision matrix, cutoff/target policy,
  source records, gap register.
- `nepal/research_v0/` — import-isolated research-only record types,
  policy primitives, gates, and a validation-only CLI. **Zero imports of
  `nepal.framework_v1`** (negative import tests enforce this).
- `tests/test_research_v0_*.py` — contract-layer tests.
- `README.md` — legacy quick-start paths marked non-pilot.

**Not modified:** `nepal/framework_v1/`, `preregistration.md`, `pinned/`,
`data/framework_inputs_v1_reconciled/`, all existing tests.

## Owner designations

- **P3 approver (designated):** repository owner (user), 2026-09-14.
  Designation is recorded here as intent; the attestation itself
  remains an external, human-verified act — `human_approved=True`
  fields stay structural only and no code path authenticates identity.
- **Engineered dam-breach vertical:** deferred
  (`DEFERRED_NO_OPEN_TIMED_SOURCE`) — no open structured Nepal
  inventory located; owner-accepted.
- **Pilot selection:** owner pre-commits to
  `FIRST_PASSING_ALL_GATES_ELSE_NO_QUALIFYING` — if no vertical clears
  its gates, `NO_QUALIFYING_PILOT_SOURCE` stands.
- **License order:** GLOF + snow-avalanche sources first
  (HiAVAL, HMAGLOFDB), then Sentinel-1, then forecast archives; MARS
  procurement deferred until a vertical actually needs it.

## Standing prohibitions (unchanged)

No downloads before P3 design approval + P5 intake gates. No FMX freeze
without a verified external freeze token. No clustering/regime fitting on
real data. No claims about operational use, warnings, production,
prediction, or scientific validation. Existing green tests are
contract-layer evidence, not real-data science.
