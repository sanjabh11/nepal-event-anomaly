# Run A Evidence Summary — v0

**Status:** `DOCUMENTATION` — compact audit record of an executed,
bounded diagnostic. Nothing here authorizes intake, prediction,
warnings, production, or authority action.
**Lane:** Run A (`run-a/reconciliation`).
**Scope:** keep the completed Run A result auditable without vendoring
raw data into the repository. The run root is gitignored; this record
binds it by path and sha256 digests.

## 1. Identity

| Field | Value | Verification |
|---|---|---|
| run_id | `gmm_confirmation_20260915T052240Z` | verified — `gmm/bundle.json` |
| run root | `research_runs/gmm_hybrid_20260915/` under the integration worktree (gitignored) | verified — exists, read-only to this lane |
| run_dir (result) | `<run_root>/gmm/` | verified |
| ledger | `<run_root>/download_ledger.json` (`status=completed`, `hybrid_assembly=true`) | verified |
| assembly window | ledger `start_time` 2026-09-15T10:52:14 → `end_time` 10:52:17 (assemble+merge step; fetch steps ran earlier the same day) | verified from ledger; fetch wall-clock UNVERIFIED |
| executor environment | python 3.14.7, sklearn 1.9.0, numpy 2.5.3, pandas 3.0.5 | verified — `bundle.json.environment` |
| result status | `EXPLORATORY_DESCRIPTIVE_SINGLE_CELL` | verified — `gmm_results.json.status` |

## 2. Route table (hybrid retrieval)

Recorded in `download_ledger.json.retrieval_paths`; fetcher:
`nepal/era5_hybrid_fetch.py` (added in commit `13195d4`).

| Variables | Period | Retrieval path | Request shape |
|---|---|---|---|
| t2m, d2m, u10, v10, tp | 2001-01-01 → 2026-08-25 (cutoff-bounded) | CDS `reanalysis-era5-land-timeseries` (ARCO regridded copy of ERA5-Land) | 1 point request at cell (28.3, 85.5); ZIP payload with 3 variable-group members |
| sd, sf | 2001–2025 JJA (plus full months through 2026-05-31 coverage end) | Earth Data Hub DestinE Zarr mirror of `reanalysis-era5-land` | 1 mirror extraction, point at selected node |
| sd, sf | 2026 Jun–Jul + Aug 1–25 | CDS `reanalysis-era5-land` (MARS-backed) | 2 requests; 2026-08 carries `expver=0005` (preliminary-era segment) |

Each of the 78 planned months was then assembled locally and passed
through the unchanged `normalize_payload` gate from
`nepal/era5_download.py` (exact variable set, exact UTC hourly
timestamp sets, all-finite, `sde` absent, no post-cutoff rows).

Note on windows: the raw ARCO request span (2001-01-01 → 2026-08-25)
covers non-JJA months, but the assembled and used window is JJA-only —
the assembler asserts the planned month set, and no non-JJA rows enter
the monthly payloads, the merged file, or the feature matrix.

## 3. Input digests (sha256, recomputed by this lane)

| Artifact | sha256 | Cross-check |
|---|---|---|
| `download_ledger.json` | `4db888e9f21c9cd7e95b9b71edf97f5397d3a62dec47a1b12d248ee1594bc1ba` | matches `run_metadata.json.download_ledger_sha256` |
| `gmm/bundle.json` | `9c4588f7e8b8d568410dbda66fa422f376e33017d769a551a306b9a8fdba2235` | self-consistent bundle |
| `gmm/gmm_results.json` | `1afb6c1f6f6e66375c8dabe827f5c74839caeaf8314b135d4a3581f1a10fe4c2` | matches `bundle.json.results_digest` |
| `merged/era5_land_nepal_jja_2001_2026.nc` | `76d393b0e18a6aee81fdf15e0572da877143af5dee22680776e90c8aed5b289c` | matches bundle `input_digests` + `complete.json.merged_sha256` |
| `merged/complete.json` | `ad3e9e5d6a4e605958e04269f6abc15608cd8e3dca85604b9047ac117ab1c297` | matches `run_metadata.complete_marker_sha256` |
| `features/features_nepal_jja_2001_2026.csv` | `c8425f1f302f3621bc7263efe2abaa305583805b03bd7e6476e9b8fde1f83102` | matches bundle `feature_digest` |
| `features/features_nepal_hourly_jja_2001_2026.csv` | `391bf92a2b75488862af91546e25672011e6385e49915e3dc8030723ddf2d756` | matches bundle `input_digests` |
| `features/feature_units.json` | `7eda9e147b9fc880aea2892855eb169676225599f21407137aefeb18b65ab9a6` | matches bundle `feature_units_digest` |
| `features/run_metadata.json` | `fc556fa3f2e06633e5a9b0755dafa432ed60eb03a6ff3ff8964942f4774f27e6` | matches bundle `input_digests` |
| `provenance_receipts.json` *(addendum 2026-09-16)* | `8f22d3c17b940e5e789e6428b9758d6bafdc0182af3fb4f2eaf60fcd2963d635` | recomputed by this lane; matches `rehash_report.json` entry (21,607 bytes); written by `nepal/run_ledger_repair.py` `write_provenance_receipts` |

**Addendum (2026-09-16).** The row above binds the H02 provenance
receipts file for run root `research_runs/gmm_hybrid_20260915/` — it
existed on disk and was re-hashed; the digest agrees with the
independent `rehash_report.json` record in the same root. For
completeness: the corrected derivative root
`research_runs/gmm_hybrid_corrected_20260915/` also carries a
`provenance_receipts.json` — sha256
`e757207b6d1616b982d53ec4984a46dc29aaceebefd84e2fcdb849ba6ce080dc`
(200 bytes) — a near-empty stub (`routes`/`assembly_artifacts`/
`unattributed` all empty, `dataset: "UNVERIFIED"`) because the
derived run produced no new retrievals. Both files are gitignored
working-tree artifacts bound here by digest only.

All 78 per-month `payload_sha256` values in the ledger are distinct and
match the bundle manifest's `assembled_YYYY_MM.nc` raw digests
(spot-verified on boundary months; full per-file re-hash of `raw/` was
not repeated by this lane — the bundle's own digest map is bound above).

## 4. Row counts (verified against artifacts)

| Quantity | Value | Source of verification |
|---|---|---|
| planned months | 78 | ledger `planned_months` |
| completed months | 78, all `status=downloaded`, `complete=true`, `expected_hours == actual_hours` | ledger `completed_months` (parsed) |
| month set | JJA only (06/07/08), years 2001–2026; 2026-08 truncated at day 25 (600 h) | ledger + monthly filenames |
| merged hours | 57,264 | `complete.json.total_hours`; hourly feature CSV has 57,264 rows |
| JJA daily feature rows | 2,386 | `features_nepal_jja_2001_2026.csv` (2,387 lines incl. header) |
| edge-censored rows | 156 (`edge_censored=True`; 6 June days × 26 years) | CSV parse; matches `gmm_results.missingness` |
| usable fit rows | 2,230 | `gmm_results.rows_used` = `input_rows`; 0 other NaN |
| baseline rows (2001–2025) | 2,150 (25 × 86) | CSV year histogram, censored rows excluded |
| target rows (2026) | 80 (86 − 6 censored) | CSV year histogram |
| selected cell | (28.3, 85.5); requested/contract (28.25, 85.5); `cell_mismatch=true` by the deterministic tie rule (equidistant latitude → higher) | `run_metadata.json`, `gmm_results.json` |
| model elevation | 4,322 m (~899 m below source elevation 5,221 m) | `run_metadata.json` |

## 5. Descriptive GMM result (verified fields)

- Features (12, `feature_units.json` sidecar): t2m, d2m (degC);
  tp, sf, sd (mm); wind_speed (m/s); wind_dir sin/cos (unitless);
  rh (percent); pdd_daily, pdd_7day (degC·day); freezing_height_m (m).
- Config: covariance `diag`, K ∈ {1,…,5} with the K=1 null included in
  the sweep, seeds {42, 7, 2024}, baseline-fitted StandardScaler
  (target rows never fit the scaler).
- K selection: **modal K=5, frequency 1.0** — all 3 declared seeds
  converged on best_k=5 by BIC (`k_instability=null`,
  `seed_coverage=1.0`, `stability=DESCRIPTIVE_REGIME_ONLY` at the
  declared-seed level only).
- JS distance baseline-vs-target occupancy: observed 0.2618, 95% CI
  [0.2391, 0.2856] (200 temporal block-bootstrap replicates), null p95
  0.0456 → `exceeds_null=true`.
- Membership confidence: mean max-posterior 0.9664, median 1.0,
  ambiguous fraction 0.025 (threshold 0.7).
- Retrospective pre-event overlay (2026-08-19 → 25, 7 days; event dates
  did not influence fitting): cluster sequence [0,0,0,2,0,0,0];
  pre-event occupancy [0.8571, 0, 0.1429, 0, 0];
  `js_distance_pre_event` 0.5084.
- Cross-check recorded in `P0_BASELINE_LEDGER.md`: computed 7-day mean
  t2m 9.42 °C and 7-day PDD sum 65.94 °C·d over 2026-08-19→25 — **this
  lane recomputed both from the feature CSV (9.4196 °C, 65.9375 °C·d):
  verified**. The literature-reported side of that comparison is
  **UNVERIFIED** here (no network; source text not in the repo).
- `k1_null_benchmark=false` — the K=1 fit was swept but the results
  file does not carry a dedicated null-benchmark artifact; treat the
  null-family requirement of `run_b/REGIME_PROTOCOL_V0.md` §5 as not
  yet exercised on real data.

## 6. Explicit limitations (binding)

- Single ERA5-Land grid cell, single basin (Langtang region), single
  pre-event period — no geographic or multi-event generalization.
- Descriptive reanalysis (`REANALYSIS` data class) only — the result
  carries no forecast-skill content and may never be presented as
  forecast evidence (D2 §7, §10).
- No operational authority: no warning-context use, no prediction
  claim, no production deployment, no causal attribution.
- `sf_daily` in the Run A feature matrix is **inflated by
  construction**: the extractor summed 24 hourly slots of an
  accumulated (running-total) `sf` field instead of reading the
  closing daily accumulation. Verified numerically — see
  `HYBRID_ROUTE_RECONCILIATION_V0.md` §4. Any reuse of Run A features
  must carry this caveat.
- `sd`/`sf` for 2026-08 came from the preliminary-era `expver=0005`
  segment (ERA5T-class) of `reanalysis-era5-land` — recorded as
  preliminary, subject to later consolidation by the provider;
  acceptable for method-only use and flagged for an exclusion
  sensitivity check under any future evidentiary use.
- The retrieval route deviated from the literal P5-C request shape
  (78 monthly area-box requests): the executed route was a hybrid
  ARCO (`reanalysis-era5-land-timeseries`) / EarthDataHub DestinE
  mirror / CDS-MARS (`reanalysis-era5-land`) assembly. Disposition
  options are recorded in
  `HYBRID_ROUTE_RECONCILIATION_V0.md` §2; pending owner ratification,
  Run A is retained as **method-only implementation-confirmation** —
  it is not P5-C-authorized scientific evidence and may not be cited
  as such.
- Route-equivalence evidence is bounded-overlap-only: numeric
  comparison against pure-route payloads exists for 5 of 78 months
  (`HYBRID_ROUTE_RECONCILIATION_V0.md` §4); the other 73 months are
  UNVERIFIED and no route-equivalence claim stands.
- No transfer to multi-region science: the fitted K (modal K=5), JS
  distances, occupancy values, and the pre-event overlay are
  single-cell descriptive outputs of this run only. None of them
  transfer into the multi-region regime protocol
  (`run_b/REGIME_PROTOCOL_V0.md`), which refits every artifact from
  its own bound inputs and forbids inherited K/performance transfer.

## 7. What remains UNVERIFIED by this record

- Fetch wall-clock times and per-request queue behaviour (only the
  ~3 s assemble+merge window is in the ledger; the "MARS queue
  serialization" motivation is a recorded claim in
  `P0_BASELINE_LEDGER.md`, not re-verified here).
- The exact EarthDataHub request mechanics (no EDH fetcher exists in
  `nepal/`; the mirror file's provenance is asserted by the ledger
  route label and its payload digest only).
- Bitwise or near-exact equivalence of the hybrid payloads to the
  authorized pure-route payloads: numeric equivalence is demonstrated
  only for the 5 overlap months (with the `tp` stamping-semantics
  difference noted in the reconciliation doc); the remaining 73
  months have no pure-route counterpart and are UNVERIFIED.
- Any value not traceable to the digests in §3 is UNVERIFIED.
