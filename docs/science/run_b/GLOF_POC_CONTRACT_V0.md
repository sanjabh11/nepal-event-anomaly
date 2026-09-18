# GLOF Descriptive PoC Contract V0 — Round-11 thin-PoC (2026-09-18)

**Status:** design contract committed; synthetic path verified;
real-data gates blocked on P5 authorization.
**Posture:** `DESIGN_DRAFT_COMPLETE`; P3 design-only;
`NO_QUALIFYING_PILOT_SOURCE`; `WARNING_PATH_AUTHORIZED: NO`.

This document freezes the thin, byte-bound descriptive path for one
GLOF event source (HMAGLOFDB v1.3.0, pending P5). It implements the
Round-11 audit's frozen interface contract — **no new validators,
no new schema, no association, no forecast, no warnings.**

## 1. Scope

One hazard vertical (`glof`/`lake_outburst`), one event source, one
feature source (reanalysis, `data_class=REANALYSIS`), one bounded
Nepal subset (basin-level units), one descriptive regime result.
Admissible receipt statuses: `RUN_ERROR`, `CANDIDATE_ONLY`,
`UNDERPOWERED_DESCRIPTIVE_ONLY`, `DESCRIPTIVE_REGIME_ONLY`.

## 2. New interfaces (the only additions)

`nepal/research_v0/source_intake.py`:

- `build_source_manifest(evidence_root, *, source_id, source_version,
  source_files, units, feature_allowlist, lineage) -> dict` — emits
  the existing non-fixture `source_manifest` shape. `source_version`
  is bound into `lineage` as a parseable `source_version=…;` token
  (the serialized schema admits no version field). Rejects malformed
  relpaths (absolute/traversal), duplicate relpaths, non-64-hex
  digests, empty/dup units/allowlist. Byte verification is NOT done
  here — `verify_source_evidence` remains the single policy.
- `load_hmaglofdb_rows(path, *, source_manifest, column_map)
  -> tuple[SourceRow, ...]` — fail-closed CSV intake. Required
  `column_map` keys: `source_row_key, basin, interval_start,
  interval_end, declared_precision, mechanism`; optional:
  `cascade_group_id, parent_source_row_key, observed_on`. Rejects:
  path outside `evidence_root`, path not declared in `source_files`,
  byte mismatch vs declared sha256, manifest evidence problems,
  missing/unknown column_map keys, missing CSV columns, duplicate
  row keys, unparseable/inverted intervals, unknown
  basin/mechanism/precision, zero rows.

`nepal/science_v0/glof_poc.py`:

- `build_hmaglofdb_event_package(rows, *, source_record,
  opportunity_frame, group_of_basin, split_of_group,
  evaluation_regions, embargo_seconds) -> dict` — returns exactly:
  `source_record, event_labels, opportunities, controls,
  holdout_plan, source_manifest_digest, event_digest,
  opportunity_digest, control_digest, holdout_digest`. Events pass
  `normalize_event → deduplicate → validate_cascade_graph →
  to_event_label → deserialize_record → problems()==[]`. Controls
  are DERIVED by `build_controls` (NEGATIVE only under an
  OBSERVED_FULL, non-overlapping opportunity). A holdout that fails
  its gates yields `holdout_plan = {"rejected": True, "problems":
  [...]}` — the runner demotes; the validator is never weakened.
- `run_glof_descriptive_poc(feature_frame, feature_cols, train_mask,
  regime_config, event_package) -> dict` — emits the
  `GLOF_POC_RECEIPT_V0`. Gate order: `RUN_ERROR` (package malformed /
  regime error / freeze rejection) → `CANDIDATE_ONLY` (source posture
  not EVIDENCE_VERIFIED, or artifact non-descriptive) →
  `UNDERPOWERED_DESCRIPTIVE_ONLY` (valid descriptive artifact, but
  holdout/event package cannot support association) →
  `DESCRIPTIVE_REGIME_ONLY`.

## 3. Receipt schema (non-promotable)

```text
record_type: "GLOF_POC_RECEIPT_V0"
status: RUN_ERROR | CANDIDATE_ONLY | UNDERPOWERED_DESCRIPTIVE_ONLY
        | DESCRIPTIVE_REGIME_ONLY
claim_scope: "research_only_no_operational_authorization"
source_manifest_digest, event_digest, opportunity_digest,
control_digest, holdout_digest, regime_artifact_digest, report_digest
promotion_eligible: false
production_authorized: false
warning_path_authorized: false
problems: list[str]
```

`report_digest` is `sha256_canonical` over the receipt minus
`report_digest` and `problems`. The receipt is NOT in
`RECORD_CLASSES` and cannot authorize anything.

## 4. Timing / precision semantics (finding 13)

- Declared precision terms map to the interval they honestly bound:
  day → `day`; a month-precision event is a bounded 30/31-day
  interval → `interval`; year/unbounded → `year` → measured COARSE →
  `unresolved`. The measured class is what `EventLabelV0` binds —
  declared terms inconsistent with measured width reject, never
  silently upgrade.
- Censored events stay in the package (they are assigned holdouts
  before filtering); they are simply inadmissible for
  precision-gated horizons.

## 5. Holdout rules (finding 14)

`assign_holdouts` + `holdout_plan_from_assignment` +
`HoldoutPlanV0.problems()` are reused verbatim: ≥3 geographic
groups, ≥2 independent basins, ≥2 test-group evaluation regions,
no Langtang-only validation, assignment before filtering,
`test_locked`, finite `embargo_seconds`. Sparse real inventories
that cannot satisfy these demote to
`UNDERPOWERED_DESCRIPTIVE_ONLY` — the gates are not weakened.

## 6. Owner gates (not agent-decidable)

- **P5 authorization** (audit finding 6): authenticated owner
  decision naming source+version+DOI, licence scope, evidence root,
  storage reserve, retrieval limits, stop rules. Template section
  added to `P3_ATTESTATION_TEMPLATE.md`. Absent → no download,
  posture stays `NO_QUALIFYING_PILOT_SOURCE`.
- **Opportunity-frame policy** (finding 9): may an independently
  byte-bound lake-inventory source supply the opportunity frame
  (with linkage + uncertainty), or must opportunities be
  source-native? Option A (external linkage) / Option B
  (source-native only → HMAGLOFDB stays disqualified) — recorded in
  the P5 template; no default assumed.
- **Denylist** (finding 30): `pinned/`, `data/`,
  `nepal/framework_v1/`, `preregistration.md` untouched; acquisition
  writes to a fresh external evidence root only.

## 7. Verified synthetic path

Temporary-byte fixture → manifest → `load_hmaglofdb_rows` →
`build_hmaglofdb_event_package` → `run_glof_descriptive_poc`
produces a typed receipt; malformed inputs fail closed at each
stage (regressions in `tests/test_glof_poc_contract.py`).

## 8. Explicit non-goals

No association claim, no forecast-skill claim, no warning path, no
production use, no scientific-qualification claim. Real acquisition,
real event package, real FMX audit, and the real descriptive run
are the P5-gated phases.
