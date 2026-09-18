# Event Package Specification — v0 (Run B)

**Status:** `DESIGN_DRAFT_COMPLETE` — specification only; no intake
authorized.
**Lane:** Run B (`run-b/source-qual`, base `33dbad4`).
**Scope:** typed mapping from each qualified/candidate source to the
`nepal/research_v0/records.py` contract surface, the event-interval and
uncertainty model, dedup and cascade-identity rules, and the
basin/catchment holdout protocol. Every rule here must already be
enforceable by the frozen validators — this spec defines semantics,
it does not change code.

Companion: `SOURCE_QUALIFICATION_RECORDS_V0.md` (per-source decisions),
`FMX_AUDIT_RUBRIC_V0.md` (value-level feature audit),
`REGIME_PROTOCOL_V0.md` (multi-region regime plan).

## 1. Source → record-type mapping

Every event-package row below names which record types a source can
legitimately produce. A source may not emit a record type it cannot
evidence — e.g., a report-sampled inventory may never emit
`ObservationOpportunityV0` with `OBSERVED_FULL`, and no caller-asserted
control state is accepted (`derive_control_state` recomputes).

| Source (source_id) | EventLabelV0 | ObservationOpportunityV0 | ControlWindowV0 | CutoffRecordV0 | HoldoutPlanV0 |
|---|---|---|---|---|---|
| `hiaval_v1_3_0` | yes — day/month-class labels | no intrinsic frame | derived only | yes | yes |
| `kneib_s1_everest_deposits` | yes — scene-interval brackets | `Sentinel1_date` scene-pair enumeration → OBSERVED_FULL/PARTIAL | derived from S1 frame | yes | yes |
| `safe_hma_annual_deposits` | yes — year-class (descriptive only) | partial, detection-conditional | no NEGATIVE possible | yes | yes |
| `icimod_hmaglofdb_v1_3_0` | yes — ±3d / month / year classes | lake-level frame via `GL_ID`/`LakeDB_ID`/`G_ID` ↔ ICIMOD/RGI lake inventories | derived from lake frame | yes | yes |
| `essd_2026_481_glacier_failure_db` | yes — day/month/year + Min/Max-Date | RGI v7 glacier denominator | derived from glacier population | yes | yes |
| `zhong_2024_ria_inventory` | yes — per-event dates | none enumerated | no NEGATIVE possible | yes | yes |
| `burrows_timed_monsoon_landslides` | yes — ~12-day windows on timed subset; untimed rows are `CENSORED_OR_AMBIGUOUS` labels, never dropped | S1 image-opportunity frame | derived from image frame | yes | yes |
| `jones_2021_monsoon_landslides` | yes — monsoon-slice class | slice-granularity mapping extent | derived at slice granularity | yes | yes |
| `nasa_coolr_hma_ls_v002` | yes — day-class, report-derived | none — detection conflated | no NEGATIVE possible | yes | yes |
| `usgs_gorkha_2015_landslides` | yes — trigger-window labels (coseismic class only) | documented extent + obscured-area flags → OBSERVED_FULL/PARTIAL | derived from extent mask | yes | yes |
| `jiang_essd_2026_107_ldof_db` | yes — dam-formation and breach intervals kept separate | river-reach frame | derived from reach frame | yes | yes |
| `usgs_ofr_91_239_landslide_dams` | yes — coarse historical labels | none | no NEGATIVE possible | yes | yes |
| `borealis_dam_failure_db` | pending Nepal-subset verification | unverified | unverified | yes | yes |
| report-only sources (DRR/DesInventar/EM-DAT/COOLR-class) | day-class labels only, reporting-biased | never OBSERVED_FULL | no NEGATIVE possible | yes | yes |
| forecast archives (ds084001, NOMADS, GEFSv12, S2S, C3S, dynamical.org) | — (not event sources) | — | — | yes — vintage-bound cutoffs | — |

## 2. Event-interval and uncertainty model

`EventLabelV0` stores `event_time_start`/`event_time_end` as an
explicit-UTC interval plus `uncertainty_seconds` that must cover the
whole bracket (B09: `uncertainty_seconds >= end - start`). The declared
`event_time_precision` must agree with the measured class via
`classify_event_time` — the mapping is binding and conservative.

| Intake timing class | Interval convention | `uncertainty_seconds` | Measured class | Admissible horizons |
|---|---|---|---|---|
| exact timestamp (≤1h known) | `[t−u, t+u]` or `[t0, t1]` | measured bracket | `EXACT_TIMESTAMP` | any passing the width gate |
| day precision (HiAVAL ~95%, HMAGLOFDB published v1.0: 39%, COOLR, report logs) — canonical day semantics: event day is the last-day-or-peak-flood day for multi-day events | `[day 00:00Z, day 24:00Z)` | `86400` | `EXACT_DAY` | 48h/72h/7d/14d/30d subject to latency gate |
| ±3-day bracket (HMAGLOFDB `Sat_evidence`) | `[t−3d, t+3d]` | `518400` | `INTERVAL_LE_7D` | 7d/14d/30d only |
| scene interval 6–12d (Kneib S1; Burrows timed subset) | `[scene_i, scene_j]` | full bracket width | `INTERVAL_8_30D` | **30d only** |
| month precision | calendar month | month width (28–31 d) | `COARSE_OR_UNRESOLVED` (declared `month` maps coarse) | none — descriptive only |
| monsoon slice (Jones) | slice window | slice width | `COARSE_OR_UNRESOLVED` | none — descriptive only |
| year (SAFE-HMA) | calendar year | year width | `COARSE_OR_UNRESOLVED` | none — descriptive only |
| sequence window (Gorkha aftershocks; Gnyawali–Adhikari) | `[mainshock, sequence_end]` | sequence width | class by width | per measured class |
| unresolved/malformed | — | — | `COARSE_OR_UNRESOLVED` | none |

Narrowing rule: an independent day-precision source (e.g., a HiAVAL
event corroborating inside an S1 scene interval) may narrow the
interval *before* label assignment. Interval brackets may never be
relabeled as release timestamps, and narrowing must be recorded in
`event_time_basis` with both source IDs.

## 3. Identity, dedup, and cascade rules

- `event_id` = `{source_id}:{source_version}:{source_row_key}` — stable,
  source-anchored, and version-pinned (label `source_version` must equal
  the bound source's version).
- Cross-source duplicates of one physical event are never merged. The
  canonical record is the one with the best timing + spatial precision;
  others set `duplicate_of` to the canonical `event_id`. Unresolved
  duplicates make the window `CENSORED_OR_AMBIGUOUS`.
- Cascade identity: trigger → downstream members share a
  `cascade_group_id`; each non-root member sets `parent_event_id`.
  `parent_event_id`/`duplicate_of` must reference bound event IDs —
  self-reference and dangling references reject (D08). Cascade groups
  are atomic: every member lands in the same holdout group.
- HMAGLOFDB recurrence: the integer `GF_ID` is the event key and the
  `Repeat` field is the governing recurrence indicator; recurrent
  events from the same lake group under one `cascade_group_id`; the
  3 ephemeral-lake series are one atomic group each — 23% of events
  may not inflate the event count. The `_Z` suffix convention is
  paper-reported but UNVERIFIED against the integer `GF_ID` schema —
  it is PAYLOAD-GATED until byte inspection, and no deduplication or
  grouping may be based on `_Z` alone.
- Dam-formation vs breach (LDOF): two labels, one cascade group;
  `event_time_basis` distinguishes formation observation from breach
  timing.
- Compound verticals never merge into one label: an ice-rock avalanche
  triggering a GLOF is `ice_rock_avalanche` (parent) + `glof` (child)
  + downstream `impact` records, all in one `cascade_group_id`.

## 4. Holdout assignment protocol

Assignment happens **before** eligibility filtering — a `HoldoutPlanV0`
whose `event_assignments` keys do not exactly equal the bound event
universe rejects (E09).

- `assignment_rule`: `basin` primary; `catchment` acceptable;
  `macroregion`/`fixed_spatial` only when basin assignment is genuinely
  unavailable (recorded with reason).
- Nepal basin universe (predeclared): `koshi`, `gandaki`, `karnali`,
  `mahakali`, `bagmati`, plus sub-basin refinements where the event
  density warrants (`dudh_koshi`, `arun`, `tamor`, `seti`,
  `marsyangdi`, `kali_gandaki`, `bheri`, `humla_karnali`,
  `langtang_trishuli`, `indrawati`). Glacier-lake units use the
  lake's contributing basin; admin-only geocodes use the basin
  containing the admin unit centroid, flagged `basin_assignment_basis`.
- Requirements (enforced by `HoldoutPlanV0.problems()`):
  - ≥3 disjoint geographic groups across train/validation/test —
    two groups cannot support a generalization claim;
  - ≥2 independent basins in the eligible universe (inventory gate);
  - `evaluation_region_names` ≥2, unique, drawn from **test** groups
    only — evaluation regions are where generalization is measured;
  - Langtang-only evaluation is rejected by construction;
  - every declared group must receive ≥1 event and every event must map
    to a declared group — complete bidirectional coverage;
  - `embargo_seconds` = max(forecast horizon, label interval,
    publication latency, cascade contamination duration); any unknown
    component blocks the plan (`None` → experiment blocked or
    restricted to a declared coarser descriptive analysis);
  - `test_locked` — nothing is tuned on locked test basins.
- Temporal boundary: a preregistered time cut separates train from
  validation inside basins where density allows; event clusters and
  cascade groups stay atomic across that boundary.

## 5. Record-graph requirements per status

Consistent with `STATUS_REQUIRED_RECORDS` in `records.py`:

- `EVENT_INTAKE_VALIDATED` / `INTAKE_COMPLETE` require
  `EventLabelV0` + `ObservationOpportunityV0` + `ControlWindowV0` +
  `HoldoutPlanV0` + `SourceRecordV0`.
- `RESEARCH_FEATURE_MATRIX_FROZEN` additionally requires
  `CutoffRecordV0` + a byte-bound `EvidenceArtifactV0` of type
  `feature_matrix`.
- `FORECAST_EXPERIMENT_ONLY` requires the full chain including
  `ForecastVintageV0`, `HazardVerticalSpecV0`, and per-vintage
  `CutoffRecordV0` linkage (E20).
- Every label's `source_id` must be bound in the envelope and reach
  `EVIDENCE_VERIFIED` posture before execution statuses are allowed
  (C15) — `EVIDENCE_VERIFIED` itself requires a byte-bound sidecar and
  `INDEPENDENTLY_VERIFIED` review.

## 6. What this spec does not do

No data is downloaded, no real labels are written, no vertical is
nominated, and no evaluation engine is specified. Synthetic fixture
generators under `tests/fixtures/` exercise the *shapes* of these
records only — they are never substitutes for governed intake.
