# Source Evidence Addendum — v0 (Run B)

**Status:** `DESIGN_DRAFT_COMPLETE` — metadata evidence only.
**Lane:** Run B (`run-b/source-qual`, base `f4e5cbe`).
**Companion record:** `SOURCE_QUALIFICATION_RECORDS_V0.md` — this
addendum binds license/redistribution metadata, versions, and retrieval
timestamps to the `source_id` values declared there. It changes no
qualification decision and authorizes nothing: no download, no intake,
no FMX freeze, no clustering.

## Method bound

Only public metadata endpoints and landing pages were queried: Zenodo
REST (`/api/records`), Figshare REST (`/v2/articles`), Dataverse native
API, ScienceBase catalog JSON, ICIMOD RDS landing pages, and provider
licence/landing pages. **No dataset files, file payloads, or data
downloads were retrieved.** Every response captured was metadata JSON
or landing-page HTML (each well under ~150 KB). `retrieval_utc` values
are real fetch timestamps recorded at query time (2026-09-15 UTC).
Anything that requires opening a data file — Nepal subset counts,
per-record schema checks, content verification — remains intake-gated
and was NOT performed.

## Resolution vocabulary

- `RESOLVED` — endpoint reachable; the license/version question is
  answered by retrieved metadata, or the retrieved metadata shows the
  answer is absent-on-record (recorded explicitly).
- `CONFLICT_REMAINS` — two retrieved sources assert materially
  different terms and no governing rule was located.
- `UNREACHABLE` — endpoint failed after retries; no value guessed.
- `NOT_FOUND` — endpoint reachable but the record does not exist.

## Snow avalanche vertical

```yaml
- source_id: hiaval_v1_3_0
  api_endpoint: "https://zenodo.org/api/records/18257425 (+ concept-version list via /api/records?q=conceptdoi:10.5281/zenodo.7066940&all_versions=true)"
  retrieval_utc: "2026-09-15T06:22:51Z (record); 2026-09-15T06:23:40Z (version list)"
  license_tag_retrieved: "cc-zero (record-level tag on v1.3.0)"
  license_url: "https://creativecommons.org/publicdomain/zero/1.0/ (inferred from cc-zero tag)"
  version_publication_date: "v1.3.0; 2026-01-15; concept DOI 10.5281/zenodo.7066940"
  resolution: RESOLVED
  notes: "CONFLICT RESOLVED as a cross-version tag change, not a single-record contradiction. Version history retrieved: 7066941 (2022-09-10)=other-open; 8157891 v1.0.0 (2023-07-18)=other-open; 10453155 v1.1.0 (2024-01-03)=cc-by-4.0; 14602271 v1.2.0 (2025-01-05)=cc-zero; 18257425 v1.3.0 (2026-01-15)=cc-zero. The CC BY 4.0 trace in the qualification doc was the v1.1.0 tag and/or the NHESS article licence (Copernicus journals are CC BY). Current v1.3.0 data record is CC0-1.0. Conservative governing term for the family if any doubt persists: CC BY 4.0 attribution — a superset obligation of CC0."
  remaining_blockers: "Nepal subset count = intake-gated, requires data download — NOT performed; GitHub LICENSE and GitHub README are the third/fourth licence surfaces and were NOT re-fetched (metadata-only bound) — the conservative governing read remains RDS CC BY 4.0"

- source_id: kneib_s1_everest_deposits
  api_endpoint: "https://zenodo.org/api/records/10895011"
  retrieval_utc: "2026-09-15T06:22:50Z"
  license_tag_retrieved: "cc-by-4.0"
  license_url: "https://creativecommons.org/licenses/by/4.0/"
  version_publication_date: "2024-03-29; record DOI 10.5281/zenodo.10895011; concept DOI 10.5281/zenodo.10895010; access_right=open"
  resolution: RESOLVED
  notes: "License tag now confirmed on record: CC BY 4.0. Lifts the named 'Zenodo license tag unconfirmed' evidence item."
  remaining_blockers: "Nepal/China extent split and deposit count = intake-gated — NOT performed"

- source_id: safe_hma_annual_deposits
  api_endpoint: "https://api.figshare.com/v2/articles/28869170"
  retrieval_utc: "2026-09-15T06:24:01Z"
  license_tag_retrieved: "CC BY 4.0 (figshare license value=1)"
  license_url: "https://creativecommons.org/licenses/by/4.0/ (returned in license.url)"
  version_publication_date: "v3; published 2026-02-19; DOI 10.6084/m9.figshare.28869170.v3; title 'SAFE-HMA per basin'"
  resolution: RESOLVED
  notes: "License confirmed CC BY 4.0 on the v3 record — consistent with the qualification doc."
  remaining_blockers: "Year-class timing blocker unchanged (descriptive role only); Nepal tile check = intake-gated — NOT performed"

- source_id: avalcd_tajikistan
  api_endpoint: "https://zenodo.org/api/records/15863589"
  retrieval_utc: "2026-09-15T06:23:16Z"
  license_tag_retrieved: "cc-by-nc-4.0"
  license_url: "https://creativecommons.org/licenses/by-nc/4.0/"
  version_publication_date: "publication_date '2026'; record DOI 10.5281/zenodo.15863589; access_right=open"
  resolution: RESOLVED
  notes: "License confirmed CC BY-NC 4.0 — matches qualification doc. BLOCKED decision stands on zero Nepal coverage, independent of license."
  remaining_blockers: "none new — zero Nepal coverage is intrinsic"
```

## GLOF vertical

```yaml
- source_id: icimod_hmaglofdb_v1_3_0
  api_endpoint: "https://zenodo.org/api/records/18257243 ; https://rds.icimod.org/Home/DataDetail?metadataId=1973283 (via DOI 10.26066/RDS.1973283) ; Zenodo version list via conceptdoi 10.5281/zenodo.7271187"
  retrieval_utc: "2026-09-15T06:22:52Z (zenodo record); 2026-09-15T06:23:40Z (version list); 2026-09-15T06:25:08Z (RDS page)"
  license_tag_retrieved: "Zenodo v1.3.0 record: cc-zero. ICIMOD RDS landing (metadataId 1973283): 'Creative Commons Attribution 4.0 International (CC BY 4.0)'"
  license_url: "https://creativecommons.org/publicdomain/zero/1.0/ (Zenodo tag); https://creativecommons.org/licenses/by/4.0/ (RDS)"
  version_publication_date: "v1.3.0; 2026-01-15 (Zenodo); RDS page title 'GLOF database of High Mountain Asia'"
  resolution: RESOLVED
  notes: "CONFLICT RESOLVED as dual-mirror license divergence plus a cross-version tag change — the same pattern as HiAVAL (shared maintainer). Zenodo version history: 7271188 v1.0.0 (2022-11-01)=other-open; 7965890 v1.0.1 (2023-05-24)=other-open; 10453149 v1.1.0 (2024-01-03)=cc-by-4.0; 14601799 v1.2.0 (2025-01-05)=cc-zero; 18257243 v1.3.0 (2026-01-15)=cc-zero. The canonical ICIMOD RDS copy is licensed CC BY 4.0; the Zenodo mirror is tagged CC0. Both are permissive; governing term for redistribution = CC BY 4.0 (the more restrictive of the two and the publisher-canonical copy)."
  remaining_blockers: "v1.3.0 Nepal event count and lake-ID join coverage = intake-gated — NOT performed"
```

## Ice/rock avalanche and glacier-failure vertical

```yaml
- source_id: essd_2026_481_glacier_failure_db
  api_endpoint: "https://zenodo.org/api/records/19477907 ; https://zenodo.org/api/records/19477908"
  retrieval_utc: "2026-09-15T06:23:15Z / 06:23:28Z (19477907, 302 redirect); 2026-09-15T06:23:16Z (19477908)"
  license_tag_retrieved: "cc-by-4.0"
  license_url: "https://creativecommons.org/licenses/by/4.0/"
  version_publication_date: "v1.0-subm; 2026-04-09; title 'Global database of glacier failures (1900-2025)'"
  resolution: RESOLVED
  notes: "CANONICAL DOI RESOLVED: 19477907 is the all-versions concept DOI — the API issues HTTP 302 to record 19477908. Versioned record 10.5281/zenodo.19477908 is the only published version (v1.0-subm), license cc-by-4.0, access_right=open. Cite concept DOI 10.5281/zenodo.19477907 for all-versions reference; cite 10.5281/zenodo.19477908 for the pinned v1.0-subm artifact. License field now confirmed — matches ESSD CC BY expectation."
  remaining_blockers: "discussion-preprint peer-review status unchanged; Nepal subset count = intake-gated — NOT performed"

- source_id: zhong_2024_ria_inventory
  api_endpoint: "https://zenodo.org/api/records/10080068"
  retrieval_utc: "2026-09-15T06:23:13Z"
  license_tag_retrieved: "cc-by-4.0"
  license_url: "https://creativecommons.org/licenses/by/4.0/"
  version_publication_date: "2023-11-07; record DOI 10.5281/zenodo.10080068; title 'Large rock and ice avalanches in High Mountain Asia'"
  resolution: RESOLVED
  notes: "License tag retrieved: CC BY 4.0. MATERIAL NEW FINDING: access_right='restricted' — the record's files are access-request-gated despite the open license tag on metadata. License and access are distinct: the CC BY 4.0 tag governs use of obtained data, but file retrieval requires an access grant. Redistribution posture is effectively request-gated until access is granted."
  remaining_blockers: "restricted file access (external grant required); Nepal subset count = intake-gated — NOT performed"
```

## Landslide (rainfall) vertical

```yaml
- source_id: burrows_timed_monsoon_landslides
  api_endpoint: "https://zenodo.org/api/records/7970874"
  retrieval_utc: "2026-09-15T06:22:53Z"
  license_tag_retrieved: "cc-by-4.0"
  license_url: "https://creativecommons.org/licenses/by/4.0/"
  version_publication_date: "2023-05-25; record DOI 10.5281/zenodo.7970874; concept DOI 10.5281/zenodo.7970873; access_right=open"
  resolution: RESOLVED
  notes: "License confirmed CC BY 4.0 — lifts the 'expected CC BY, unverified' evidence item."
  remaining_blockers: "70% untimed censoring rule and horizon floor unchanged; per-record slide timing = intake-gated — NOT performed"

- source_id: icimod_rds_landslide_sets
  api_endpoint: "https://rds.icimod.org/Home/DataDetail?metadataId=34425 ; ?metadataId=34426 (via DOIs 10.26066/RDS.34425, 10.26066/RDS.34426)"
  retrieval_utc: "2026-09-15T06:25:08Z"
  license_tag_retrieved: "'Creative Commons Attribution 4.0 International (CC BY 4.0)' on both records"
  license_url: "https://creativecommons.org/licenses/by/4.0/"
  version_publication_date: "landing pages only (no version tag exposed); titles: 'Landslide data of Koshi basin (within Nepal) of 1990...' and '...of 2010 developed through remote sensing approach'"
  resolution: RESOLVED
  notes: "CC BY 4.0 confirmed on both Koshi records — consistent with the qualification doc's 'verified on Koshi records'."
  remaining_blockers: "epochal timing and partial-coverage blockers unchanged"

- source_id: nasa_coolr_hma_ls_v002
  api_endpoint: "https://nsidc.org/data/hma_ls_cat/versions/2 (landing; Earthdata catalog id NSIDC-CPRD-HMA-LS-CAT-2)"
  retrieval_utc: "2026-09-15T06:27:22Z"
  license_tag_retrieved: "no formal license class exposed; usage condition text: 'As a condition of using these data, you must cite the use of this data set.'"
  license_url: "https://nsidc.org/data/hma_ls_cat/versions/2 ; DOI 10.5067/E8U4F9M2NCCN"
  version_publication_date: "V002 (2023, Kirschbaum & Stanley); catalog states ~2,800 events 2007-01-05 to 2018-12-31 (+ one 1990 event); COOLR/GLC projects discontinued — V002 is final"
  resolution: RESOLVED
  notes: "No SPDX/CC license tag is exposed on the landing page; terms are citation-required plus a free NASA Earthdata Login for data access. Redistribution terms remain unwritten on the record — the qualification doc's 'unverified' is confirmed as absent-on-record, not as a failure of lookup."
  remaining_blockers: "no explicit redistribution license on record; Earthdata Login required; report-sampled detection bias unchanged"
```

## Landslide (coseismic) vertical

```yaml
- source_id: usgs_gorkha_2015_landslides
  api_endpoint: "https://www.sciencebase.gov/catalog/item/582c74fbe4b04d580bd377e8?format=json (via DOI 10.5066/F7DZ06F9)"
  retrieval_utc: "2026-09-15T06:25:37Z"
  license_tag_retrieved: "no license field on the item JSON; citation asserts 'U.S. Geological Survey data release'; tags include 'USGS Science Data Catalog (SDC)'"
  license_url: "USGS public-domain policy applies to USGS-authored data releases (item is a USGS data release; originators incl. J.W. Godt, USGS)"
  version_publication_date: "Roback et al. 2017 data release (DOI 10.5066/F7DZ06F9)"
  resolution: RESOLVED
  notes: "Author-affiliation evidence confirmed: this is a USGS-authored USGS data release → public-domain posture per USGS policy stands, consistent with the QUALIFIES (role-scoped) decision. Supplementary: https://www.usgs.gov/information-policies-and-instructions/copyrights-and-credits returned only a ~919-byte JS stub on fetch — treated as page-content UNREACHABLE; the ScienceBase citation is the primary evidence."
  remaining_blockers: "none for the declared coseismic role; role-scope constraint unchanged"

- source_id: gnyawali_adhikari_gorkha
  api_endpoint: "https://www.sciencebase.gov/catalog/item/5874a7cee4b0a829a320bb3e?format=json (via DOI 10.5066/F7028Q2X)"
  retrieval_utc: "2026-09-15T06:25:37Z"
  license_tag_retrieved: "no license field on the item JSON; item is USGS-hosted but originators are Gnyawali K.R. and Adhikari B.R. (non-USGS)"
  license_url: "none exposed on record"
  version_publication_date: "2017 deposit; inventory originally created by Gnyawali and Adhikari (2016); part of the Schmitt et al. ground-failure compilation"
  resolution: RESOLVED
  notes: "Author-affiliation evidence confirmed and it is adverse to the earlier assumption: this is NOT a USGS-authored record — the public-domain presumption does not apply. The record exposes no license tag at all; license posture is genuinely absent-on-record (not a lookup failure)."
  remaining_blockers: "license undetermined — written terms must be obtained from the authors/publisher before redistribution; sequence-window timing unchanged"
```

## LDOF (natural landslide dam) vertical

```yaml
- source_id: jiang_essd_2026_107_ldof_db
  api_endpoint: "https://zenodo.org/api/records/19198720"
  retrieval_utc: "2026-09-15T06:23:14Z"
  license_tag_retrieved: "cc-by-4.0"
  license_url: "https://creativecommons.org/licenses/by/4.0/"
  version_publication_date: "2026-03-26; record DOI 10.5281/zenodo.19198720; concept DOI 10.5281/zenodo.18438522; access_right=open; title 'Bridging the Data Gap: An Enhanced Global Inventory for Statistical Characterization and Breach Prediction of Landslide Dams'"
  resolution: RESOLVED
  notes: "License confirmed CC BY 4.0, open access — lifts the license evidence item. Preprint-status blocker unchanged."
  remaining_blockers: "preprint status; Nepal subset count = intake-gated — NOT performed"
```

## Dam breach (engineered) vertical

```yaml
- source_id: borealis_dam_failure_db
  api_endpoint: "https://borealisdata.ca/api/datasets/:persistentId/?persistentId=doi:10.5683/SP2/E7Z09B"
  retrieval_utc: "2026-09-15T06:24:32Z (fetched) / 06:24:46Z (parsed); earlier attempt on https://dataverse.borealisdata.ca/... failed DNS resolution at 06:24:13Z–06:24:20Z"
  license_tag_retrieved: "license field null; termsOfUse: 'This dataset is made available under a Creative Commons CC0 license with the following additional/modified terms and conditions:' + citationRequirements field requiring correct citation of the dataset and its 196 underlying references"
  license_url: "https://creativecommons.org/publicdomain/zero/1.0/ (base) + record-specific citation terms"
  version_publication_date: "v1.3; publicationDate 2020-02-21; title 'A Worldwide Historical Dam Failure's Database'; 3,861 cases"
  resolution: RESOLVED
  notes: "Terms retrieved: CC0 base + custom citation requirement (community-standards citation of the dataset and its per-case references). Note host discrepancy: the 'dataverse.borealisdata.ca' subdomain does not resolve; canonical API host is 'borealisdata.ca' (DOI 10.5683/SP2/E7Z09B resolves there). fileAccessRequest=false — files not request-gated (not exercised)."
  remaining_blockers: "Nepal engineered subset (expected <=2) = intake-gated — NOT performed"

- source_id: grand_v1_3_gdw
  api_endpoint: "https://api.figshare.com/v2/articles/25988293"
  retrieval_utc: "2026-09-15T06:24:03Z"
  license_tag_retrieved: "CC BY 4.0 (figshare license value=1)"
  license_url: "https://creativecommons.org/licenses/by/4.0/ (returned in license.url)"
  version_publication_date: "v1; published 2024-07-25; DOI 10.6084/m9.figshare.25988293.v1; title 'Global Dam Watch database version 1.0'"
  resolution: RESOLVED
  notes: "License confirmed CC BY 4.0. TITLE DISCREPANCY noted: the record is 'Global Dam Watch database version 1.0' — the qualification doc label 'GRanD v1.3 / GDW' conflates two products; the figshare record is the GDW v1.0 deposit. BLOCKED-as-event-source decision unchanged (attribute layer, not an event inventory)."
  remaining_blockers: "not an event source — unchanged; label should be corrected to GDW v1.0 at next doc revision"
```

## Forecast and reforecast archives

```yaml
- source_id: tigge_ecds_cma
  api_endpoint: "https://ecds.ecmwf.int/licences/tigge-licence"
  retrieval_utc: "2026-09-15T06:27:25Z"
  license_tag_retrieved: "TIGGE licence (rev. 2): per-provider — CC BY 4.0 for DWD, ECCC, ECMWF, KMA, NCEP, UKMO; CC BY-NC 4.0 for BoM, CMA, CPTEC, IMD, JMA, MF, NCMRWF; access delayed 48 h after forecast initial time; ECMWF TIGGE data additionally governed by ECMWF Terms of Use"
  license_url: "https://ecds.ecmwf.int/licences/tigge-licence ; https://creativecommons.org/licenses/by/4.0/legalcode ; https://creativecommons.org/licenses/by-nc/4.0/legalcode"
  version_publication_date: "licence revision 2 (rolling archive 2006→)"
  resolution: RESOLVED
  notes: "Per-provider licence classes retrieved verbatim. For the Nepal-relevant CMA portal path: CMA is in the CC BY-NC 4.0 class — non-commercial only. Confirms the qualification doc's 'mixed incl. non-commercial; per-centre verification required'."
  remaining_blockers: "per-centre verification must bind to whichever centre data are actually pulled from; 48 h dissemination delay must enter the availability-latency margin; registration-gated access unchanged"

- source_id: s2s_database_ecds
  api_endpoint: "https://ecds.ecmwf.int/licences/s2s-licence"
  retrieval_utc: "2026-09-15T06:28:05Z (fetched) / 06:28:23Z (parsed)"
  license_tag_retrieved: "S2S licence: per-provider — CC BY 4.0 for ECCC, ECMWF, KMA, NCEP, HMCR, UKMO; CC BY-NC 4.0 for BoM, CMA, CNR-ISAC, CNRM, CPTEC, IAP-CAS, JMA; realtime access delayed 48 h (CMA, CNR-ISAC, CPTEC, ECCC, ECMWF, HMCR, IAP-CAS, JMA, KMA, NCEP) or 1 week (BoM, CNRM, UKMO); 'The reforecasts can be accessed without any restrictions.'"
  license_url: "https://ecds.ecmwf.int/licences/s2s-licence ; https://creativecommons.org/licenses/by/4.0/legalcode ; https://creativecommons.org/licenses/by-nc/4.0/legalcode"
  version_publication_date: "current licence text (centre list as of retrieval)"
  resolution: RESOLVED
  notes: "Per-provider licence classes retrieved verbatim — confirms 'mixed per-centre licenses incl. CC BY-NC'. Retrieval-time evidence for availability: reforecasts unrestricted; realtime delayed 48 h or 1 week per centre."
  remaining_blockers: "per-centre license review must bind to the actual centres used; delay class (48 h vs 1 week) is per-centre and must enter availability-latency margins"

- source_id: ncar_rda_ds084001_gfs
  api_endpoint: "https://rda.ucar.edu/datasets/d084001/ (landing; now presented as 'NSF NCAR GDEX Dataset d084001')"
  retrieval_utc: "2026-09-15T06:28:08Z"
  license_tag_retrieved: "'This work is licensed under a Creative Commons Attribution 4.0 International License' (link rel=license → creativecommons.org/licenses/by/4.0/)"
  license_url: "https://creativecommons.org/licenses/by/4.0/"
  version_publication_date: "ds084001/d084001 (GDEX presentation); RDA-listed span 2015-01-15 → 2026-10-02 — early-2026 freeze/AWS-migration caveat; tail availability PAYLOAD-GATED"
  resolution: RESOLVED
  notes: "CC BY 4.0 confirmed on the landing page — consistent with the qualification doc's 'verified on RDA record'."
  remaining_blockers: "cycle completeness and per-cycle issue-time retrievability = intake-gated (G14); RDA-listed span ends 2026-10-02 — the early-2026 freeze/AWS migration means tail-cycle availability must be proven at retrieval, not assumed from the listing"
```

## Not re-queried in this lane

The following `source_id` values from the qualification records were not
assigned metadata endpoints for this pass and carry no new evidence
here (decisions and open items unchanged): `nepal_drr_portal_moha`,
`desinventar_nepal`, `emdat_avalanche`, `hmaglofdb_recurrence_note`
(inherits parent), `kaab_2021_detachments`,
`science_china_glacier_slope_inventory`, `jones_2021_monsoon_landslides`,
`desinventar_bipad_landslides`, `usgs_ofr_91_239_landslide_dams`,
`icold_wrd_incidents`, `nepal_engineered_breach_reports`,
`ncei_nomads_archive`, `gefsv12_reforecast_aws`, `c3s_seasonal`,
`ecmwf_mars_operational`, `dynamical_org_open_meteo_single_runs`,
`era5_era5land_imdaa`, `ecmwf_open_data_nomads_rt_imd`.

## Conflict ledger

| Item | Outcome |
|---|---|
| HiAVAL CC BY 4.0 vs CC0 | RESOLVED — cross-version tag change: v1.1.0 tagged cc-by-4.0; v1.2.0/v1.3.0 tagged cc-zero; NHESS article CC BY. Current v1.3.0 record = CC0-1.0; conservative governing term CC BY 4.0 |
| HMAGLOFDB RDS CC BY 4.0 vs Zenodo CC0 | RESOLVED — dual-mirror divergence + same version history pattern; RDS canonical = CC BY 4.0 governs; Zenodo v1.3.0 mirror = CC0 |
| Kneib 10895011 license tag | RESOLVED — cc-by-4.0 |
| Burrows 7970874 license | RESOLVED — cc-by-4.0 |
| essd-2026-481 canonical DOI | RESOLVED — 19477907 = concept DOI (302 → 19477908); versioned record = 10.5281/zenodo.19477908, cc-by-4.0, v1.0-subm |
| Zhong 10080068 license | RESOLVED — cc-by-4.0 tag, but NEW finding: access_right=restricted (files request-gated) |
| Gnyawali–Adhikari F7028Q2X | RESOLVED (adverse) — no license tag on record; non-USGS originators confirmed → public-domain presumption does not apply; written terms still required |
| NSIDC HMA_LS_Cat V002 | RESOLVED — no license class on record; citation-required + Earthdata Login |
| TIGGE / S2S licence classes | RESOLVED — verbatim per-centre CC BY 4.0 / CC BY-NC 4.0 maps retrieved |
| ds084001 license | RESOLVED — CC BY 4.0 confirmed |
| HMAGLOFDB licence surfaces (all four recorded; governing read remains conservative CC BY 4.0 — metadata surfaces, not access authorization) | RDS declaration: CC BY 4.0 · Zenodo record tag: CC0 · GitHub LICENSE file: not re-fetched (metadata-only) · GitHub README: not re-fetched (metadata-only) |

Remaining `CONFLICT_REMAINS` / `UNREACHABLE` / `NOT_FOUND`: none across
queried records. One partial reachability note: the USGS copyrights
policy page returned a JS stub (~919 bytes) — recorded as
page-content-unreachable; ScienceBase citation used instead. The
`dataverse.borealisdata.ca` subdomain does not resolve; canonical host
`borealisdata.ca` used instead.
