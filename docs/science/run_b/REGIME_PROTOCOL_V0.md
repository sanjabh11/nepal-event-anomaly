# Multi-Region Regime Protocol — v0 (Run B)

**Status:** `DESIGN_DRAFT_COMPLETE` — specification only; no regime
fitting authorized by this document.
**Lane:** Run B (`run-b/source-qual`, base `33dbad4`).
**Scope:** the predeclared protocol for unsupervised regime discovery
on retrospective (reanalysis-class) feature matrices across ≥3
geographic groups. It extends the single-cell P5-C confirmation
diagnostic (`EXPLORATORY_DESCRIPTIVE_SINGLE_CELL`) to the multi-region
design required before any regime–event association is contemplated.
Mode is `RETROSPECTIVE_REGIME` — disjoint from `FORECAST_REGIME`
(D2 §10).

The only terminal statuses this protocol may emit are
`DESCRIPTIVE_REGIME_ONLY` and `UNSUPERVISED_STRUCTURE_NOT_STABLE`.
Association testing is a downstream phase (Run C
`ASSOCIATION_PROTOCOL_V0.md`) and begins only after regimes are
frozen — labels never touch fitting, selection, or interpretation.

## 1. Inputs

- `data_class = REANALYSIS` only (`RegimeArtifactV0` enforces): ERA5,
  ERA5-Land(+T), or IMDAA feature matrices. Forecast archives and
  feeds are excluded by construction.
- Geographic groups: ≥3 disjoint basin groups drawn from the
  predeclared Nepal basin universe (`koshi`, `gandaki`, `karnali`,
  `mahakali`, `bagmati` with sub-basin refinements per
  `EVENT_PACKAGE_SPEC_V0.md` §4). A single-cell or single-basin fit is
  exploratory-only and may not emit either terminal status.
- Temporal coverage: a preregistered season set (e.g., JJA monsoon)
  over a declared multi-year span; era boundaries for drift
  diagnostics are preregistered before fitting (B33).
- Feature set: declared `GMM_FEATURES`-style column list with units
  (`feature_units` sidecar); all required columns must exist or the
  run fails closed; missingness is reported per column before
  filtering.
- `fitted_on = TRAIN_ONLY`; `label_blinding = true` — no event labels,
  no B artifacts, no exposure variables anywhere in the input.

## 2. Train-only preprocessing

- Scalers/imputers fit on training-group rows only; fitted parameters
  are bound as a `preprocessing` `EvidenceArtifactV0`.
- Circular-mean handling for directional features (mean-resultant
  components, per CFM-05 precedent).
- Rolling/aggregate features computed only inside contiguous-date
  runs; windows spanning gaps are edge-censored and dropped pre-fit
  (P5 precedent: `edge_censored` flag, count reported).
- Missingness policy: per-column NaN computed pre-filter; declared
  drop/impute rule; `edge_censored` rows reported and excluded.
- Locked-test basins contribute nothing to preprocessing — no
  statistics, no imputation values, no outlier thresholds.

## 3. Model and selection

- Estimator: Gaussian mixture (full covariance) as the declared
  baseline method; alternative methods (hierarchical, density-based)
  may be added only as separately preregistered comparators.
- K sweep: `K ∈ {1,2,3,4,5}` with the **K=1 null mandatory** — a
  no-structure baseline must always be fitted and bound
  (`null_model` artifact + `null_model_digest`, E19).
- Seeds: ≥3 distinct non-negative seeds (e.g., 42, 7, 2024); per-seed
  BIC/AIC/convergence recorded; only converged fits are K-eligible;
  all-fail → run error, not a result.
- K selection: modal-K across seeds reported with its frequency;
  frequency < 1.0 is `k_instability`, surfaced in the stability
  report — no silent winner.
- No inherited K=6, no T2A performance transfer, no prior cluster
  centroids — every artifact is refit from bound inputs.

## 4. Stability requirements

All of the following are required for `DESCRIPTIVE_REGIME_ONLY`;
failure of any one → `UNSUPERVISED_STRUCTURE_NOT_STABLE`.

| Axis | Check |
|---|---|
| seeds | ≥3 seeds; pairwise partition agreement (ARI or declared equivalent) reported; modal-K frequency = 1.0 required for a K claim |
| temporal | block bootstrap over contiguous time blocks (≥200 replicates); regime-parameter 95% CIs reported |
| basin/geographic | leave-one-region-out refits: regime structure recovered on held-out basins, not just within-fit basins |
| season | refit on season-matched subsets; regime structure must not collapse to a calendar artifact |
| elevation | elevation-band splits where elevation data permit; regimes must not reduce to the elevation coordinate itself |
| missingness | sensitivity to declared missingness policy and to observation-effort differences between basins/eras |
| drift | preregistered era boundaries; drift diagnostics reported per era pair (B33) |

## 5. Null models

Two null families are mandatory:

- **Shuffled null:** within-row feature shuffling (or declared
  equivalent) destroying cross-feature dependence while preserving
  marginals — the regime fit must exceed the shuffled-null envelope
  (e.g., JS distance > null p95, `exceeds_null` flag per CFM-08).
- **Season-matched null:** synthetic samples drawn from the same
  seasonal/era marginals — regimes that vanish under the
  season-matched null are calendar artifacts, not structure.

Both null artifacts are bound (`null_model` type) with digests.

## 6. Outputs and binding

- `RegimeArtifactV0` fields: `mode=RETROSPECTIVE_REGIME`,
  `data_class=REANALYSIS`, `fitted_on=TRAIN_ONLY`, `label_blinding`,
  `k`, `seeds` (≥3 distinct), `preprocessing_digest`,
  `k_selection_digest`, `stability_report_digest`,
  `null_model_digest`, `source_digests` — every digest is a real
  64-hex bound to a byte-verified `EvidenceArtifactV0` (C19).
- `DESCRIPTIVE_REGIME_ONLY` additionally requires artifact types:
  `regime_model`, `stability_report`, `k_selection`, `preprocessing`,
  `null_model`.
- `UNSUPERVISED_STRUCTURE_NOT_STABLE` requires the regime record and
  is a legitimate terminal outcome — not a failure to be repaired by
  loosening the stability criteria.
- Membership-confidence reporting: mean/median max-posterior and
  ambiguous fraction (CFM-09 precedent).
- Provenance: a run manifest binds input/feature/output digests,
  config, environment, and seeds (CFM-11 precedent); atomic publish
  or quarantine on failure (B42/C29).

## 7. Prohibitions

- No event labels in preprocessing, K selection, fitting, or
  interpretation — label opening is a later, separately gated phase.
- No retrospective regime description may be presented as forecast
  skill; causal and forecast wording is rejected by the claim scan.
- No single-box (e.g., Langtang-only) or single-cell generalization
  — the P5-C diagnostic remains
  `EXPLORATORY_DESCRIPTIVE_SINGLE_CELL` and does not feed this
  protocol's claims.
- No tuning on locked test basins — including preprocessing and
  stability-threshold choices.
