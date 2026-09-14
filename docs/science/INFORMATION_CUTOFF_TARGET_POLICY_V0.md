# Information-Cutoff & Target Policy — v0

**Status:** `DESIGN_DRAFT_COMPLETE` — pending human approval (P3 gate).
**Scope:** research only. Executable form lives in
`nepal/research_v0/policy.py`; every rule here has a test in
`tests/test_research_v0_*.py`. This document authorizes no intake,
freeze, or claim.

## 1. Timestamp model

All timestamps are UTC and remain separate fields (see
`CutoffRecordV0`):

```
event_time_start, event_time_end          (the physical interval)
event_time_precision, event_time_basis    (evidence class + source)
observation_acquisition_start, observation_acquisition_end
source_processing_complete_time
source_publication_time
feature_availability_time
forecast_initialization_time
forecast_issue_time
forecast_valid_start, forecast_valid_end
forecast_vintage_id
local_retrieval_time                      (provenance ONLY)
```

For operational-like evaluation, the enforced chain is:

```
source_observation_end <= source_processing_complete
    <= source_publication <= feature_availability
    <= forecast_issue <= forecast_valid_start <= forecast_valid_end
```

`local_retrieval_time` can never make a late product historically
available. **Publication-time ≠ issue-time:** no provider archives when
an object became publicly retrievable (the R14 GFS failure mode), so
every archived vintage carries a preregistered conservative
issue+dissemination latency margin rather than trusting object
timestamps such as S3 `Last-Modified`.

## 2. Event-time precision → permitted horizons

Candidate horizons: `6h, 24h, 48h, 72h, 7d, 14d, 30d`. A horizon is
eligible only when

```
horizon_width >= event_time_uncertainty
               + verified_observation_latency
               + verified_processing_availability_latency
```

AND the horizon is in the class allowlist (binding, conservative):

| Event-time class | Policy |
|---|---|
| `EXACT_TIMESTAMP` (uncertainty ≤1h) | Any horizon passing the width gate |
| `EXACT_DAY` (≤24h) | No sub-day target; 48h/72h/7d/14d/30d subject to gate |
| `INTERVAL_LE_7D` | 7d or longer; 24h/48h/72h prohibited |
| `INTERVAL_8_30D` | 30d only |
| `COARSE_OR_UNRESOLVED` (month/season/malformed) | No occurrence target; descriptive regime analysis only |

**Sentinel-1 consequence:** a ~12-day effective scene interval is
`INTERVAL_8_30D`, so the only admissible occurrence horizon is **30d**
under the binding class table. An independent event-time source (e.g.,
HiAVAL day-precision) may narrow the interval *before* label assignment;
interval brackets may never be relabeled as release timestamps.

## 3. Target definition — three-valued

- `POSITIVE`: adjudicated event interval fully contained in the window.
- `NEGATIVE`: verified observation opportunity for the full window AND
  no event interval intersects it.
- `CENSORED_OR_AMBIGUOUS`: timing uncertainty, incomplete observation,
  boundary overlap, or unresolved source disagreement.

Censored labels are never silently converted to negatives. Binary metrics
are computed only on the predeclared unambiguous subset; interval-censored
sensitivity results are reported separately.

**Separate targets:** occurrence (release/initiation/outburst), exposure
(people/infrastructure/built-up/connectivity), and impact (arrival,
damage, loss). Exposure variables may appear only in a separately
declared impact analysis — never in physical-occurrence features.
B priority scores and ranked arrays are prohibited everywhere in
forecast feature matrices (provenance digests only).

## 4. Controls and non-events

Controls are built **before outcome inspection**:

- Match by basin/forecast unit, season, platform, data availability,
  coverage quality.
- `NEGATIVE` requires full observation opportunity; insufficient
  coverage is `UNKNOWN`, never `NEGATIVE`.
- Event-free windows, event clusters, and cascade groups stay atomic.
- Future event labels may not select the control frame.
- Report eligible, missing, and unobserved opportunities separately.

## 5. Temporal embargo

```
embargo = max(max_forecast_horizon,
              max_event_label_interval,
              max_observation_publication_latency,
              max_cascade_contamination_duration)
```

Applied to train/validation/test boundaries and to event-free control
windows. Any unknown component → the experiment is blocked or restricted
to a declared coarser descriptive analysis (`embargo_seconds()` returns
`None`).

## 6. Holdout policy

- Basin/catchment groups assigned **before** eligibility filtering.
- Duplicate, parent, child, and cascade events in one group — atomic.
- Basin/catchment is primary; macroregion/fixed-spatial fallback only
  when basin assignment is genuinely unavailable.
- Non-empty train, validation, and locked test groups required; two
  groups are insufficient for a generalization claim.
- Nothing is tuned on locked test basins (no preprocessing, K,
  thresholds, features, or models).
- No random row cross-validation; no single-box Langtang validation.
- Report geographic, temporal, seasonal, mechanism, and missingness
  slices.

## 7. Forecast-data classes

| Class | Use |
|---|---|
| `REANALYSIS` (ERA5/ERA5-Land/ERA5T/IMDAA) | Retrospective regime path ONLY — never scored as forecast skill |
| `REFORECAST` (GEFSv12 reforecast, S2S reforecasts) | Research forecast experiments; init+valid known; does not prove operational availability |
| `ARCHIVED_OPERATIONAL` (TIGGE, NCEI/NCAR GFS archive, MARS) | Eligible when original issue/vintage/availability semantics are documented |
| `CURRENT_FEED` (ECMWF Open Data, NOMADS rolling) | Outside research authorization; not a historical archive |

## 8. Leakage prohibitions (forecast feature gate)

Rejected by `gates.forecast_feature_problems`:

- Post-issue publication / future assimilation / post-event imagery.
- Target-derived aggregates.
- B ranks, priorities, scores, ranked arrays (digest references only).
- Exposure/impact variables in occurrence experiments.
- Reanalysis values in forecast experiments.
- Spatial neighbors, duplicate/cascade events crossing split boundaries.
- Locked-test tuning of any kind.

## 9. Adjudication and power

- Two independent reviews plus third-party adjudication for
  disagreements; dissent and uncertainty retained
  (`ADJUDICATION_STATES` include `DISAGREEMENT_RETAINED`).
- `≥5 events / ≥2 groups` is an inventory gate only. A pre-outcome
  power/precision gate applies to forecast metrics: underpowered results
  emit `UNDERPOWERED_DESCRIPTIVE_ONLY`, never a success claim.

## 10. Regime discovery and association (P7/P8 contract)

- Modes are disjoint: `RETROSPECTIVE_REGIME` (reanalysis, descriptive)
  vs `FORECAST_REGIME` (archived forecast/reforecast, independently fit).
- Training-only preprocessing; seasonality/elevation controls; K=1 null;
  no inherited K=6 or T2A performance transfer; multiple seeds and
  temporal blocks; basin/region stability; missingness and
  observation-effort sensitivity; season-matched and shuffled nulls.
- No event labels in preprocessing, K selection, or interpretation.
- Regimes frozen before labels are opened; enrichment/transitions tested
  on held-out basins with event-group bootstrap; causal/forecast wording
  rejected. Outputs: `DESCRIPTIVE_REGIME_ONLY` /
  `UNSUPERVISED_STRUCTURE_NOT_STABLE`, then
  `REGIME_ASSOCIATION_SUPPORTED` / `UNSUPERVISED_PATH_NOT_SUPPORTED`.

## 11. Authority boundary

All envelopes carry `research_diagnostic_only=true`,
`claim_scope=research_only_no_operational_authorization`,
`promotion_eligible=false`, `production_authorized=false`,
`warning_path_authorized=false`. A future warning pathway is a separate
program against the WMO four-pillar framework and competent-authority
approval — it is not implied by any research output here.
