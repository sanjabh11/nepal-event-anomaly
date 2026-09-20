# P5 extension status index — 2026-09-20 (current)

The authoritative P5 state record. `FRAMEWORK_V1_CURRENT_STATUS.md`
and `STATUS_SCOPE_RECONCILIATION_NOTE_20260915.md` describe the broader
legacy framework and are **not** P5 evidence. Release-bound counts are
recorded in `docs/science/ARTIFACT_MANIFEST_V0.json` — this document
carries evidence state only.

## Lane states

| Lane | State | Evidence |
|---|---|---|
| P5-A2 daily | `CANDIDATE_ONLY` receipt / `UNSUPERVISED_STRUCTURE_NOT_STABLE` artifact — honest negative | `p5-glof-2026-09-19/retrieval/p5_glof_descriptive_receipt_v0.json` |
| Seasonal v0 | SUPERSEDED semantics — immutable, never patched | `p5-seasonal-jja-2026-09-20/` |
| **Seasonal v1 (current)** | `UNSUPERVISED_STRUCTURE_NOT_STABLE` — second honest negative; same three open gates under corrected semantics | `p5-seasonal-v1-2026-09-20/run/seasonal_lane_receipt_v0.json` |
| Option 3 seismic | **BLOCKED** at preflight — zero in-window events, no bytes, storage marginal; formal disposition recorded | `p5_d_preflight_reconciliation_v0.json` + `p5_d_owner_disposition_v1.json` |
| Arm C pressure levels | DEFERRED — separate owner amendment required | — |
| Event association | BLOCKED — labels `UNADJUDICATED`, controls censored (by design) | — |

## Seasonal v1 result detail

- Failed gates: `modal_k_unanimous` (3/3/4 across seeds), `season_matched_null` (p=0.66 ≥ 0.05), `shuffled_null` (7/50 replicate failures → incomplete envelope, fails closed)
- `gate_observations` distinguishes `PASS`/`FAIL`/`SKIPPED`/`NOT_APPLICABLE` from binding — LORO is `SKIPPED`/non-binding (diagnostic), season_refits/elevation/effort are `NOT_APPLICABLE`
- Negative control refused before fitting (declaration) and rejected statistically (independent tests)
- Replay: `REPLAY_OK` via `scripts/replay_seasonal_p5.py` (deterministic frame rebuild, envelope+freeze digests, floor, bindings, authority)

## Cross-root index

`p5-glof-2026-09-19/retrieval/p5_evidence_index_v0.json` binds all
four roots (daily, seasonal v0, seasonal v1, seismic probe) with
byte-level digests — the single cross-root provenance surface.

## Owner-gated items (not executable without approval)

1. Option 3 execution — needs a documented in-window event or a new non-event estimand, storage re-measure, decoder admission, FDSN intake module (never in the local byte reader), dedicated one-station runner
2. ObsPy project admission — isolated qualification only; needs a dependency amendment with pin/hash/license
3. Arm C — separate CDS/feature amendment
4. Publication / external verification — deferred

## Warnings disclosure

Fresh suite carries ~56 xarray/netCDF4 deprecation warnings
(upstream, monitored) and 1 sklearn ConvergenceWarning in a
degenerate synthetic fixture (expected). No warning-free claim is
made.
