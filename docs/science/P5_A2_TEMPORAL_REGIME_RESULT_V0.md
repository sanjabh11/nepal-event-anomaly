# P5-A2 Temporal Regime Result (V0)

**Status:** `CANDIDATE_ONLY` — research-only negative finding
**Artifact verdict:** `UNSUPERVISED_STRUCTURE_NOT_STABLE`
**Authority:** `promotion_eligible=false`, `warning_path_authorized=false`,
`production_authorized=false`

This report publishes the first governed real-data execution under the
authenticated P5-A2 temporal amendment. It is a scientific result, not a
pipeline failure and not evidence of forecast, warning, or operational
readiness.

## Bound inputs

- Event source: HMAGLOFDB v1.3.0, source-level evidence verified.
- Feature source: byte-bound ERA5-Land HMA multi-basin frame.
- Hydrology: Melamchi raw value retained; administrative values are
  Sindhupalchok and source-serialized `Bagmati`; derived hydrology is
  `Indrawati` → Sun Koshi → `koshi`.
- Feature groups: `gandaki`, `karnali`, `koshi`.
- FMX: `FMX_PASS`, 6,900 rows × 19 columns, no rejected predictors.

## Temporal protocol

| Partition | Interval | Rows | Use |
|---|---|---:|---|
| Train | JJA 2001–2017 | 4,692 | Fit and bootstrap refits |
| Embargo | JJA 2018–2019 | 552 | Excluded from fit and evaluation |
| Holdout | JJA 2020–2025 | 1,656 | Temporal extrapolation evaluation |

All three feature basins are in the fit. `heldout_groups=[]` because the
run uses one declared temporal axis. Event labels use the typed
`evaluation_only` waiver and never enter regime fitting.

## Result and interpretation

The complete K-sweep, multi-seed, temporal-block bootstrap, null-replicate,
and ablation protocol ran. Structural stability failed: the learned
partition was not reproducible at the declared gates. The runner therefore
emitted `CANDIDATE_ONLY` with `UNSUPERVISED_STRUCTURE_NOT_STABLE`.

The correct interpretation is that this feature frame does not support a
stable descriptive regime under the preregistered gates. It does not
justify changing `MIN_GEO_GROUPS`, removing the temporal embargo, selecting
a convenient K, or relabelling Melamchi as Bagmati.

## Reproducibility evidence

- Receipt: `retrieval/p5_glof_descriptive_receipt_v0.json`
- Holdout gate: `retrieval/holdout_feature_gate_report.json`
- Source/reviewer sidecar: `retrieval/source_evidence_sidecar_v0.json`
- Full replay: `retrieval/p5_replay_report_v0.json` (`REPLAY_OK`)
- Evidence root: `/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19`
- Repository release: manifest-bound suite 3,387 passed / 6 skipped / 0 failed

## Remaining scientific gates

Event labels remain `UNADJUDICATED`, and every opportunity is
`UNKNOWN`, so controls are `CENSORED_OR_AMBIGUOUS`. Association is therefore
blocked. Geographic transfer is unevaluated; the fourth-group amendment is
dormant and is not required for this temporal result. Forecast, warning, and
production paths remain outside scope.

If a stable regime is required, the next action is an owner-signed
scientific amendment selecting a new feature set, K range, or frame
granularity, followed by a fresh byte-bound run. No fourth basin should be
acquired for this temporal objective.
