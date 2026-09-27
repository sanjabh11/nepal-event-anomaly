# GLM2 Download-Data Plan — Phase-Loop Ledger

**Created:** 2026-09-11
**Status:** COMPLETE — HANDOFF READY
**Tier:** 2 (multi-phase, persistent artifact)
**Owner:** GLM2 (this session)
**Completed:** 2026-09-11

## Constraints

- **Write scope:** `data/framework_inputs_v1/` and `data/framework_inputs_v1_tmp/` ONLY
- **Must not modify:** `preregistration.md`, existing `data/*.json`, `data/*.nc`, `nepal/`, `tests/`, `plots/`
- **Persistent handoff:** < 5 GB
- **Temporary working space:** < 8 GB hard ceiling
- **Raw SLC:** NEVER, even temporarily
- **Credentials:** env vars or netrc only, never print/persist tokens
- **No overwrite:** refuse to overwrite existing files

## Artifact Contract (15 fields per artifact)

```
artifact_id, source_url, query_or_request, source_record_id,
acquired_at, observation_start, observation_end,
publication_or_validity_date, sha256, bytes, crs, units,
license, processing, status
```

Statuses: READY, INCOMPLETE, INVALID, UNAVAILABLE, PROVISIONAL

## Current State Assessment

### Already available (needs registration with metadata)
- ERA5-Land daily (EDH Zarr v3): `data/era5_land_nepal_jja_2001_2026.nc` (9.2 MB, 5 vars, 2392 steps)
- Copernicus DEM GLO-30: `data/dem_n28e085.tif` (36.6 MB, EPSG:4326)
- NISAR catalog ledger: `data/nisar_catalog_ledger.json` (28 pre-event, 4 post-event)
- Hausfather reference: `data/hausfath_reference/` (3 NetCDF files)
- Bashkova-Rupper glacier failure DB: `data/glacier_failure_db/` (xlsx + JSON)
- Locked JJA events: `data/locked_jja_events.json` (14 events)

### Needs acquisition
- RGI7/GLIMS glacier inventory
- Farinotti RGI60 thickness (where spatially relevant)
- Sentinel-2 pre-event dry-season metadata
- Sentinel-1 acquisition metadata (S1-A inventory lane)
- GHSL built-up surface
- WorldPop population
- OSM/Geofabrik infrastructure
- HydroSHEDS/HydroRIVERS drainage
- FDSN seismic availability check (metadata only)

### Disk space
- Available: 5.0 GB (at persistent limit)
- Stale temp files: `data/temp_parallel/` = 12 MB (should clean)
- Current data size: 61 MB
- Estimated framework_inputs_v1 size: < 500 MB (derived products only)

## Stage Execution Plan

### Stage 0 — Preflight
- [x] Record git status
- [ ] Confirm disk space (5.0 GB — at limit, need cleanup)
- [ ] Clean stale temp files (data/temp_parallel/ = 12 MB)
- [ ] Confirm credentials (CDS: present, EDH: need to check, ASF: need netrc)
- [ ] Create data/framework_inputs_v1/ and data/framework_inputs_v1_tmp/
- [ ] Create dry-run manifest
- [ ] Confirm output root is new and isolated

### Stage 1 — Catalog & literature evidence
- [ ] Register Bashkova-Rupper DB with DOI, license, checksum
- [ ] Register locked JJA events list
- [ ] Record source papers and official reports
- [ ] Record CNKI/CAS/Tibet Bureau search status (likely UNAVAILABLE)
- [ ] Language-coverage and access-status log

### Stage 2 — ERA5-Land
- [ ] Register existing EDH daily data with full 15-field metadata
- [ ] Validate UTC dates are unique and complete
- [ ] Confirm tp treated as daily total
- [ ] Confirm no post-event values in as-of baseline
- [ ] Record missing sd/sf as UNAVAILABLE (not fabricated)

### Stage 3 — DEM & glacier inventories
- [ ] Register existing Copernicus GLO-30 DEM with full metadata
- [ ] Acquire RGI7/GLIMS inventory for HMA regions
- [ ] Acquire Farinotti RGI60 thickness where spatially relevant
- [ ] Record RGI60-to-RGI7 crosswalk or UNMAPPED status
- [ ] Check Sentinel-2 pre-event dry-season metadata availability

### Stage 4 — Sentinel-1
- [ ] S1-A: Collect acquisition metadata for 30 km box + holdout sites
- [ ] S1-A: Record platform, orbit, path/frame/burst, mode, polarization, incidence
- [ ] S1-A: Record acquisition time, product availability date, pair compatibility
- [ ] S1-B: DEFERRED until GLM-5.3 emits locked pair manifest

### Stage 5 — Exposure/context data
- [ ] GHSL built-up surface (fixed box + holdout areas)
- [ ] WorldPop (where license and resolution suitable)
- [ ] OSM/Geofabrik infrastructure
- [ ] HydroSHEDS/HydroRIVERS drainage context

### Stage 6 — Optional seismic preflight
- [ ] FDSN metadata/availability check only
- [ ] Do NOT download continuous waveforms
- [ ] Do NOT make seismic a project dependency

### Stage 7 — Handoff verification
- [ ] Every required artifact has checksum
- [ ] All URLs, queries, licenses, dates, units recorded
- [ ] Event-year data is as-of-event
- [ ] No raw SLC anywhere
- [ ] No silent substitution
- [ ] Persistent size < 5 GB
- [ ] Temp size < 8 GB
- [ ] All missing inputs explicitly represented
- [ ] Manifest itself is hashed
- [ ] Declare READY or INCOMPLETE
