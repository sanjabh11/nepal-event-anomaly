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
| Disk | ~8.2 GiB free at baseline; dipped to ~7.1 GiB mid-cycle (guard correctly refused writes); **~15 GiB at re-check 2026-09-14 (round-7)**. Pre/post-write checks required on every future write; serial acquisition aborts below 8 GiB |
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
- `pytest tests/test_p5_io_contract.py`: **34 passed** (2026-09-14,
  P5 I/O contract — payload normalization, cell tie, year-range,
  JJA universe, edge censoring, marker/area/cell gates,
  full-chain layout).
- Full suite under pinned `.venv`: **1282 passed, 5 skipped,
  0 failed** — both prior environmental failures resolved
  (interpreter ends in `/python`; `ruptures` installed). The 5 skips
  are `rasterio`-dependent frozen-package tests — optional-dependency
  gates, not waived coverage; they are disclosed, not hidden:
  `test_framework_v1_adapters.py:80`, `:92`, `:109`, `:128`,
  `test_framework_v1_screen.py:123`.
- Content commit: bound via `content_head` in the manifest
  (bound head is the manifest's own field — see
  `ARTIFACT_MANIFEST_V0.json` for the current value).

## Last fully-verified baseline (round-10 hardening)

- HEAD: Round-10 hardening content commit (bound via `content_head`
  in `ARTIFACT_MANIFEST_V0.json`; the manifest rebind lands as its
  direct child, same relationship as previous binds).
- Full suite under pinned `.venv`: **2757 passed,
  5 skipped (rasterio), 0 failed, 57 warnings** — verified at the
  Round-10 hardened content commit.
- Manifest files governed: **84** (81 prior + root `conftest.py`
  collection guard + `tests/test_r10_promotion.py` + ledger
  updates).
- A Round-10 independent audit reopened the Round-9 "complete
  shared floor" claim — sixteen findings closed this round
  (see `GAP_REGISTER_V0.md` Round-10 census): bounded numeric
  parsing (`_finite_float` — no numeric probe raises through any
  boundary, including the auditor's richer checks), complete
  serialized-config semantics (`_config_semantic_problems`),
  ordered-sequence duplicate rejection, null/blank partition
  carrier preflight in `run_regimes`, exact digest/count typing,
  non-coercing `RegimeAssignmentArtifact` construction with exact
  map coverage, exact non-fixture source-manifest schema,
  component-walk symlink policy with identity pinning, the exact
  status×terminal×associable state machine, complete null-family
  record validation, exact field sets across the whole envelope,
  typed byte-bound `forecast_vintages` binding for associable
  forecast artifacts, forged-artifact association evidence in the
  mutation matrix, the canonical-collection guard
  (repo-wide == `tests/`, the ignored external symlink never
  collected), and this addendum.  `tests/test_r10_promotion.py`
  carries 503 tests — 89 mutations × floor/freeze/adapter/audit/
  association — plus the coordinator's ~60 executable adversarial
  probes (one residual found and fixed in-round: `cutoff_iso`
  rebound to the recomputed max train date).

## Prior verified baseline (round-9 hardening)

- HEAD: Round-9 hardening content commit (bound via `content_head`
  in `ARTIFACT_MANIFEST_V0.json`; the manifest rebind lands as its
  direct child, same relationship as previous binds).
- Full suite under pinned `.venv`: **2252 passed,
  5 skipped (rasterio), 0 failed** — verified at the Round-9
  hardened content commit `ae1b03e`.
- Manifest files governed: **82**.
- New surface: `nepal/research_v0/producer_validation.py` hardened
  into a complete producer-validation boundary —
  `validate_producer_payload(payload, *, verify_source_bytes=True)`
  now runs byte-verified source-evidence binding at every boundary
  (freeze, adapter, audit, association producer-payload binding);
  strict `RunManifestV0` deserialization; recomputed row universes
  and the 6-decimal semantic feature-matrix digest over
  `input_values`; config↔artifact cross-binding; a strict
  positive-definite covariance floor; exact seed/gate/null/status
  semantics; and `unit_basin_map` + `unit_basin_map_digest` bound
  from the payload through the adapted artifact into
  `run_association`'s `unit_basins` equality check.
  An independent adversary pass then closed twelve residual
  holes the matrix missed (`R9-V1..V12`: association-binding
  admissibility, fit∩heldout disjointness, row uniqueness,
  calendar/label bounds, material-bound `input_bytes_digest`,
  scalar type floors, forecast-field mode bans, required bound
  digests, model/feature-width binding, missingness accounting,
  typed-section field exactness, null-replicate bound, and a
  crash-not-finding decode path).
  `tests/test_r9_promotion.py` carries the 33-mutation ×
  4-boundary promotion-closure matrix plus the 30-test
  `TestR9AdversarialResiduals` regression class.
- Prior baseline retained as history: round-8 hardening,
  **2073 passed, 5 skipped (rasterio), 0 failed, 57 warnings** —
  retained verbatim as the prior verified baseline below; round-7
  content `cc2218b`, 77 manifest files, 2015/5/0/57, 562 focused;
  round-6 content `aceedff`, 76 files, 1993/5/0/57, 540 focused.

## Prior verified baseline (round-8 hardening)

- HEAD: Round-8 hardening content commit (bound via `content_head`
  in `ARTIFACT_MANIFEST_V0.json`; the manifest rebind lands as its
  direct child, same relationship as previous binds).
- Full suite under pinned `.venv`: **2073 passed, 5 skipped
  (rasterio), 0 failed, 57 warnings**.
- New surface: `nepal/research_v0/producer_validation.py` (shared
  producer validator), `tests/test_r8_provenance.py` (31),
  `tests/test_r8_vintage.py`, `tests/test_r8_fmx_assoc.py`;
  `verify_vintage_evidence` in `nepal/research_v0/_hashing.py`;
  `ForecastVintageV0.evidence_root`; byte-bound
  `FORECAST_EXPERIMENT_ONLY` gate in `evaluate()`; FMX label
  derivation + required metadata in `science_v0/fmx_audit.py`;
  spatial-shift support accounting in `experiment_v0/association.py`.
- Prior baseline retained as history: round-7 content `cc2218b`,
  77 manifest files, 2015/5/0/57, 562 focused; round-6 content
  `aceedff`, 76 files, 1993/5/0/57, 540 focused.

## Prior baseline (2026-09-16, round-7 hardening)

- HEAD: `cc2218b` — "Round-7 enforcement hardening" (content
  commit; the manifest rebind lands as its direct child, same
  relationship as previous binds).
- Manifest/HEAD relationship: `ARTIFACT_MANIFEST_V0.json` carries
  `content_head=cc2218b` — the **commit whose tree the manifest
  hashes**; the manifest commit itself follows the content commit.
  77 manifest files (76 + test_r7_hardening.py).
- Full suite under pinned `.venv`: **2015 passed, 5 skipped
  (rasterio), 0 failed, 57 warnings**.
- Focused lanes: **562 green** (round7-hardening 22 + round6
  hardening 118 + round5 hardening 18 + regimes 93 + association 63
  + evaluation 118 + audit 61 + adapters/replay/e2e).

## Verification snapshot history (2026-09-15, round 2 and later)

- HEAD at round-2 snapshot: `d3d9238`-series.
- Full suite under pinned `.venv` (round-2 snapshot): **1306 passed,
  5 skipped (rasterio), 0 failed** — `pytest --collect-only` reported
  1311 nodes (1306 + 5). An external audit observed 1300 in an earlier
  environment; the manifest's 1306 was verified correct at that HEAD.
- Post-swarm snapshots: **1641** after the first science_v0 +
  experiment_v0 integration; **1828 passed, 5 skipped (rasterio), 0
  failed** at the post-audit residual-repair head (round-4; superseded
  by the verified baseline above). Earlier counts are retained as
  dated history. 24 Run-A-reconciliation tests (calendar validity,
  claim-scan recursion, ledger repair, canonical JSON, accumulation
  semantics).
- Warnings: 57 total at the verified baseline (this block earlier
  said 56 — corrected 2026-09-16 to match the verified census and
  the count already recorded above): 56 are xarray/netCDF4
  `DeprecationWarning`s in `test_p5_io_contract.py` and 1 is a
  sklearn `ConvergenceWarning` in `test_science_v0_regimes.py` —
  all library-level, none in the research namespace. 5 skips are
  `rasterio` optional-dependency gates — disclosed, not waived.
- Run A corrected derivative: `research_runs/
  gmm_hybrid_corrected_20260915/` — sf running-accumulation defect
  (RA-01) corrected via closing-value aggregation; modal K=5
  recomputed (not inherited); JS 0.2702 [0.2505, 0.2944];
  status `EXPLORATORY_DESCRIPTIVE_SINGLE_CELL`, method-only pending
  owner ratification of the hybrid route.
- Independent rehash: `rehash_report.json` in each run root; all
  recorded digests match recomputed bytes (183 + 16 files).
- Run ledger: `size_accounting` measured from bytes; timestamps carry
  `*_utc` normalization with explicit `timestamp_classification`.
- Run A reconciliation (2026-09-15, `run-a/reconciliation` lane):
  valid-day request chunking (no June-31-class cross products),
  UTC `Z` ledger timestamps, byte-measured size accounting
  (run ledger now reports ~40.19 MB across raw/monthly/merged),
  request-vs-used window fields + JJA-only merged assertion,
  recursive `claim-scan` (CI glob defect closed), canonical-JSON
  result persistence with scaler/GMM parameters, and a
  `provenance_receipts.json` binding recoverable route evidence
  (unrecoverable fields recorded as UNVERIFIED, never fabricated).
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

## Run A result — P5 descriptive GMM confirmation (2026-09-15)

The bounded ERA5-Land acquisition executed and completed. Run root:
`research_runs/gmm_hybrid_20260915` (gitignored). Retrieval was hybrid
because the MARS queue serializes per-account requests (1 running +
3 queued; verified via CDS jobs API QoS metadata):

- `t2m, d2m, u10, v10, tp` — CDS `reanalysis-era5-land-timeseries`
  (ARCO copy of the same archive), one request, cell (28.3, 85.5).
- `sd, sf` 2001–2025 — Earth Data Hub DestinE Zarr mirror of
  `reanalysis-era5-land` (coverage ends 2026-05-31, last closed month).
- `sd, sf` 2026 (Jun–Jul + Aug 1–25) — `reanalysis-era5-land` MARS,
  2 requests.

All 78 months passed the unchanged `normalize_payload` gate (exact
vars, exact hourly timestamp sets, finite values, no post-cutoff data,
`sde` absent). Merged: 57,264 hours; `merged/complete.json` digest
matches. Features: 2,386 JJA daily rows, 156 edge-censored, 2,230
usable; selected cell (28.3, 85.5), model elevation 4322 m. GMM:
modal K=5 (3/3 seeds, all converged), JS 0.2618 (95% CI
[0.2391, 0.2856], null p95 0.0456, exceeds null), pre-event JS 0.5084.
Cross-reference vs reported literature values: 7-day mean T 9.42 °C
(reported 9.43 °C), PDD 65.94 °C·d (reported 65.94 °C·d).

Status: `EXPLORATORY_DESCRIPTIVE_SINGLE_CELL` — descriptive regime
structure on one grid cell only. This is NOT a forecast, precursor,
warning, or pilot result. `nepal/era5_hybrid_fetch.py` added (assembly
+ provenance-recorded retrieval paths).

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
without a verified external freeze token. No clustering/regime fitting
on real data beyond Run A's authorized single-cell bounded descriptive
GMM implementation confirmation (method-only,
`EXPLORATORY_DESCRIPTIVE_SINGLE_CELL`); multi-region regime discovery
and event–regime association remain gated under Run B and have not run
on real data. No claims about operational use, warnings, production,
prediction, or scientific validation. Existing green tests are
contract-layer evidence, not real-data science.
