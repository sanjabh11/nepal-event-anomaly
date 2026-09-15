# Open Distribution Note — v0

**Status:** `DESIGN_DRAFT_COMPLETE` — redistribution-safety record only.
**Lane:** DocsRecon-R2 (Round-2 audit, gap OPEN-01).
**Scope:** what may be republished from this repository and what must
stay external. This note authorizes nothing new: no download, no
intake, no freeze, no scoring, and no claims about prediction,
warnings, production, or scientific validation. License evidence cited
here is metadata-verified per
`run_b/SOURCE_EVIDENCE_ADDENDUM_V0.md` (retrieval 2026-09-15) and the
decisions in `run_b/SOURCE_QUALIFICATION_RECORDS_V0.md`.

**Binding distinction.** Metadata license resolution ≠ redistribution
permission ≠ event-source qualification. A verified license tag
records the terms governing bytes *if obtained*; it does not grant
file access (records may be request-gated or login-gated) and it does
not qualify any source for an evidentiary role.

## 1. Publishable from this repository

- Source code, schema/contract surfaces (`nepal/research_v0/`,
  `pinned/`), and transform/normalization logic — publishable under
  the repository's own governing terms.
- Protocol, decision, and qualification documents under
  `docs/science/` — metadata records containing no third-party raw
  payload bytes.
- Retrieval-record schemas, sha256 digest bindings, and manifests.
- Derived run artifacts are bound by digest only (see
  `run_a/RUN_A_EVIDENCE_SUMMARY_V0.md` §3); the payloads themselves
  are not part of the distributed record.

## 2. Stays external — never vendored or republished

- Raw source bytes of every third-party dataset: GRIB/NetCDF
  payloads, ERA5-Land/ERA5T-class bytes, mirror extractions, and any
  downloaded event-inventory files. `research_runs/` and `data/` are
  gitignored and are not distribution surfaces.
- Registration-gated archive content (TIGGE, S2S, ECMWF MARS),
  login-gated content (NASA Earthdata), request-gated files
  (`access_right=restricted`), and paywalled content (ICOLD WRD).
- Any bytes governed by CC BY-NC or CC-BY-NC-ND terms where
  republication would exceed the licensed use.
- Reproducibility path for all of the above: re-fetch from the
  canonical endpoints recorded in the addendum; the repository binds
  them by digest and retrieval-record schema, never by vendored copy.

## 3. Verified license posture per source

Metadata-verified in `run_b/SOURCE_EVIDENCE_ADDENDUM_V0.md`
(2026-09-15). "Open" below means an open license tag on the record —
it is a license class, not a grant of access and not a qualification
decision.

| Source (`source_id`) | Verified tag | Redistribution posture |
|---|---|---|
| `hiaval_v1_3_0` | CC0-1.0 on v1.3.0 record | open; conservative governing term CC BY 4.0 (attribution superset) |
| `kneib_s1_everest_deposits` | CC BY 4.0 | open with attribution |
| `safe_hma_annual_deposits` | CC BY 4.0 | open with attribution |
| `icimod_hmaglofdb_v1_3_0` | RDS canonical CC BY 4.0; Zenodo mirror CC0 | open with attribution (CC BY 4.0 governs) |
| `essd_2026_481_glacier_failure_db` | CC BY 4.0 (v1.0-subm) | open with attribution; preprint status unchanged |
| `zhong_2024_ria_inventory` | CC BY 4.0 tag, `access_right=restricted` | request-gated — open tag does not grant file access |
| `burrows_timed_monsoon_landslides` | CC BY 4.0 | open with attribution |
| `icimod_rds_landslide_sets` | CC BY 4.0 | open with attribution |
| `jiang_essd_2026_107_ldof_db` | CC BY 4.0 | open with attribution; preprint status unchanged |
| `usgs_gorkha_2015_landslides` | USGS public domain (author-affiliation verified) | public domain; citation requested |
| `usgs_ofr_91_239_landslide_dams` | USGS public domain (verified) | public domain |
| `borealis_dam_failure_db` | CC0 base + record citation terms | open; citation of dataset + underlying references required |
| `grand_v1_3_gdw` (GDW v1.0, figshare 25988293) | CC BY 4.0 | open with attribution; attribute layer, not an event source |
| `ncar_rda_ds084001_gfs` | CC BY 4.0 | open with attribution; registration for access |
| `tigge_ecds_cma` | per-provider: CC BY 4.0 (DWD, ECCC, ECMWF, KMA, NCEP, UKMO); CC BY-NC 4.0 (BoM, CMA, CPTEC, IMD, JMA, MF, NCMRWF) | registration-gated; non-commercial for the BY-NC centres; 48 h dissemination delay |
| `s2s_database_ecds` | per-provider: CC BY 4.0 (ECCC, ECMWF, KMA, NCEP, HMCR, UKMO); CC BY-NC 4.0 (BoM, CMA, CNR-ISAC, CNRM, CPTEC, IAP-CAS, JMA) | registration-gated; reforecasts unrestricted; real-time delay 48 h or 1 week per centre |
| `avalcd_tajikistan` | CC BY-NC 4.0 | non-commercial only; no Nepal coverage regardless |
| `emdat_avalanche` | CC-BY-NC-ND | no derivatives — governed artifacts cannot be republished |
| `usgs_gorkha`-adjacent `gnyawali_adhikari_gorkha` | no license tag on record; non-USGS originators | terms required in writing before any redistribution |
| `nasa_coolr_hma_ls_v002` | no license class on record; citation-required + Earthdata Login | login-gated; redistribution terms absent-on-record |

Not metadata-queried in the addendum pass — license class as recorded
in the qualification doc, unverified for redistribution until
on-record verification: `ncei_nomads_archive`,
`gefsv12_reforecast_aws`, `c3s_seasonal`, `ecmwf_mars_operational`
(agreement-gated), `dynamical_org_open_meteo_single_runs`,
`era5_era5land_imdaa`, `ecmwf_open_data_nomads_rt_imd`,
`nepal_drr_portal_moha`, `desinventar_nepal`,
`desinventar_bipad_landslides`, `icold_wrd_incidents` (paywalled),
`science_china_glacier_slope_inventory`,
`hmaglofdb_recurrence_note` (inherits parent), `kaab_2021_detachments`,
`jones_2021_monsoon_landslides`, `nepal_engineered_breach_reports`.

## 4. Reproducibility limits (documented, binding)

- Nothing in this repository lets a third party reproduce payload
  bytes without re-fetching from canonical endpoints under their own
  credentials/licenses — that is intentional.
- Run A is method-only implementation-confirmation pending owner
  ratification; its result may not be cited as P5-C-authorized
  scientific evidence (see `run_a/HYBRID_ROUTE_RECONCILIATION_V0.md`
  §2, §5).
- The Run A 2026-08 `sd`/`sf` segment is `expver=0005` preliminary
  (ERA5T-class) and subject to provider consolidation — a later rerun
  need not be byte-identical; flagged for exclusion sensitivity.
- Numeric equivalence between hybrid and literal-route payloads is
  demonstrated only for 5 overlap months; untested months are
  UNVERIFIED.
- Intake-gated items (Nepal subset counts, cycle completeness,
  per-cycle issue-time retrievability, historical availability)
  require downloads this repository does not perform or distribute.
- `warning_path_authorized=false`; `production_authorized=false`;
  `promotion_eligible=false`.
