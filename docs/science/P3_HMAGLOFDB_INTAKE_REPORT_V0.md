# P3 HMAGLOFDB Intake Report (V0) — real acquired bytes

**Status:** `INTAKE-COMPLETE-UNADJUDICATED` — labels are byte-bound
and validated; adjudication is an owner step (reviewer identities are
never agent-invented).
**Source:** HMAGLOFDB v1.3.0 (RDS DOI 10.26066/RDS.1973283), zip
sha256 `7903cd5a…2622ca` (sidecar-verified), member digests
cross-checked against `retrieval/retrieval_record_hmaglofdb.json`.
**Package:** `glof-events/p3_event_package_v0.json`
(sha256 `0e786f9cbd79e0975b947baee30c40f9fdb0081b0c3d285a2dde07208393dc0d`,
sidecar present). Implementation: `nepal/research_v0/p3_intake.py`;
regression-pinned by `tests/test_p3_hmaglofdb_intake.py`.

## The six required reports (full 768-row census)

| Report | Result |
|---|---|
| **Row census / Nepal count** | 768 rows; **Nepal 58** (China 203, Kyrgyzstan 198, Pakistan 151, India 61, Kazakhstan 54, Bhutan 21, Tajikistan 17, Afghanistan 5); Nepal loadable **30** |
| **Precision distribution** | day 326, year 230, month 64, unresolved 148 (year spans/ranges like `Before 1962`, `1960s` → ledgered verbatim, 66 rows) |
| **GL_ID / LakeDB_ID / G_ID coverage** | GL_ID valid `GL…` **386**; placeholders 368 (Ephemeral 210, Not mapped 146, No lake 10, Unknown 2); NA 14. LakeDB_ID non-NA 386 (exactly the valid-GL rows). G_ID non-NA 747. `_Z` GF_ID suffix: **absent** (all integers) — payload gate resolved negative |
| **Recurrence** | `Repeat == Y` **448** / N 320 |
| **Cascades** | **70 atomic recurrence groups** covering **438 events** (`Repeat=='Y'` + identical reported lake coordinates; root = earliest, members carry `parent_event_id`) |
| **Removed rows** | **102** — and **all 102 GF_IDs also appear in the main CSV**: `HMAGLOFDB_removed.csv` is a duplicate-recording table, not a disjoint deletion log; the main CSV is the operative row set |
| **Post-2025 rows** | **0** (year range 1533–2025) |
| **Actual bytes** | zip 107,879; main CSV 449,837; removed CSV 27,942; encoding cp1252 (schema.ini ANSI) |

## Derived label package

- **45 loadable event labels** (30 Nepal + 15 hydrologically-adjacent
  Tibetan headwater rows in mapped basins): koshi 32, karnali 8,
  gandaki 5. All `vertical_id=glof`, `mechanism=
  lake_outburst`, `geometry_role=lake_point`, UNADJUDICATED with no
  reviewer identities (owner adjudication is a separate step).
- **1,175 observation opportunities** (47 PDGL lakes × JJA
  2001–2025), state UNKNOWN — no observation frames are bound this
  cycle.
- **1,175 control windows**, state **derived** via
  `derive_control_state` — all honestly `CENSORED_OR_AMBIGUOUS`
  (a NEGATIVE control requires an OBSERVED_FULL opportunity).
- **Event holdout plan** `p3-hmaglofdb-basin-holdout-v0`: rule `basin`,
  assigned before filtering; test = {koshi, gandaki} (2 named
  evaluation regions), validation = {karnali}, train is empty under the
  explicit `holdout_mode=evaluation_only` waiver. Event labels are
  evaluation inputs only and never enter regime fitting; the separate
  regime holdout is the authenticated temporal amendment (JJA 2001–2017
  train / 2018–2019 embargo / 2020–2025 holdout). Cascade groups remain
  atomic by construction.

## Linkage-uncertainty ledger (nothing dropped silently)

| Reason | Rows |
|---|---|
| `basin_outside_operative_universe` | 542 |
| `unresolved_timing` | 296 |
| `unparseable_year` | 66 (year ranges — raw values retained) |
| `province_conflict` | 2 (GF 345 and Melamchi GF 515: hydrological koshi vs Province Bagmati — hydrology governs; administrative fields are preserved separately; HMAGLOFDB serializes the province as the canonical abbreviation `Bagmati`) |

## Honest limitations (V0)

1. Labels are UNADJUDICATED; the ≥2-reviewer gate is intentionally
   unsatisfied until the owner adjudicates.
2. `Sat_evidence` ±3-day narrowing is not applied (4 scene-ID rows
   only; no corroborating day-precision source bound).
3. Opportunity frames are unbound (UNKNOWN); no control can be
   NEGATIVE this cycle.
4. Year-range rows (66) and no-year rows (296 minus overlaps) are
   recoverable later only via an owner-approved narrowing rule.
