# P3 Basin-Assignment Rule — HMAGLOFDB events → operative basins (V0)

**Status:** `IN-EFFECT` — governs the P3 label package and every
downstream basin-grouped analysis (LORO folds, holdout groups).
**Binding evidence:** `retrieval/anchor_derivation_record.json`
(operative HMA anchors, `FROZEN-3-BASINS-FINAL`), HMAGLOFDB v1.3.0
acquired bytes, RDS 7952 L2 attribution record.

## 1. Operative basin set — 3 basins, final

The operative basins are **`koshi`, `gandaki`, `karnali`** — the
basins with a glacial-lake population in either inventory and L2
polygons in RDS 7952 (HMA attribution: koshi 1,436 / gandaki 301 /
karnali 767 lakes). `mahakali` and `bagmati` have **no glacial-lake
population in either inventory and no L2 polygons** — a geographic
fact recorded in the anchor record (`mahakali_bagmati_finding`); no
lake-derived anchor exists for them under any rule. **No analysis
may scope five basins.** (HMAGLOFDB does contain a small number of
events reported in Bagmati Province — e.g. Melamchi 2021 — which are
labelled and assigned for completeness but carry no ERA5 anchor
feature frame; they cannot enter feature-linked modelling.)

## 2. Event basin assignment — explicit table, hydrology governs

For each HMAGLOFDB row the basin is assigned by lookup of the
whitespace-normalized `River_Basin` value in the explicit mapping
table in `nepal/research_v0/p3_intake.py::RIVER_BASIN_TO_UNIVERSE`
(sub-basins map to their parent operative basin — e.g. `Dudh Koshi`,
`Tamor`, `Arun`, `Tama Koshi`, Tibetan `Poiqu`/`Pumqu` → `koshi`;
`Humla`, `Mugu Karnali`, `Bheri`, `Dhauliganga`, `West Seti` →
`karnali`; `Kali Gandaki`, `Marsyangdi`, `Upper Mustang`,
`Budhi Gandaki`, Pokhara `Seti` → `gandaki`; `Melamchi` → `koshi`).
The Melamchi row keeps its source and administrative values separately
(`raw_river_basin=Melamchi`, `administrative_district=Sindhupalchok`,
`administrative_province=Bagmati`). The source serializes the province as
`Bagmati`; this is the canonical abbreviation for Bagmati Province in the
source record and is not a hydrological basin label. Its derived
hydrological fields are `basin_group=koshi` and
`hydro_subbasin=Indrawati`.
Rows whose river is absent from the table are **ledger-recorded**
(`basin_outside_operative_universe`, raw value retained) and never
silently dropped; no agent-chosen coordinates or polygon point-in-poly
re-derivation is performed on event rows.

## 3. Nepal Province cross-check — conflicts recorded, not resolved by vote

For Nepal rows the `Province` field is a cross-check only. The
hydrological basin (the river that flooded) governs. Conflicts —
e.g. GF 345 (`Tama Koshi`, Province `Bagmati`) — are recorded in the
linkage-uncertainty ledger as `province_conflict` and the row keeps
its hydrological basin.

## 4. Timing precision

`Year_exact`/`Year_approx` + `Month` + `Day` derive intervals
deterministically: full date → `day` (00:00Z bracket, uncertainty
86,400 s); month+year → calendar-month bracket (uncertainty = the
actual month width; declared `interval` when the width measures
INTERVAL_8_30D, `month` when it measures COARSE — class-consistency
C14); year only → calendar-year bracket (`year`). Rows with no
parseable year are ledgered (`unresolved_timing`); year ranges
(`Before 1962`, `1960s`, `2002-2004`) are retained verbatim in the
ledger detail but not parsed. The ±3-day `Sat_evidence` narrowing is
**not applied** — only 4 rows carry scene IDs and no corroborating
day-precision source is bound this cycle.

## 5. Recurrence and cascades

`Repeat == 'Y'` events sharing identical reported lake coordinates
form one atomic `cascade_group_id` (root = earliest event; other
members set `parent_event_id`). The `_Z` GF_ID suffix convention is
**absent** from the acquired v1.3.0 bytes (verified: all `GF_ID`
values are integers) — the payload gate is resolved negative.

## 6. Geographic gates and demotion

The Nepal count is reported separately from the loadable set; events
outside the operative universe remain in the ledger. A basin group
with no loadable events is never declared in the holdout plan
(declared groups must be non-empty). Nothing is demoted silently:
every exclusion has a ledger reason.
