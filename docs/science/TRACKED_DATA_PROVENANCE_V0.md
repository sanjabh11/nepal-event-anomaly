# Tracked Data Artifact Provenance — V0

**Status:** PROVENANCE_REVIEW_RECORD — recorded 2026-09-27; containment
executed 2026-09-27 (see §0).
**Boundary:** research-only repository; this record authorizes nothing.

## 0. Containment executed

On 2026-09-27 the five payload paths below were removed from **all
reachable git history** via `git filter-repo --invert-paths` (a
clean-history rewrite; every commit hash changed). A pre-rewrite backup
bundle is retained offline. Post-rewrite verification: zero reachable
objects reference the five paths; `git ls-files data/` is empty. The
rewrite was recorded in
`docs/science/MANIFEST_SCOPE_EXCLUSIONS_V0.json` under
`scrubbed_from_public_history`, and permanent ignore rules now block
re-tracking. **Caveat:** copies may persist in GitHub forks, caches,
or prior clones — that residual exposure cannot be contained by a
rewrite and is disclosed rather than claimed resolved.

This record is retained as the external provenance of record: source,
version, SHA-256, terms, and attribution for bytes that are **no
longer in the repository**. The per-artifact entries below preserve
their original `git ls-files`-verified digests from before the scrub.

## 1. Purpose and scope

This record documents the source, governing terms, attribution
obligation, and sensitivity of the five third-party data payload
artifacts that were tracked in the public repository under `data/`
before the containment scrub. It is a provenance and terms review
record only. It authorizes nothing: no acquisition, no redistribution
decision, and no scientific claim follows from it.

SHA-256 values below were verified against the on-disk files before
the scrub; they remain the canonical digests of the bytes that were
exposed and subsequently removed.

## 2. Tracked-vs-gitignored misalignment

Ignore rules never untrack already-committed files. Three of the five
artifacts match live `.gitignore` patterns yet remain tracked; the
other two match **no** ignore rule at all — the boundary gap is partly
"ignored-but-tracked" and partly "never-ignored". Matching confirmed
with `git check-ignore --no-index -v` (2026-09-27):

| artifact | first matching `.gitignore` rule | also matches | tracked? |
|---|---|---|---|
| `data/dem_n28e085.tif` | `.gitignore:38` `*.tif` | — | yes (since `36d6bf9`) |
| `data/temp/era5_land_2001_06.nc` | `.gitignore:20` `data/temp/` | `.gitignore:44` `*.nc` | yes (since `0f0c8a4`; restored `9b880cb`) |
| `data/download_ledger.json` | `.gitignore:21` `data/download_ledger.json` | — | yes (since `0f0c8a4`) |
| `data/era5_download_log.txt` | none | — | yes (since `36d6bf9`, initially empty) |
| `data/nisar_catalog_ledger.json` | none | — | yes (since `e9b2bef`) |

Notes on the misalignment:

- `data/edh_*_log.txt` (`.gitignore:52`) covers the later EDH logs but
  not `era5_download_log.txt`; no `*.txt` or `data/*_log.txt` rule
  exists.
- A broader frozen-boundary cleanup already ran: `9b880cb`
  (2026-09-27, "fix(ci): restore frozen data/ boundary to baseline
  5ef43c2") removed merge-WIP `data/` additions — reconciled-input
  manifests, result JSONs, feature CSVs, EDH logs — while *retaining*
  the five baseline artifacts recorded here. The retention is a
  boundary-baseline decision, not a terms clearance.
- Any removal-by-history-rewrite is explicitly deferred to an owner
  decision (§4). `git rm --cached` would untrack going forward but
  leaves history bytes public; rewrite is the only mechanism that
  removes them and is not authorized by this record.

## 3. Per-artifact records

### 3.1 `data/dem_n28e085.tif`

- **path:** `data/dem_n28e085.tif`
- **size_bytes:** 38,402,839
- **sha256:**
  `1590255a0ae7e8c1f49b277e287032a18a2e32c8e13c4c3298ed458f851cd3c7`
- **source/provider:** Copernicus DEM GLO-30, tile
  `Copernicus_DSM_COG_10_N28_00_E085_00_DEM` — surface model (DSM),
  1 arc-second (~30 m), delivered via the AWS Registry of Open Data
  mirror bucket `copernicus-dem-30m.s3.eu-central-1.amazonaws.com`
  (URL recorded in the artifact manifest entry; see below). Underlying
  producer: ESA / Airbus Defence and Space / DLR.
- **acquisition mechanism:** HTTPS fetch of the COG object from the S3
  mirror; recorded `acquired_at` 2026-09-10T13:47:00Z. The acquisition
  record lives in `data/framework_inputs_v1/manifest.json`, entry
  `artifact_id: copernicus_dem_glo30_n28e085` (`source_url` =
  `.../Copernicus_DSM_COG_10_N28_00_E085_00_DEM.tif`,
  `processing` = "Downloaded from Copernicus S3, GLO-30 (30m
  resolution), tile N28 E085", `crs` = EPSG:4326, `units` = meters,
  `bytes` = 38,402,839, `sha256` identical to the tracked file). That
  manifest is itself **untracked** at HEAD (gitignored as
  `data/framework_inputs_v1/`); it survives on disk and in history at
  commit `51dd2e1` — recover via
  `git show 51dd2e1:data/framework_inputs_v1/manifest.json`.
- **introduced commit:** `36d6bf9` (2026-09-10, "Phase 2-5 code: All
  analysis scripts + tests (42 passing)") — the payload was bundled
  into a code commit; the commit message carries no retrieval note.
- **local verification:** TIFF little-endian, 3600×3600 px, 32-bit
  samples, deflate — consistent with a GLO-30 COG tile and with the
  "3600x3600 float32 EPSG:4326" contract in `nepal/phase1_exit_gate.py`
  and `.phase-loop/glm2_download_plan.md`.
- **governing terms:** recorded in-repo only as the string "Copernicus
  License" (`preregistration.md` §9; framework manifest). Copernicus
  DEM is distributed under ESA/Copernicus terms requiring attribution;
  no verbatim license text is vendored in the repository, so the exact
  clause set is recorded here by reference only. The repository's own
  distribution policy (`docs/science/OPEN_DISTRIBUTION_NOTE_V0.md` §2)
  states raw third-party payload bytes "stay external — never vendored
  or republished" and that `data/` is not a distribution surface —
  standing in direct tension with the tracked state of this file.
- **attribution requirement:** Copernicus DEM attribution per provider
  terms (dataset produced under Copernicus; credit ESA/Airbus/DLR as
  specified in the GLO-30 license). No in-repo NOTICE file carries the
  attribution string.
- **sensitivity:** elevation raster over N28E085 (Langtang region);
  no personal data, no credentials, no security-relevant content.
  Risk class is third-party licensed bytes at scale (36.6 MB).
- **consuming code:** `nepal/phase1_exit_gate.py` `check_dem()`
  (existence + TIFF magic + size only); declared a required `geotiff`
  input ("Copernicus DEM surface") in
  `nepal/framework_v1/contract.py` (dem entry); referenced as the
  source for the derived `dem_300x300_100m_32645` grid in
  `.phase-loop/glm2_reconciled_download_plan.md`; lapse-rate use
  planned in `DEEP_RESEARCH_ADDENDUM.md`. No raster reader of this
  file exists in tracked `nepal/*.py`.
- **disposition:** `REDISTRIBUTION_REVIEW_REQUIRED`

### 3.2 `data/temp/era5_land_2001_06.nc`

- **path:** `data/temp/era5_land_2001_06.nc`
- **size_bytes:** 2,079,221
- **sha256:**
  `9dbaaf7bd6cbfb6b230cabfcd6e5c5cf75f040e205a6bb4e87647a78bd6d490d`
- **source/provider:** ECMWF Copernicus Climate Data Store (CDS),
  dataset `reanalysis-era5-land`, monthly request for 2001-06 —
  CDS-delivered object `41aafccc5916a8d6bfa7c20b576390cf.zip`
  (zip name visible in `data/era5_download_log.txt`).
- **payload reality:** despite the `.nc` name the file is a ZIP
  archive (PK magic; first member `data_0.nc`, HDF5/NetCDF-4) — the
  raw CDS delivery before normalization. This is the only month ever
  completed of the planned 78 monthly JJA 2001–2026 requests: the
  ledger never advanced past one `completed_months` entry and the log
  ends after the first download.
- **staleness flag (scientific):** the request used the variable name
  `snow_depth`, which delivers `sde` (geometric depth) — the
  pre-correction variable the current contract forbids. The committed
  test `tests/test_p5_io_contract.py`
  (`test_real_legacy_payload_is_zip_with_sde`) codifies this: "the
  committed June-2001 payload is a ZIP carrying sde — the corrected
  request must be re-run; it cannot be normalized." The file is a
  forensic/legacy artifact, not a usable input.
- **acquisition mechanism:** produced by `nepal/era5_download.py` as it
  existed at commit `0f0c8a4`, which wrote `data/temp/
  era5_land_{year}_{month}.nc` plus `data/download_ledger.json`
  directly into the `data/` tree (CDS API via `~/.cdsapirc`; request
  started 2026-09-10T13:47:29 per the ledger). The HEAD revision of
  the same script was later rewritten to write only under
  `--run-root` (`research_runs/…`) and documents that "the frozen
  `data/` tree is never written" — the tracked payloads predate that
  correction. `pinned/acquire_era5_land.py` is a different project's
  multi-winter acquirer and is not the producer of this file.
- **introduced commit:** `0f0c8a4` (2026-09-10, "Fix 10 gaps from
  ecc-advisor cross-verification"). Dropped by merge-WIP `51dd2e1`
  (2026-09-27) and re-added by `9b880cb` (2026-09-27) to restore the
  frozen `data/` boundary to baseline `5ef43c2`.
- **governing terms:** License to Use Copernicus Products (CDS terms;
  recorded in-repo as "copernicus licence (CC BY 4.0 attribution)" in
  `docs/science/P5_ACQUISITION_AUTHORIZATION_RECORD_V0.md` P5-C;
  `preregistration.md` §9 lists "Copernicus License"). The same
  `OPEN_DISTRIBUTION_NOTE_V0.md` §2 conflict applies — ERA5-Land-class
  bytes are explicitly named as "stay external" content.
- **attribution requirement:** Copernicus attribution per the License
  to Use Copernicus Products ("generated using Copernicus Climate
  Change Service information" class of statement) if retained/cited.
- **sensitivity:** reanalysis meteorological fields over
  [29N,85E]–[27N,86E]; no personal data or credentials. Wrong-variable
  staleness means its only value is as evidence of the request, not of
  the field.
- **consuming code:** none for science. Only the contract test above
  opens it (skips if absent). Governed feature extraction reads
  run-root monthly/merged files, never `data/temp/`.
- **disposition:** `REDISTRIBUTION_REVIEW_REQUIRED`

### 3.3 `data/download_ledger.json`

- **path:** `data/download_ledger.json`
- **size_bytes:** ~0.9 KB (924 bytes on disk)
- **sha256:**
  `66d7b459373982f49d3ac1a766149cded270df1067990b236b0bc1d343cbe4a0`
- **source/provider:** self-generated by this repository's own tooling
  — `nepal/era5_download.py` (the `0f0c8a4`-era revision writing into
  `data/`). No third-party payload bytes; the file is a retrieval
  record.
- **content:** `start_time` 2026-09-10T13:47:29; `dataset`
  `reanalysis-era5-land`; 7 variables (including the pre-correction
  `snow_depth`); `area` [29.0, 85.0, 27.0, 86.0]; `event_cell`
  [28.25, 85.5]; years 2001–2026, months 06/07/08; one completed
  month (2001-06, 2.0 MB); run left at its "started" marker with
  `total_size_mb` ≈ 1.98.
- **internal inconsistency (recorded, not adjudicated):** ledger `area`
  is a 2°×1° box while the same-era log claims "1° × 1°" and the
  current contract enforces `[29.0, 85.0, 28.0, 86.0]` (1°×1°,
  `assert_area_contract()`). The discrepancy belongs to the pre-
  correction download path and is preserved as forensic evidence.
- **introduced commit:** `0f0c8a4`; modified by merge-WIP `51dd2e1`;
  restored to baseline content by `9b880cb`.
- **governing terms:** repository-generated record; no third-party
  license attaches to the JSON itself.
- **attribution requirement:** none beyond normal repository citation.
- **sensitivity:** request parameters and timing only; no credentials
  (`.cdsapirc` is gitignored and absent). Contains no secrets.
- **consuming code:** none for the `data/`-root copy. Governed tooling
  reads per-run ledgers at `run_root/download_ledger.json`
  (`nepal/feature_extraction.py`, `nepal/run_ledger_repair.py`); this
  tracked file is a Phase-1 leftover, not the run ledger.
- **disposition:** `DOCUMENTED_PROJECT_GENERATED`

### 3.4 `data/era5_download_log.txt`

- **path:** `data/era5_download_log.txt`
- **size_bytes:** ~0.7 KB (681 bytes on disk)
- **sha256:**
  `92bbaee423497333b398b6ac746a6368bf5c5db00231708e4d1003d130c70176`
- **source/provider:** self-generated stdout capture of the same CDS
  download run — "Nepal Event Anomaly — ERA5-Land Download",
  years 2001–2026, JJA months, 7 variables, event cell
  (28.25°N, 85.5°E), then CDS client init and the single
  `Requesting 2001-06 (attempt 1/3)` line with the zip progress bar.
- **introduced commit:** `36d6bf9` (2026-09-10) — committed as an
  **empty** file; content added in `0f0c8a4`; rewritten under
  merge-WIP `51dd2e1`; restored to baseline content by `9b880cb`.
- **governing terms:** repository-generated log; quotes CDS progress
  output (request zip object name) but contains no third-party data
  payload.
- **attribution requirement:** none.
- **sensitivity:** contains no credentials; confirms the CDS account
  existed but exposes no key material.
- **consuming code:** none — no tracked file references this log.
- **disposition:** `DOCUMENTED_PROJECT_GENERATED`

### 3.5 `data/nisar_catalog_ledger.json`

- **path:** `data/nisar_catalog_ledger.json`
- **size_bytes:** ~18 KB (18,379 bytes on disk)
- **sha256:**
  `bafc4c11a5bb960112804786c74fb7a9a7a0e51732e6d232c1ec4609631aa9bd`
- **source/provider:** self-generated NASA CMR catalog **query
  metadata** — `nepal/nisar_catalog_check.py` queries
  `https://cmr.earthdata.nasa.gov/search/granules.json` (no auth) for
  four ASF-hosted NISAR collections: GUNW provisional
  `C2854335566-ASF`, GOFF provisional `C2854341702-ASF`, GUNW beta
  `C2850261892-ASF`, GOFF beta `C2850263910-ASF`, at source point
  (28.28858, 85.52701), period 2025-07-30 → 2026-08-26T02:52:00Z.
- **content:** `query_time` 2026-09-10T13:38:02; 28 pre-event and 4
  post-event granule metadata records (producer granule IDs,
  acquisition windows, catalog update timestamps, derived publication-
  latency and lead-time fields such as `warning_lead_hours`); a
  `publication_latency_findings` note on acquisition-vs-publication
  timing. **No NISAR imagery/payload bytes** — catalog metadata only.
- **introduced commit:** `e9b2bef` (Phase 0 init, 2026-09-10
  13:38:17 +0530 — committed ~15 seconds after the recorded query
  time). A byte-identical copy (same sha256) existed under
  `data/framework_inputs_v1/nisar/` in merge-WIP `51dd2e1`; that copy
  was untracked by `9b880cb` along with the rest of
  `framework_inputs_v1/`.
- **governing terms:** recorded as "NASA Open Data" in the framework
  manifest entry (`nisar_catalog_ledger`, `source_url`
  `https://cmr.earthdata.nasa.gov/`, `processing` "CMR catalog query,
  metadata only (no imagery downloaded)"). NASA/ASF catalog metadata
  is publicly distributed; no redistribution restriction on metadata
  was observed in-repo.
- **attribution requirement:** cite NASA Alaska Satellite Facility
  CMR as the metadata source if the ledger is cited.
- **sensitivity:** granule identifiers and timestamps only; public
  catalog content; non-sensitive.
- **consuming code:** `nepal/phase1_exit_gate.py` `check_nisar_ledger()`
  (pre/post granule counts, latency-trap count);
  `nepal/run_nepal_test.py` loads it into the result bundle;
  regenerated by `nepal/nisar_catalog_check.py` (`OUTPUT_FILE` =
  `data/nisar_catalog_ledger.json`).
- **disposition:** `DOCUMENTED_PROJECT_GENERATED`

## 4. Decisions required of the owner

Enumerated; none is decided by this record.

- **(a) DEM and ERA5 bytes — re-fetch or retain.** Either replace the
  tracked Copernicus DEM tile and the stale ERA5-Land June-2001 zip
  with re-fetch instructions (both canonical endpoints are recorded in
  §3: the AWS `copernicus-dem-30m` S3 object for the DEM; CDS
  `reanalysis-era5-land` for the .nc — noting the corrected
  `snow_depth_water_equivalent` variable), or explicitly accept
  attribution-governed retention under the provider terms cited.
- **(b) Untrack going forward.** ~~Decide whether to
  `git rm --cached` the five paths~~ **Superseded by §0:** the
  clean-history rewrite removed all five paths from every reachable
  commit; permanent `.gitignore` rules now block re-tracking.
- **(c) History rewrite.** ~~The bytes remain in commit history and on
  `origin` regardless of (b); removing them requires a rewrite decision
  this record does not make.~~ **Executed per §0** under explicit owner
  decision — all historical copies were stripped; residual copies on
  `origin` prior to the force-update and in external forks/caches are
  disclosed, not claimed resolved.
- **(d) Standing rule.** No NEW third-party payload bytes may be
  committed to this repository until the candidate artifact is cleared
  through the review recorded here (source, terms, attribution,
  sensitivity documented before the commit, not after).

## 5. Authority

```json
{
  "bulk_acquisition_authorized": false,
  "weather_download_authorized": false,
  "satellite_bulk_authorized": false,
  "seismic_waveform_authorized": false,
  "forecast_authorized": false,
  "warning_authorized": false,
  "detector_authorized": false,
  "odds_authorized": false,
  "causal_authorized": false,
  "operational_authorized": false
}
```

## Appendix — verification commands used

- `git ls-files data/` — tracked inventory (five paths pre-scrub;
  empty post-scrub, verified).
- `git check-ignore --no-index -v <path>` — ignore-rule matching per
  §2.
- `git log --follow --format='%h %ad %s' --date=short -- <path>` and
  `git log --diff-filter=A -- <path>` — introduction and mutation
  commits per §3.
- `git show 51dd2e1:data/framework_inputs_v1/manifest.json` — the
  untracked acquisition register carrying DEM/NISAR `source_url`,
  `acquired_at`, `license`, and digest fields.
- `file data/temp/era5_land_2001_06.nc` + `xxd` — ZIP magic and
  `data_0.nc` member (§3.2).
- `file data/dem_n28e085.tif` — TIFF geometry summary (§3.1).
