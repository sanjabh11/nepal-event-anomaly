# GLM2 Reconciled Download-Data Plan

**Status:** COMPLETE — HANDOFF INCOMPLETE (with documented gaps)
**Tier:** 2 (multi-phase, persistent artifact)
**Owner:** GLM2 (this session)
**Completed:** 2026-09-11
**Output Root:** `data/framework_inputs_v1_reconciled/`
**Temp Root:** `data/framework_inputs_v1_reconciled_tmp/`
**Write Scope:** Only `data/framework_inputs_v1_reconciled/` and `data/framework_inputs_v1_reconciled_tmp/`

## Stage Summary

| Stage | Status | Key Result |
|-------|--------|------------|
| 0 — Isolated Preflight | PASS | New output root created, existing handoff preserved, no raw SLC, no symlinks, no path escapes |
| 1 — Canonical Manifest | PASS | Schema 2.0.0-reconciled, contract_sha256 bound, self-hash valid, 34 artifacts sorted |
| 2 — Catalog & Literature | PASS | 7 A-role artifacts: Bashkova-Rupper DB, HMA events, locked JJA, adjudication ledger, language coverage log |
| 3 — ERA5 Protocol Separation | PASS | JJA thermal marked as context (Paper 0), winter B screening separate, sd/sf UNAVAILABLE |
| 4 — DEM & RGI | PASS | RGI14/15 .prj repaired, focal RGI ID verified (RGI2000-v7.0-G-15-05732), 300x300 100m EPSG:32645 grid created |
| 5 — Sentinel-2 Dry-Season | INCOMPLETE | Query record exists but no STAC catalog access for automated query |
| 6 — Sentinel-1 Observability | PASS | S1-A inventory READY, winter table INCOMPLETE, pair table PROVISIONAL (deferred to GLM-5.3) |
| 7 — Exposure | PASS | OSM Nepal READY, GHSL/HydroRIVERS/WorldPop UNAVAILABLE |
| 8 — Optional Metadata | PASS | FDSN 472 stations READY (sidecar), permafrost/thermal UNAVAILABLE (sidecars) |
| 9 — Handoff Gate | PASS | Manifest valid, sizes OK, no raw SLC, all READY have checksums |
| Verification Matrix | PASS | 76 tests pass (unit, integration, fuzz) |

## Handoff Gate Result

```
HANDOFF STATUS: INCOMPLETE (with documented gaps)
```

### Gate Checks

- Manifest valid: True
- Self-hash valid: True
- Schema: 2.0.0-reconciled
- Persistent: 855.4 MB / 5120 MB — OK
- Temporary: 0.0 MB / 8192 MB — OK
- Raw SLC: 0 found — OK
- All READY artifacts have checksums: True

### Artifact Counts

```
READY:       18
INCOMPLETE:   3
PROVISIONAL:  2
UNAVAILABLE: 11
TOTAL:       34
```

### B Input Readiness

```
B inputs: 16 total
  READY: 7
  UNAVAILABLE: 5
  INCOMPLETE: 2
  PROVISIONAL: 2
```

## Documented Gaps

| Artifact | Status | Reason |
|----------|--------|--------|
| sentinel2_dry_season_metadata | INCOMPLETE | No STAC catalog access for automated query |
| hanging_ice_support_grid | UNAVAILABLE | Depends on S2 dry-season metadata |
| sentinel1_winter_acquisition_table | INCOMPLETE | Winter-specific CMR queries not yet performed |
| sentinel1_compatible_pair_table | PROVISIONAL | Deferred until GLM-5.3 locked pair manifest |
| sentinel1_per_unit_observability | PROVISIONAL | Depends on compatible pair table |
| ghsl_built_up_surface | UNAVAILABLE | Needs subset extraction from global raster |
| hydrorivers_drainage | UNAVAILABLE | Asia dataset ~1.5GB, needs subset extraction |
| worldpop_population | UNAVAILABLE | Not yet acquired |
| era5_winter_b_screening | UNAVAILABLE | Winter ERA5 for B screening not yet acquired |
| era5_sd_snow_depth | UNAVAILABLE | Not in EDH daily store |
| era5_sf_snowfall | UNAVAILABLE | Not in EDH daily store |
| rgi60_to_rgi7_crosswalk | INCOMPLETE | RGI7 includes rgi6_links.csv but not processed |
| farinotti_rgi60_thickness | UNAVAILABLE | Needs RGI60-to-RGI7 crosswalk first |
| cnki_cas_tibet_bureau_search | UNAVAILABLE | No Chinese-language search access |
| permafrost_proxy | UNAVAILABLE | Low-confidence sidecar |
| thermal_layers_sidecar | UNAVAILABLE | Low-confidence sidecar |

## Files Created

```
data/framework_inputs_v1_reconciled/
├── manifest.json                          (34 artifacts, self-hashed)
├── dry_run_manifest.json                  (Stage 0 preflight)
├── handoff_gate_result.json               (Stage 9 gate result)
├── scripts/
│   ├── canonical_manifest.py              (registration & verification)
│   └── contract.py                        (contract constants)
├── tests_reconciled/
│   ├── conftest.py                        (pytest path config)
│   └── test_verification_matrix.py         (76 tests)
├── catalog/
│   ├── bashkova_rupper_db.xlsx
│   ├── hma_events_all.json
│   ├── hma_events_post2000.json
│   ├── locked_jja_events.json
│   ├── adjudication_ledger.json
│   └── language_coverage_log.json
├── era5/
│   └── paper0_context_jja.nc
├── dem/
│   ├── copernicus_glo30_n28e085.tif
│   ├── dem_300x300_100m_32645.tif          (NEW: 300x300 100m UTM 45N)
│   └── dem_coverage_mask_300x300.tif       (NEW: binary coverage mask)
├── glacier_inventory/
│   ├── rgi13_central_asia.{shp,dbf,shx,prj,cpg}
│   ├── rgi14_south_asia_west.{shp,dbf,shx,prj,cpg}  (.prj REPAIRED)
│   ├── rgi15_south_asia_east.{shp,dbf,shx,prj,cpg}  (.prj REPAIRED)
│   ├── rgi15_fixed_box_subset.shp          (NEW: 412 glaciers in study box)
│   └── focal_rgi_record.json              (NEW: RGI2000-v7.0-G-15-05732)
├── sentinel1/
│   ├── s1a_inventory_langtang.json
│   └── winter_acquisition_table.json
├── sentinel2/
│   └── dry_season_metadata.json
├── exposure/
│   └── nepal.osm.pbf
├── seismic/
│   └── fdsn_station_availability.txt
└── reference/
    └── (Hausfather references)
```

## Verification Matrix Results

```
76 tests passed in 6.58s

Unit & Contract Tests:
  ✓ Manifest self-hash valid
  ✓ Safe paths and symlink rejection
  ✓ Status taxonomy (5 statuses, 8 roles, 6 kinds)
  ✓ Frozen-file lock (feature_contract, preregistration)
  ✓ Mutable study-box regression
  ✓ Date conflicts and interval boundaries
  ✓ No provisional B inputs consumed
  ✓ Raw SLC absence
  ✓ Size limits (persistent <5GB, temp <8GB)

Real-Artifact Integration Tests:
  ✓ EPSG:4326 DEM → 300x300 EPSG:32645 grid
  ✓ No huge raster window
  ✓ RGI14/RGI15 CRS sanity (Transverse_Mercator removed)
  ✓ Focal RGI ID discovery (RGI2000-v7.0-G-15-05732)
  ✓ Manifest rejection of unavailable/provisional required inputs
  ✓ Actual catalog replay (14 locked events)

Fuzz/Property Tests:
  ✓ ISO/slash/ambiguous dates
  ✓ Path traversal and absolute paths
  ✓ Invalid statuses
  ✓ NaN/infinite arrays
  ✓ Wrong grid shapes
  ✓ Duplicate IDs
  ✓ Invalid intervals
```

## Write Scope Compliance

- **Wrote only to:** `data/framework_inputs_v1_reconciled/` and `data/framework_inputs_v1_reconciled_tmp/` (temp now empty)
- **Did not modify:** `preregistration.md`, existing `data/*.json`/`*.nc`, `nepal/`, `tests/`, `plots/`, `data/framework_inputs_v1/` (preserved)
- **No raw SLC stored**
- **No secrets printed or persisted**

## Pending Tasks

1. Acquire Sentinel-2 dry-season metadata via STAC catalog (Copernicus Data Space)
2. Generate hanging-ice support grid from S2 data
3. Query ASF CMR for winter-specific S1 windows
4. Process RGI60-to-RGI7 crosswalk from rgi6_links.csv
5. Acquire subsetted GHSL, HydroRIVERS, WorldPop
6. Acquire winter ERA5 for B screening
7. Wait for GLM-5.3 locked pair manifest before S1-B derived products
8. Obtain ASF authentication for HyP3 work
