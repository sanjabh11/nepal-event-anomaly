# Feature-Matrix Audit Rubric — v0 (Run B)

**Status:** `DESIGN_DRAFT_COMPLETE` — specification only; no intake or
freeze authorized.
**Lane:** Run B (`run-b/source-qual`, base `33dbad4`).
**Scope:** the value-level audit every candidate feature matrix must
pass before `RESEARCH_FEATURE_MATRIX_FROZEN` may be claimed. The
structural layer (`field_class` registry, `source_lineage`, denylist
regexes, class↔data consistency) is already enforced in
`nepal/research_v0/gates.py`; this rubric covers what shape-level
checks cannot — the actual **values** (defects A20, B38, E17 are
`GATED_ON_DATA` precisely because they need this rubric).

## 1. Per-column audit fields

Every column in a candidate matrix carries an audit record. Any
missing field → column rejected (fail closed).

| Field | Required content |
|---|---|
| `column_name` | exact header; matched against the denylist name regex |
| `declared_field_class` | one of `OCCURRENCE_FIELD_CLASSES` for occurrence matrices (`meteorological_reforecast`, `meteorological_archived_operational`, `terrain_static`, `cryosphere_state`, `hydrology_state`, `observation_metadata`, `catalog_label`) |
| `source_lineage` | non-empty provenance chain: source_id, version, transform chain |
| `availability_semantics` | per-value availability rule: what timestamp makes this value historically retrievable |
| `unit` | declared physical unit; reconciled against the source's documented unit |
| `value_domain` | declared legal range / enum; sentinel policy (`-999`, `NaN`, `inf`) |
| `temporal_window` | the window of source observations feeding the column |
| `audit_verdict` | `pass` / `reject` / `censored` per column |
| `audit_evidence` | check IDs fired + measured values (counts, max violation) |

## 2. Value-level checks

### 2.1 Exposure/impact contamination

Occurrence matrices may contain no exposure or impact signal.

- **Name layer** (existing): denylist names — `ghsl`, `worldpop`,
  `population`, `built_up`, `builtup`, `building_count`, `fatality`,
  `fatalities`, `deaths`, `damage`, `loss`, `affected_population`,
  `exposure`, `impact`, `road_exposure`, `settlement`.
- **Value layer** (this rubric):
  - a column whose values correlate (|Spearman| > 0.9) with any bound
    exposure proxy is flagged for reviewer adjudication — renamed
    payload detection;
  - any column constant-within-basin but varying with settlement
    density pattern → suspect exposure proxy;
  - `catalog_label` columns are LABEL channels: they may exist only as
    label passthrough and are excluded from the predictor set (B17) —
    a `catalog_label` appearing inside the feature digest set rejects.
- Exposure/impact variables live only in a separately declared impact
  analysis with its own envelope — never in occurrence features (D2 §3).

### 2.2 B-series leakage (priority/rank/score)

- **Name layer** (existing): `priority|rank|ranked|ranking|top_N|
  screen_score|screen_rank|b_score|exposure_rank` regex.
- **Value layer** (this rubric):
  - monotonic integer sequences or dense rank-ordered columns inside a
    declared non-rank field class → reject;
  - a column equal to a digest-mapped position of any B screen output →
    reject; B artifacts are referenced by digest only, never as values;
  - any column whose values exactly reproduce an ordering of the
    susceptibility screen (Kendall τ = 1.0 vs the bound B artifact
    ordering) → reject;
  - no inherited K, no T2A performance statistics, no prior ranking —
    provenance digests only (D2 §8).

### 2.3 Post-event values

For every cell (row × column): the feature value must be computable
from information available strictly before the prediction cutoff.

- `feature_availability_time <= forecast_issue` per the cutoff chain;
  per-column `availability_semantics` must make this checkable, not
  asserted;
- imagery-derived features: acquisition end must precede the cutoff —
  a Sentinel-1 scene acquired after the event interval is post-event
  evidence, not a predictor;
- features aggregated over windows that intersect the event interval
  are `CENSORED_OR_AMBIGUOUS`, never silently kept;
- any column whose production pipeline ran after the target window
  (processing timestamp > forecast_issue) → reject.

### 2.4 Future assimilation

- `REANALYSIS` values are prohibited in forecast experiments — ERA5/
  ERA5-Land/IMDAA assimilate future observations; they may appear only
  in `RETROSPECTIVE_REGIME` matrices;
- vintages must be `REFORECAST` or `ARCHIVED_OPERATIONAL` with
  byte-bound `archive_payload_sha256` + `retrieval_record_sha256`
  (E05);
- `local_retrieval_time` can never make a late product historically
  available — `archive_availability >= issue_time` is enforced, and
  object-store timestamps (`Last-Modified`) are provenance only; a
  preregistered conservative issue+dissemination latency margin
  applies to every archived vintage (publication-time ≠ issue-time);
- ERA5-T provisional-era rows are marked and audited separately —
  provisional values may differ from the consolidated release.

### 2.5 Locked-test and split leakage

- preprocessing statistics (scaler means/variances, imputation values,
  encoding maps) are computed on **train rows only**; any statistic
  whose inputs include validation/test rows → reject;
- duplicate detection across the split boundary: rows deriving from
  the same underlying event (same `cascade_group_id`, same
  `duplicate_of` chain, or identical source row key) may not appear in
  two groups — atomicity is enforced by `cascade_atomicity_problems`;
- spatial-neighbor leakage: features that aggregate a spatial
  neighborhood overlapping a held-out basin's cells are flagged —
  neighborhood windows must respect group boundaries;
- no column may be built using the outcome window (target-derived
  aggregates), the control-selection frame, or future event labels;
- nothing is tuned on locked test basins — preprocessing, K,
  thresholds, features, models (D2 §6).

### 2.6 Integrity checks

- sentinel values: declared sentinel policy per column; undeclared
  sentinels (`-999`, `9999`, `1e20`) → reject;
- non-finite values outside a declared missingness policy → reject;
- unit consistency: values outside the declared `value_domain` by >3σ
  of the declared unit's physical range → reviewer adjudication;
- row identity: `unit_id` + window must join to a bound
  `ObservationOpportunityV0`/`EventLabelV0`/`ControlWindowV0`;
- byte binding: the matrix file is hashed into `EvidenceArtifactV0`
  (`sha256` + `size_bytes` + `as_of_date`) — digests must equal the
  artifact record, not a caller-supplied string (C19).

## 3. Freeze criteria

`RESEARCH_FEATURE_MATRIX_FROZEN` requires all of:

1. every column audited per §1 with `audit_verdict = pass` or
   `censored` (rejected columns removed, removal logged);
2. `CutoffRecordV0` chain complete and ordered per column-class;
3. `HoldoutPlanV0` bound with complete `event_assignments`;
4. `EvidenceArtifactV0` of type `feature_matrix` byte-bound under
   `evidence_root`;
5. zero exposure/impact predictors; zero B-derived values; zero
   reanalysis values in forecast mode;
6. value-audit report itself bound as a `source_evidence` artifact;
7. `FMX_BLOCKED_CUTOFF` instead whenever any cutoff component is
   unknown — a blocked freeze is a valid, honest outcome.

## 4. Honesty boundary

A passing audit proves the matrix is *admissible for research*, not
that it is predictive. Freeze status carries no claim about skill,
readiness, or operational fitness; those terms are outside this
document's vocabulary by construction.
