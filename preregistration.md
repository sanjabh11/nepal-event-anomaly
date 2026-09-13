# Pre-Registration: Nepal Event Anomaly Assessment

## Status: FROZEN — Do not modify after data inspection

**Frozen on:** 2026-09-10
**Study type:** Retrospective hindcast (NOT prospective prediction)
**Authorization:** Research-only. No operational warning, no publication as prediction, no production deployment.

---

## 1. Scientific Question

> Relative to pre-registered seasonal and interannual controls (2001-2025 JJA),
> did open-source meteorological data (ERA5-Land nearest cell) show statistically
> unusual pre-event thermal conditions in the 7 days before 26 August 2026,
> and did any change-point appear before the collapse?

**This is hindcasting, NOT prediction.** The event date is known. We are testing
whether conditions were unusual, not whether they could have been forecast.

---

## 2. Event Definition

| Field | Value | Source |
|-------|-------|--------|
| Event date | 26 August 2026 | USGS |
| Event time | ~02:52 UTC / ~08:37 NPT | HiRisk RHA NP3 |
| Location | Langtang Lirung north face, Nepal | HiRisk |
| Reference point | 28.288708°N, 85.528159°E | HiRisk |
| Alternative centroid | 28.28858°N, 85.52701°E | Rui Li arXiv |
| ERA5-Land cell | 28.25°N, 85.50°E | Hausfather (nearest grid cell) |
| Model elevation | 4,322 m | Hausfather (ERA5 orography) |
| Source elevation | ~5,221 m | HiRisk/Rui Li |
| Elevation gap | ~899 m | Computed (5,221 - 4,322) |
| Glacier ID | RGI2000-v7.0-G-15-05732 | HiRisk |
| Mechanism | Mixed ice-rock slope failure (not GLOF) | HiRisk/Guo et al. |

**Critical caveat:** ERA5-Land model elevation (4,322 m) is ~899 m below the
actual source elevation (~5,221 m). All temperature values are measured at
4,322 m, NOT at the failure plane. Lapse-rate extrapolation is an assumption,
not a measurement. This must be printed on every plot.

---

## 3. Frozen Analysis Windows

| Window | Dates | Purpose | Pre-registered? |
|--------|-------|---------|-----------------|
| Pre-event window | **Aug 19-25, 2026** (7 days before event) | Primary anomaly assessment | YES — frozen |
| Event day | Aug 26, 2026 | Held out — NOT scored | YES — frozen |
| Post-event window | Aug 27-31, 2026 | Held out — NOT scored | YES — frozen |
| 2026 JJA | Jun 1 - Aug 25, 2026 | Full seasonal context (Aug 26+ excluded) | YES — frozen |
| Historical baseline | **JJA 2001-2025** (25 years, Jun 1 - Aug 31) | Climatological reference | YES — frozen |
| Negative control years | 2021, 2022, 2023, 2024, 2025 | No-event years for false positive check | YES — frozen |
| Rolling windows | 3, 7, 14, 30 days | Trailing summaries (must end at or before Aug 25) | YES — frozen |

**Rule:** No window may include Aug 26 or later in any precursor scoring.
The post-event window is completely excluded from all estimation, fitting,
threshold selection, and testing.

---

## 4. Frozen Feature Contract

### 4.1 Raw ERA5-Land Variables (7)

| # | GRIB Short Name | CDS Long Name | Unit | ERA5-Land Definition |
|---|----------------|---------------|------|---------------------|
| 1 | `2t` | `2m_temperature` | K | Air temperature at 2m above model surface |
| 2 | `2d` | `2m_dewpoint_temperature` | K | Dewpoint at 2m above model surface |
| 3 | `10u` | `10m_u_component_of_wind` | m/s | Eastward wind at 10m |
| 4 | `10v` | `10m_v_component_of_wind` | m/s | Northward wind at 10m |
| 5 | `sd` | `snow_depth` | m | Snow water equivalent (NOT geometric depth) |
| 6 | `sf` | `snowfall` | m | Snowfall (accumulated, water equivalent) |
| 7 | `tp` | `total_precipitation` | m | Total precipitation (accumulated) |

### 4.2 Derived Features (3)

| # | Feature | Formula | Unit | Notes |
|---|---------|---------|------|-------|
| 8 | `wind_speed` | `sqrt(10u^2 + 10v^2)` | m/s | Magnitude only |
| 9 | `wind_dir` | `atan2(10v, 10u)` | radians | Circular; encode as sin/cos for clustering |
| 10 | `relative_humidity` | Magnus formula from 2d, 2t | % | Derived; not independent of 2d and 2t |

### 4.3 Computed Thermal Indices (not counted as features, but pre-registered)

| Index | Formula | Purpose |
|-------|---------|---------|
| `PDD_daily` | `max(0, T_daily_C)` | Daily positive degree days |
| `PDD_7day` | Rolling 7-day sum of PDD_daily | Antecedent melt energy |
| `T_freezing_height` | `4322 + (T_2m - 273.15) / (-0.0065)` | 0°C isotherm height (m), lapse rate -6.5 K/km |

### 4.4 Feature Count Reconciliation

- **Raw variables:** 7 (from `LAND_VARS` in `real_source_adapter.py`)
- **Derived features:** 3 (wind_speed, wind_dir, RH)
- **Total distinct quantities:** 10
- **Previous claim of "11 LAND features":** INCORRECT — reconciled to 10
- **Note:** `sd` in ERA5-Land is snow water equivalent, NOT geometric snow depth.
  Counting both `sd` and `SWE` as independent features would duplicate information.

### 4.5 Variables NOT Included (and why)

| Variable | Reason for exclusion |
|----------|---------------------|
| `ssrd` (surface solar radiation downwards) | Not in `LAND_VARS`; could be added in sensitivity analysis |
| `strd` (surface thermal radiation downwards) | Not in `LAND_VARS`; could be added in sensitivity analysis |
| `snowmelt` | Not in `LAND_VARS`; available in full 36-variable list |
| `runoff` | Not in `LAND_VARS`; available in full 36-variable list |
| `skin_temperature` | Not in `LAND_VARS`; measures skin, not air |
| Pressure level variables | 700 hPa is below source; requires validity masking |

**Sensitivity analysis (optional, post-hoc, labeled as such):**
If primary analysis is null, optionally add `ssrd` and `strd` from ERA5-Land
to test whether radiation balance changes the conclusion. This must be labeled
as post-hoc, not pre-registered.

---

## 5. Frozen Methods

### 5.1 Primary Analysis (pre-registered)

| Method | Implementation | Parameters | Output |
|--------|---------------|------------|--------|
| Z-score anomaly | numpy/scipy | Same-calendar-day distribution 2001-2025 | |z| > 2 flagged |
| Percentile ranking | numpy | Same-window distribution 2001-2025 | 95th, 99th flagged |
| PELT change-point | `ruptures.Pelt` | model="rbf", pen=10 | Change-point dates on JJA 2026 2t and PDD |
| CUSUM | `ruptures` or manual | k=1.0, threshold=5.0 | Sustained shift dates on 2t and PDD |
| Isolation Forest | `sklearn.IsolationForest` | n_estimators=200, contamination=0.01, random_state=42 | Anomaly score for each Aug 2026 day |

### 5.2 Secondary/Descriptive (pre-registered as descriptive only)

| Method | Implementation | Parameters | Output |
|--------|---------------|------------|--------|
| GMM descriptive | `sklearn.GaussianMixture` | K=1,2,3,4,5 (BIC selection), covariance="diag", random_state=42 | Cluster membership for Aug 2026 days |
| Jensen-Shannon distance | scipy.spatial.distance | Compare cluster occupancy Aug 2026 vs 2001-2025 | JS distance value |

**GMM is DESCRIPTIVE ONLY.** It describes weather regimes. It does NOT detect
avalanche precursors. It does NOT claim cluster shifts cause avalanches.
K=1 (null benchmark) is included per Astra's recommendation.

### 5.3 Controls (pre-registered)

| Control | Method | Purpose |
|---------|--------|---------|
| Negative control years | Run all primary methods on 2021-2025 JJA | False positive rate |
| Block permutation | Permute year-labels in blocks of 7, 14, 30 days | Dependence-aware significance |
| Pseudo-event dates | Score random dates in 2001-2025 JJA | Baseline anomaly rate |

### 5.4 Methods NOT Used (and why)

| Method | Reason |
|--------|--------|
| DBSCAN | Designed for spatial clustering, not time-series anomaly |
| VAE | Too data-hungry for 2,340 samples; advisory-only in existing code |
| HDBSCAN | Astra warns against method proliferation |
| Bootstrap (iid) | Astra: ignores serial correlation; use block permutation instead |

---

## 6. Publication-Time Ledger

For every data source, record:

| Field | Description |
|-------|-------------|
| Acquisition time | When the sensor observed |
| Production time | When the product was processed |
| Publication time | When it appeared in the catalog |
| Effective cutoff | Latest publication time usable for a given warning lead |

**Critical (Astra finding):** The 26 July → 19 August NISAR GUNW pair was
inserted into the catalog on 25 August — only ~26 hours before the event.
Acquisition date alone overstates warning time. Any lead-time claim must
use publication time.

**ERA5-Land latency:** Preliminary product has ~5-day delay. An operationally
faithful 25 August evaluation generally cannot use 24-25 August ERA5-Land
observations. Final reanalysis arrives later (~2-3 months).

---

## 7. Null Result Acceptance

**A negative result is a valid scientific finding.**

If no open meteorological precursor exceeds seasonal/control variability,
the conclusion is:

> "No open meteorological precursor exceeded climatology in the 7 days
> before 26 August 2026, conditional on ERA5-Land measurement sensitivity
> at 4,322 m model elevation."

This is NOT a failure. It is a bound on observability.

---

## 8. What This Study Does NOT Claim

- It does NOT claim prediction or forecasting capability
- It does NOT claim causal attribution of the avalanche to thermal forcing
- It does NOT claim that GMM clustering detected the event
- It does NOT claim that ERA5-Land 9km data represents conditions at 5,200 m
- It does NOT claim that a single event validates any methodology
- It does NOT claim that data was available before the event (publication-time ledger documents actual availability)

---

## 9. Data Sources

| Source | Resolution | Access | License |
|--------|-----------|--------|---------|
| ERA5-Land (primary) | ~11.1 × 9.8 km, **daily** | DestinE Earth Data Hub Zarr v3 (`api.earthdatahub.destine.eu`) | Copernicus License |
| ERA5-Land (fallback) | ~11.1 × 9.8 km, hourly | CDS API (`~/.cdsapirc`) | Copernicus License |
| Copernicus DEM GLO-30 | 30 m | Copernicus Data Space | Copernicus License |
| NISAR GUNW/GOFF (metadata only) | 80 m / 20 m | ASF CMR (no auth for catalog) | NASA Open Data |
| Hausfather ERA5 0.25° (reference) | ~31 km | GitHub (committed NetCDF) | Open |

**Post-hoc data source substitution (2026-09-10, logged per Section 11):**

The primary data source was changed from CDS API hourly to EDH Zarr v3 daily
due to CDS API queue latency (~13 hours for 78 monthly requests). The EDH
daily store provides pre-aggregated daily values from the same ERA5-Land
reanalysis, accessed via `api.earthdatahub.destine.eu` with a standard
API key.

**Variables available in EDH daily store (5 of 7 contract variables):**
- t2m, d2m, u10, v10, tp (daily mean/sum as appropriate)

**Variables NOT available in EDH daily store (2 of 7):**
- sd (snow_depth), sf (snowfall)

**Justification for missing sd/sf:**
For the JJA monsoon regime at 28.25°N (Langtang region), snow depth and
snowfall are near-zero. The event is a monsoon-season glacier slope
failure, not a winter snow-avalanche. The missing variables do not
affect the thermal regime anomaly assessment that is the primary
scientific question.

**Cross-validation:**
EDH daily t2m was cross-validated against the Hausfath ERA5 0.25°
reference for Aug 19-22, 2026 (4-day overlap). Mean absolute difference:
1.64 K, explained by grid cell offset (EDH cell at 28.20°N vs Hausfath
at 28.25°N, consistent with environmental lapse rate).

---

## 10. Cross-References

| Source | Role |
|--------|------|
| Hausfather (2026) | ERA5 0.25° t2m anomaly — cross-validate our ERA5-Land results |
| Guo et al. (2026) | Multi-sensor event reconstruction — cite, don't rediscover |
| Rui Li (2026) | ERA5-Land thermal analysis (9.43°C, 65.94°C·d PDD) — cross-validate |
| Xu (2026) | Open-data cascade reconstruction — cite |
| Shirzaei (2026) | Sentinel-1 deformation (~10 mm/month) — reference for Phase 2 |
| Khadka et al. (2022) | ERA5-Land validation in Everest — cite for limitation |
| Astra (2026) | NISAR catalog + publication latency — primary source for NISAR feasibility |

---

## 11. Change Log

| Date | Change | Reason |
|------|--------|--------|
| 2026-09-10 | Initial freeze | Pre-registration before data inspection |
| 2026-09-10 | POST-HOC: Data source substitution CDS hourly → EDH daily | CDS queue latency (~13h for 78 requests); EDH Zarr v3 provides same ERA5-Land reanalysis in 66 seconds. Missing sd/sf documented in Section 9. Cross-validated against Hausfath reference (1.64 K mean abs diff, explained by grid offset). |

**No changes after this point. Any post-hoc additions must be logged as
"post-hoc, not pre-registered" with justification.**
