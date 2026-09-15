# Hybrid Route Reconciliation — v0 (Run A)

**Status:** `RECONCILIATION_RECORDED` — this document records the
authorized-vs-actual acquisition comparison and a local numeric
harmonization check. It decides nothing; disposition is an owner call.
**Lane:** Run A (`run-a/reconciliation`).
**Scope:** P5-C authorization text vs the executed hybrid retrieval for
run `gmm_confirmation_20260915T052240Z`
(`research_runs/gmm_hybrid_20260915/`, gitignored, digests bound in
`RUN_A_EVIDENCE_SUMMARY_V0.md`).

## 1. What P5-C authorized (verbatim scope)

From `P3_ATTESTATION_RECORD_V0.md` §"P5-C bounded-acquisition
authorization (owner-directed, same date)":

> - Scope: exactly 78 monthly ERA5-Land JJA requests, 2001–2025 full
>   JJA + 2026 through August 25 only.
> - Variables: the seven pre-registered ERA5-Land variables, with
>   `snow_depth_water_equivalent` (sd), never `snow_depth`/`sde`.
> - Run root: a unique timestamped directory under `research_runs/`
>   (gitignored, non-frozen).
> - Disk floor: abort below 8 GiB free.
> - Purpose: retrospective descriptive regime check only —
>   `EXPLORATORY_DESCRIPTIVE_SINGLE_CELL`.

## 2. Authorization vs actual

| Dimension | P5-C authorized (literal) | Executed | Deviation? |
|---|---|---|---|
| Variable set | 7 contract vars incl. `snow_depth_water_equivalent`, never `sde` | Same 7 vars; `sde` absent from all payloads (gate-enforced) | none |
| Period | JJA 2001–2025 + 2026 through Aug 25 | JJA 2001–2026, 2026-08 capped at day 25; no post-cutoff timestamps | none |
| Spatial | implied 1° area box `[29.0,85.0,28.0,86.0]` + deterministic cell selection | Point request at selected node (28.3, 85.5) on all three routes | request shape |
| Request plan | 78 monthly requests to `reanalysis-era5-land` | 1 ARCO timeseries request + 1 EarthDataHub mirror extraction + 2 CDS `reanalysis-era5-land` requests; 78 monthly files assembled locally | request plan and dataset mix |
| Dataset | `reanalysis-era5-land` | `reanalysis-era5-land-timeseries` (ARCO regridded copy), EarthDataHub DestinE Zarr mirror, `reanalysis-era5-land` | two additional retrieval surfaces |
| Run root | unique dir under `research_runs/` | `research_runs/gmm_hybrid_20260915/` | none |
| Disk floor | abort < 8 GiB | pre-acquisition + pre-merge checks recorded, all `ok` (≥12.49 GiB) | none |
| Validation | unchanged `normalize_payload` gate | all 78 months passed it | none |
| Purpose | `EXPLORATORY_DESCRIPTIVE_SINGLE_CELL` | result carries that status | none |

**Reading.** The authorization's evident intent — the same ERA5-Land
data content, same period, same cell, same downstream chain — was met.
Its literal shape — 78 monthly requests against the primary archive —
was not: queue serialization on the MARS-backed dataset motivated a
hybrid route (rationale recorded in `P0_BASELINE_LEDGER.md`; queue
metadata itself is UNVERIFIED here). Two retrieval surfaces outside
the literal authorization were used: the CDS ARCO timeseries copy and
the EarthDataHub DestinE mirror.

**Disposition options (owner decision required — this lane does not
decide):**

- **Option A — owner-ratified amendment.** The owner records that the
  P5-C network authorization covered the ERA5-Land data content for the
  bounded period/cell, and ratifies the ARCO + EarthDataHub surfaces as
  in-scope delivery mechanisms. Run A then stands as the executed P5-C
  diagnostic under an amended route, with §3–§4 below as its
  harmonization evidence.
- **Option B — method-only retention.** Absent ratification, the route
  deviation stands as an unratified scope deviation. Run A is then
  retained explicitly as implementation/method-confirmation only: it
  proves the fetcher → normalize → extract → GMM chain on real
  ERA5-Land bytes, but its result may not be cited as P5-C-authorized
  evidence, and any future evidentiary use requires either a rerun
  under the literal authorized route or a subsequent amendment.

Both options preserve every downstream limitation in
`RUN_A_EVIDENCE_SUMMARY_V0.md` §6.

## 3. What is verifiable now about the three routes

From the payloads on disk (integration worktree, read-only):

| Property | ARCO timeseries (`reanalysis-era5-land-timeseries`) | EarthDataHub DestinE mirror | CDS `reanalysis-era5-land` (MARS) |
|---|---|---|---|
| Delivery | ZIP, 3 NetCDF members (wind / 2 m temperature / pressure-precipitation groups) | single NetCDF (HDF5 magic) | ZIP, 1–2 NetCDF members (`data_*.nc`) |
| Time coord | `valid_time`, hourly, datetime64 | `valid_time`, hourly, datetime64 | `valid_time`, hourly, datetime64 |
| Coverage in run | 2001-01-01 → 2026-08-25T23 (224,832 steps) | 2001-01-01 → 2026-05-31T23 (222,768 steps) | 2026 JJA only (1,464 + 600 steps) |
| Cell encoding | scalar lat/lon ≈ 28.30000000000097 / 85.49999999999943 | scalar lat/lon ≈ 28.30000000000097 / 85.49999999999942 | lat/lon dims; node exactly 28.3 / 85.5 |
| Extra coords | none | `depthBelowLandLayer=100`, `number=0`, `surface=0` | `number=0`, `expver` (`0001` Jun–Jul; `0005` Aug = preliminary-era segment) |
| Units | K; m s⁻¹; m | m of water equivalent (sd, sf) | K; m s⁻¹; m; m of water equivalent |
| Precision | float32 | float32 | float32 |
| Accumulation semantics | tp delivered as per-hour increments | sd, sf share the CDS accumulated-field encoding (verified §4) | GRIB `stepType=accum` for tp, sf (running within-day accumulation; 00:00 slot carries the prior day's closing value); `instant` for t2m, d2m, u10, v10, sd |

Assembler-side harmonization in `nepal/era5_hybrid_fetch.py`
(verified by reading code + outputs): `valid_time`→`time`
canonicalization, scalar→size-1 dim promotion, node verification
before relabeling, exact contract-cell coordinate assignment
(eliminating the 28.30000000000097 vs 28.3 float epsilon that would
break `join="exact"`), disjointness assertion across the two snow
sources, and the unchanged `normalize_payload` gate per month.

Window note: the raw ARCO request span (2001-01-01 → 2026-08-25)
covers non-JJA months, but the assembled and used window is JJA-only —
the assembler asserts the 78-month JJA set and no non-JJA rows enter
the monthly payloads. The 2026-08 `sd`/`sf` segment additionally
carries `expver=0005` (preliminary, ERA5T-class): recorded as
preliminary, acceptable for method-only use, and flagged for an
exclusion sensitivity check under any future evidentiary use.

## 4. Local numeric comparison (no download — existing files only)

The earlier pure-CDS attempt (`research_runs/gmm_confirmation_20260915T020537Z/`,
stalled at 5/78 months, plus `shard_2001_2007/`) left normalized
full-box payloads for 2001-06/07/08 and 2002-06/07. All 5 months were
compared variable-by-variable against the hybrid assembled payloads at
the selected cell (720–744 hourly steps each).

| Variable | Max abs diff | Interpretation |
|---|---|---|
| u10, v10 | 0.0 (bitwise identical) | identical bytes at the cell |
| t2m, d2m | ≤ 2.44e-4 K | float32 re-encoding/regrid rounding (~1e-6 relative) |
| sd | ≤ 2.9e-6 m w.e. | rounding-level agreement (instant field) |
| sf | ≤ 3.6e-6 m w.e. | rounding-level agreement — **same accumulated encoding on both routes** |
| tp | raw: up to ~4.6e-2 m | **not equal by construction** — different stamping semantics (below) |

**tp semantics finding (verified).** CDS/MARS `tp` is a running
within-day accumulation whose 00:00 slot holds the previous day's
closing accumulation. ARCO `tp` is per-hour increments. Deaccumulating
the CDS field reproduces the ARCO increments to ≤ 9.3e-10 m for all
interior hours (hours 2–23 of each day, all 5 months) and exactly at
hour 1; the 00:00 slot differs by construction (≤ 4.0e-3 m vs the
prior-day total, consistent with GRIB packing precision). The
assembled monthly files carry the ARCO increments bitwise
(verified: monthly ≡ assembled, max diff 0.0), and `tp_daily` in the
feature CSV equals the 24-hour increment sum ×1000 mm to ≤ 1e-6 mm —
so `tp_daily` is a correct daily total **under the ARCO convention
only**. Had the pure-CDS route been used, the extractor's
`resample("D").sum()` (which assumes per-hour increments) would have
summed a running accumulation — measured on the same 5 months this is
a ~7× monthly-total over-count (per-day ratios ~5–9×) — unless
deaccumulated first. `normalize_payload` performs no deaccumulation.

**sf semantics finding (verified).** `sf` is accumulated on both the
EarthDataHub and CDS routes (carryover structure `sf[d,0] ≈ sf[d-1,23]`,
correlation 0.987; interior hours nondecreasing on 189/215 sampled
days; GRIB `stepType=accum` on the CDS payload). The extractor applied
the same per-hour-increment assumption, so **`sf_daily` in the Run A
feature matrix equals the sum of 24 running-total slots — an inflated
quantity, not a daily snowfall total** (e.g., 2001-06-03: feature
41.0 mm vs closing accumulation 4.04 mm; monthly aggregate ~5.7× for
2001-06). `sd` is an instant field and
`sd_daily` (daily mean) is unaffected. This defect is recorded against
the Run A feature set; it does not alter the acquisition or merge
layers, which carried the values faithfully.

**What could not be compared locally:** the 73 months with no
pure-route counterpart — numeric equivalence is demonstrated only for
the 5 overlap months and all untested months remain UNVERIFIED — and
the EarthDataHub mirror's generation provenance (no EDH fetcher exists
in `nepal/`; the mirror is asserted by ledger label + payload digest
only).

## 5. Conclusion recorded (not decided)

Bitwise equivalence between the hybrid route and the literal
authorized route **cannot be demonstrated**: the comparison shows
bitwise identity only for u10/v10 on 5 of 78 months, rounding-level
agreement for t2m/d2m/sd/sf, and a construction-level (semantic, not
noise) difference for tp, alongside a verified `sf_daily` feature
defect. Pending the owner decision in §2, **Run A is retained as
method-only implementation-confirmation** — it is not
P5-C-authorized scientific evidence and may not be cited as such.
The §4 comparison is bounded-overlap evidence only — five of 78
months — and establishes no route equivalence for the untested
months. Nothing in the Run A result (selected K, JS distances,
occupancy values) transfers to the multi-region regime protocol
(`run_b/REGIME_PROTOCOL_V0.md`).
Equivalence, if ever claimed, would require either owner acceptance
of the quantified bounds above or a controlled rerun under the
literal request plan.
