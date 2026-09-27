# A1 Source-Qualification Deltas — v0 (Swarm 1)

**Status:** `DESIGN_DRAFT_COMPLETE` — metadata deltas only; no intake,
no raw payloads downloaded for qualification purposes in this lane.
**Lane:** Swarm 1 / A1 (`swarm1/scientific`, base `52bd564`).
**Baseline:** `run_b/SOURCE_QUALIFICATION_RECORDS_V0.md` +
`SOURCE_EVIDENCE_ADDENDUM_V0.md` (33 records). This file records only
**deltas** — fields that advanced beyond the run_b baseline during the
Swarm-1 session. No record is re-qualified here; `NO_QUALIFYING_PILOT_SOURCE`
stands.

## Delta records

### `cds_reanalysis_era5_land` (meteorological reanalysis — descriptive only)

| field | delta |
|---|---|
| exact_version | `reanalysis-era5-land` (MARS) + `reanalysis-era5-land-timeseries` (ARCO copy) |
| license_evidence_delta | CC-BY 4.0 confirmed for both CDS datasets (dataset landing pages) |
| timing_class | `REANALYSIS` — never admissible as forecast-skill data |
| observation_opportunity | hourly, 0.1°, 1950–present (ARCO copy: point time-series, whole span in one request, seconds-scale response) |
| non_event_feasibility | full — every hour is retrievable, no event-conditioned sampling |
| decision_delta | **qualification delta for the descriptive lane only**: ARCO timeseries demonstrated end-to-end (Run A hybrid: t2m, d2m, u10, v10, tp for cell (28.3, 85.5), 2001-01-01→2026-08-25, ~9.5 MB in ~30 s); `sd`/`sf` absent from ARCO stores — snow/precip ARCO collections carry `sde`, `snowc`, `sp`, `tp` only (verified against ECMWF ARCO Zarr group tables) |
| blocker | none for descriptive-regime use; remains excluded from forecast-skill roles |
| confidence | high — verified by executed retrieval, not documentation alone |
| evidence | executed request `location={lat:28.3,lon:85.5}`, `date=2001-01-01/2026-08-25`, NetCDF zip (3 var-group members); ECMWF jobs API QoS: `reanalysis-era5-land` = 1 running + 3 queued per user |

### `earth_data_hub_era5_land_zarr` (new source record — DesinE mirror)

| field | delta |
|---|---|
| source_id | `earth_data_hub_era5_land_zarr` (DestinE Earth Data Hub) |
| exact_version | ERA5-Land Zarr mirror; API key required (free registration) |
| license_evidence_delta | inherits ERA5-Land CC-BY 4.0 upstream; EDH platform terms not yet recorded — re-verify at intake |
| nepal_coverage_delta | verified: returned `sd` + `sf` for cell (28.3, 85.5), 2001→2026-05-31 (last closed month) — all finite |
| timing_class | `REANALYSIS` |
| observation_opportunity | xarray direct-read, no queue; coverage ends at last closed month (2026-05) — trailing months still require MARS |
| non_event_feasibility | full |
| decision_delta | `CANDIDATE` → demonstrated-viable for snow variables in the descriptive lane (Run A used it for 2001–2025 sd/sf); NOT an event source |
| blocker | platform terms + version pinning not yet captured |
| confidence | high on availability, medium on license pinning |

### `cds_mars_era5_land` (queue semantics delta)

| field | delta |
|---|---|
| decision_delta | throughput constraint recorded: server enforces ~1 year per request for `reanalysis-era5-land` (5-yr and 2-yr chunks rejected with `cost limits exceeded` even at point area); per-user QoS 1 running + 3 queued — request-level parallelism provides no speedup |
| blocker | none; constraint is now quantified |

## Candidates unchanged this lane

- `hiaval_v1_3_0`, `icimod_hmaglofdb_v1_3_0`, `essd_2026_481`,
  `jiang_essd_2026_107`, `borealis_dam_failure_db`, `kneib_s1`,
  `safe_hma`, `burrows`, `jones_2021`, `nasa_coolr`, `usgs_gorkha_2015`,
  `usgs_ofr_91_239` — no new evidence; blockers in the baseline stand
  (Nepal subset counts, license text, peer-review status).
- `dam_breach_engineered` — `DEFERRED_NO_OPEN_TIMED_SOURCE` **stands**;
  no authoritative open timed source located this session.
- `gefsv12_reforecast_aws` (run_c) — remains nearest-passing archive;
  intake still gated on archive-completeness evidence.

## What this lane did not do

No payload downloads for qualification, no decision escalation, no
pilot nomination, no license assertion beyond CC-BY pages verified or
retrievals actually executed.
