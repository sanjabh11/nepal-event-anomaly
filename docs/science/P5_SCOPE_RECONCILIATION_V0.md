# P5 Scope Reconciliation (V0) — audit findings SCOPE-01, SCOPE-02, HOLD-01

**Status:** `IN-EFFECT` — resolves the scope findings raised in the
Codex post-acquisition audit. Binding evidence:
`retrieval/anchor_derivation_record.json`,
`retrieval/holdout_feature_gate_report.json`,
`P3_BASIN_ASSIGNMENT_RULE_V0.md`.

## 1. Basin scope — three basins, final (SCOPE-01)

The operative universe is `{koshi, gandaki, karnali}` — FINAL.
`mahakali`/`bagmati` have no glacial-lake population in either bound
inventory and no RDS 7952 L2 polygons; no lake-derived anchor is
possible under any rule. Older five-basin contract text is superseded
for the real path; the synthetic `BASIN_UNIVERSE` vocabulary (15 names)
remains as the *type-level* basin namespace — it constrains valid basin
identifiers, not required scope. No protected path was edited; no
fourth/fifth basin will be added without a scope amendment and new
source evidence.

## 2. Event geography — hydrological, not national (SCOPE-02)

**Explicit scope declaration:** the event package includes rows whose
*rivers drain into the operative basins*, regardless of `Country` —
the GLOF hazard boundary is the drainage basin, not the border. Of the
45 loadable events: 30 Nepal, 15 Tibetan/Indian-headwater rows mapped
via the explicit `RIVER_BASIN_TO_UNIVERSE` table (Poiqu, Pumqu, Arun,
Humla headwaters, etc.). Every excluded row is in the linkage-uncertainty
ledger (`basin_outside_operative_universe` 542); nothing is silently
dropped. This policy is now declared here so downstream consumers do
not infer a Nepal-only event universe.

## 3. Holdout axes — decoupled by design (HOLD-01)

Two distinct group axes exist and are NOT the same object:

- **Event holdout** (`p3-hmaglofdb-basin-holdout-v0`): assigns EVENTS
  for evaluation — train={bagmati}, validation={karnali},
  test={gandaki, koshi}. Bagmati's single event stays honestly in the
  event record even though no feature frame exists for it.
- **Regime fit groups** (`RegimeRunConfig.train_groups/heldout_groups`):
  declare FEATURE-frame `basin_group` values — only
  {koshi, gandaki, karnali} exist. Declared: train={koshi, gandaki},
  heldout={karnali} (LORO-style). Verified in
  `retrieval/holdout_feature_gate_report.json`: nonempty, disjoint,
  in-universe, nonempty masks — **PASS**.

No bagmati features are invented; the event holdout is not coerced;
the regime fit cannot and does not claim bagmati coverage.

## 4. Downstream agreement

All manifests, the runner package, holdout digests, and this record
use the same three-basin operative set. Any proposal to widen scope
is a formal amendment, never a silent drift.
