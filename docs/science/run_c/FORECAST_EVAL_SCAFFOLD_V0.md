# Forecast Evaluation Scaffold — v0

**Status:** `DESIGN_DRAFT_COMPLETE` — pending human approval.
**Scope:** research only. This scaffold defines how a
`FORECAST_EXPERIMENT_ONLY` evaluation is designed, powered, and reported.
It authorizes no archive download, no scoring of real forecasts, and no
operational inference. Synthetic fixtures only (§10).

**Binding contracts:** `nepal/research_v0/records.py`
(`ForecastExperimentV0`, `ForecastVintageV0`, `HoldoutPlanV0`,
`EventLabelV0`, `ObservationOpportunityV0`, `ControlWindowV0`,
`CutoffRecordV0`, `EvidenceArtifactV0`, `ResearchClaimEnvelopeV0`),
`nepal/research_v0/gates.py` (`forecast_feature_problems`,
`occurrence_feature_problems`), `nepal/research_v0/policy.py`
(`ForecastDataClass`, `HORIZON_SECONDS`, embargo rules), and
`docs/science/INFORMATION_CUTOFF_TARGET_POLICY_V0.md` §§2–9.

## 1. Admissible data

- Only `data_class ∈ {REFORECAST, ARCHIVED_OPERATIONAL}` vintages may
  enter a forecast feature matrix — enforced by
  `ForecastVintageV0.problems()` and `forecast_feature_problems`.
- **ERA5, ERA5-Land, ERA5T, and IMDAA are excluded from all
  forecast-skill claims.** Reanalysis values are post-assimilation
  products, not issue-time information; their use is confined to the
  `RETROSPECTIVE_REGIME` descriptive path. A feature with
  `data_class=REANALYSIS` in a forecast matrix rejects.
- `CURRENT_FEED` objects are not archives and reject likewise.
- Feature `field_class` must come from the controlled occurrence
  registry (`meteorological_reforecast`,
  `meteorological_archived_operational`, `terrain_static`,
  `cryosphere_state`, `hydrology_state`, `observation_metadata`);
  forecast-class features bind a `vintage_digest`; `catalog_label` is a
  target channel, never a predictor; `availability_time <= issue_time`
  is enforced per feature.
- B-derived ranks/priorities/scores and exposure/impact variables are
  denied in occurrence namespaces (digest references only).

## 2. Experiment record binding

A `ForecastExperimentV0` is admissible only when:

| Field | Rule |
|---|---|
| `target` | `"occurrence"` only — `EXPERIMENT_TARGETS` allowlist |
| `horizon` | ∈ `HORIZON_SECONDS` {6h,24h,48h,72h,7d,14d,30d} and admissible for the label's event-time class |
| `feature_digests` / `vintage_digests` | non-empty, 64-hex, byte-bound to `EvidenceArtifactV0`/`ForecastVintageV0` payloads |
| `baselines` | must include `climatology`, `rule`, `regularized_supervised` (+ `null` required by B39 policy) |
| `metrics` | must include `brier`, `calibration`, `precision_recall`, `event_recall`, `false_alarms_per_opportunity` |
| `power_report_digest` | 64-hex bound `power_report` artifact |
| `uncertainty_method` | declared (basin/event-season block bootstrap) |
| `missing_data_policy` | declared (§7) |
| `evaluation_region_count` | ≥2 named regions from locked test groups; Langtang-only prohibited |
| `test_locked` | `true` — nothing tuned on locked test basins |
| `claim_scope` | fixed `research_only_no_operational_authorization` |

## 3. Baseline suite (predeclared, all mandatory)

1. **`climatology`** — opportunity-weighted seasonal base rate per
   forecast unit × season. Defines the no-skill floor.
2. **`rule`** — a transparent predeclared decision rule on archived
   forecast fields (e.g., accumulated forecast precipitation +
   temperature threshold); fully specified before evaluation.
3. **`null`** — marginal-rate/no-signal model: predicted probability
   constant within unit; detects degenerate "skill" artifacts.
4. **`regularized_supervised`** — a predeclared regularized classifier
   (e.g., penalized logistic regression) trained on train groups,
   threshold selected on validation groups only.
5. **Ablation** — the candidate model re-scored with each declared
   feature group removed; reported alongside the full model.

A candidate that does not beat **all** baselines on the primary metric
family is not retained — sophistication is not evidence.

## 4. Metric set

| Metric | Definition (predeclared) | Notes |
|---|---|---|
| `brier` | mean squared probability error on the unambiguous (POSITIVE/NEGATIVE) subset | primary |
| `calibration` | reliability curve + calibration-in-the-large and calibration slope; isotonic/miscalibration bands | report curve, not only scalar |
| `precision_recall` | PR curve + AUPRC on unambiguous subset | report against climatology baseline AUPRC |
| `event_recall` | fraction of adjudicated event groups detected at predeclared operating threshold(s) | threshold(s) fixed on validation, not test |
| `false_alarms_per_opportunity` | false-alarm count divided by verified observation opportunities | denominator is `OBSERVED_FULL` opportunities only |

Plus, mandatory reporting layers:

- **Lead-time degradation** — every metric recomputed per lead bucket
  (24h/48h/72h/7d/14d/30d as admissible); a skill curve, not a point.
- **Slices** — geographic (per evaluation region), temporal (per era),
  seasonal (JJA vs non-JJA as declared), mechanism (vertical subset),
  missingness (per §7 scenario).
- **Censored accounting** — `CENSORED_OR_AMBIGUOUS` windows are counted
  and reported separately, never folded into negatives.

## 5. Evaluation design

1. **Splits.** `HoldoutPlanV0`: basin/catchment groups assigned before
   eligibility filtering; complete event→group mapping; cascade groups
   atomic; `embargo_seconds` finite and applied to split boundaries and
   control windows.
2. **Locked test.** Test groups are locked; no preprocessing choice,
   K, threshold, feature, or model decision may touch them. Thresholds
   are fixed on validation groups.
3. **Evaluation regions.** ≥2 uniquely named regions drawn from locked
   test groups; single-region and Langtang-only evaluations reject.
4. **Vintage discipline.** Each scored prediction is bound to a
   `ForecastVintageV0` digest; the cutoff chain is verified per scored
   unit (`issue_time <= valid_start`, `archive_availability >= issue`,
   `local_retrieval >= archive` — retrieval never substitutes for
   provider availability).
5. **Atomic reporting.** Evaluation artifacts commit atomically per
   artifact with resumable manifests (B42); no partial promotion.

## 6. Power and effective sample size (predeclaration)

Before any evaluation run:

- Prospective power/precision computation on the declared design:
  expected event-group count on test basins, clustering factor
  (basin × event-season), event rarity, and target metric precision.
- The effective sample size is the number of independent
  **event groups** (not windows, not members) after clustering.
- Minimum detectable enrichment/score difference is declared; designs
  below it emit `UNDERPOWERED_DESCRIPTIVE_ONLY` — a non-significant
  result is never re-spun as success.
- The power computation is itself a bound artifact
  (`power_report_digest` → `EvidenceArtifactV0` type `power_report`).

## 7. Missing-feed degradation (predeclared scenarios)

`missing_data_policy` must declare, per scenario:

- **Provider dropout** — one archive centre's vintages missing for a
  contiguous block (delayed publication, outage); metrics recomputed
  under the degraded feed.
- **Variable dropout** — one feature group unavailable at issue time.
- **Member truncation** — reduced ensemble size (e.g., 11 → 5 members)
  to bound member-count sensitivity.
- **Latency stress** — vintages treated as unavailable until
  `issue_time + margin`; any use of margin-internal information
  rejects.
- A degradation that destroys the primary conclusion is reported as
  fragile, not averaged away.

## 8. Uncertainty quantification

- `uncertainty_method`: basin/event-season block bootstrap —
  resample cascade/event-season groups within evaluation regions;
  report percentile intervals on every metric.
- Intervals never cross evaluation-region boundaries.
- Dependence-aware intervals are mandatory: i.i.d. bootstrap on
  windows is rejected (B31/B32).

## 9. Threshold and tuning discipline

- Operating thresholds, calibration mappings, and any hyperparameter
  are fixed on validation groups; locked test basins see them once.
- Re-tuning after test inspection invalidates the experiment — the
  corrected path is a new experiment id, not a revised score.

## 10. Synthetic fixture protocol

Scaffold conformance is exercised on synthetic payloads only:

- Generated `ForecastVintageV0`-shaped vintages with planted
  init/issue/valid violations (rejected), inversion timestamps
  (rejected), `REANALYSIS`/`CURRENT_FEED` classes (rejected).
- Generated label/opportunity/control frames with planted positives,
  ambiguity dominance, and boundary-clipping intervals.
- A planted-skill synthetic forecast stream verifying the metric
  pipeline, slice machinery, lead-time bucketing, and decision rule —
  contract-layer tests, never scientific evidence.
- **No real scoring occurs in this lane.**

## 11. Decision rule

```
admissible data check fails          → no experiment (rejected upstream)
power below predeclared floor        → UNDERPOWERED_DESCRIPTIVE_ONLY
experiment completes on
 locked test basins                  → FORECAST_EXPERIMENT_ONLY
```

`FORECAST_EXPERIMENT_ONLY` means only: *a predeclared archived-forecast
experiment was executed under the contract and reported its full
metric/slice/uncertainty set.* It implies no skill finding, no
operational capability, and no authority. Skill language beyond
descriptive reporting of the metrics is prohibited; `READY`-shaped and
authority-shaped statuses are absent from the neutral vocabulary by
construction.

## 12. Non-claims

- No forecast, warning, production, or authority claim.
- No ERA5/ERA5-Land-derived forecast-skill claim under any framing.
- No threshold tuned on test data.
- `warning_path_authorized=false`; `production_authorized=false`;
  `promotion_eligible=false`.
