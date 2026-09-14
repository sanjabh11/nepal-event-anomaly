# P0 Baseline Ledger — v0

**Recorded:** 2026-09-13. Engineering-proof baseline only; no scientific
claim is implied by anything here.

| Field | Value |
|---|---|
| Canonical integration root | `/Users/sanjayb/nepal-event-anomaly-worktrees/full-framework-v1-20260912-144802/integration` |
| HEAD at baseline | `5ef43c292ce41a93c9adec55185973caa7045285` |
| Working tree at baseline | clean (`git status --porcelain` empty) |
| Runtime | Python 3.14.7, pytest 9.1.1 (system `python3`; no `.venv` in this worktree) |
| Test invocation | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. python3 -B -m pytest tests/ -q` |
| Disk | ~8.2 GiB free at baseline; dipped to ~7.1 GiB mid-cycle (guard correctly refused writes); **~15 GiB at re-check 2026-09-14**. Pre/post-write checks required on every future write; serial acquisition aborts below 8 GiB |
| Frozen preregistration | `preregistration.md` untouched; frozen 2026-09-10; single-event retrospective Langtang hindcast |
| N5 / gates | B strict path hash-bound fail-closed; C/D intentionally blocked; E needs verified A+B; F strict needs A+B+E; FMX blocked (no verified external freeze token) |
| Authorization flags | `warning_path_authorized=false`, `production_authorized=false`, `promotion_eligible=false` everywhere |
| Empty Git shell (DO NOT USE) | `/Users/sanjayb/Documents/ChatGPT/nepal-event-anomaly` — no commits; not the implementation root |

## Path/HEAD guard (G01)

Every future command against this project must first resolve the
canonical integration root and exact HEAD above. Abort on any mismatch.
The empty Git shell is never the implementation root.

## Test results (exact, as run)

- `pytest tests/test_research_v0_*.py`: **77 passed** (2026-09-14,
  hardened contracts incl. adversarial A02–A17 cases).
- Full suite `pytest tests/`: **1212 passed, 2 failed** — both
  environmental baseline debt: (1) preflight expects an executable
  ending in `/python` while the active interpreter is `python3.14`;
  (2) `ruptures` declared but not installed in the active environment.
  Zero failures reference research_v0 or docs/science.
- CLI smoke: `validate-envelope` accepts a file-bound envelope and
  rejects tampered/authority-flagged copies via self-hash recomputation;
  `claim-scan` clean on all docs; `horizons`/`embargo` fail closed.

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

## Standing prohibitions (unchanged)

No downloads before P3 design approval + P5 intake gates. No FMX freeze
without a verified external freeze token. No clustering/regime fitting on
real data. No claims about operational use, warnings, production,
prediction, or scientific validation. Existing green tests are
contract-layer evidence, not real-data science.
