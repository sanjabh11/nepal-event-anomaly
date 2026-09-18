# Source Qualification Records — v0 (Run B)

**Status:** `DESIGN_DRAFT_COMPLETE` — metadata/specification only.
**Lane:** Run B (`run-b/source-qual`, base `33dbad4`).
**Scope:** qualification decisions per candidate source. This document
authorizes nothing: no download, no intake, no FMX freeze, no
clustering, and no claims about operational use, warnings, production,
or scientific validation.
**Inputs:** `HAZARD_EVENT_INVENTORY_DECISION_MATRIX_V0.md` (D1),
`SOURCE_FEASIBILITY_RECORDS_V0.md` (metadata lane),
`INFORMATION_CUTOFF_TARGET_POLICY_V0.md` (D2), `GAP_REGISTER_V0.md`,
`nepal/research_v0/records.py` (contract surface).

## Decision vocabulary

- `QUALIFIES` — every qualification field is evidence-cleared for the
  *declared research role* recorded on the record. QUALIFIES is
  role-scoped: it never confers pilot candidacy, and it does not lift
  the source to `EVIDENCE_VERIFIED` posture inside `SourceRecordV0` —
  that transition requires a byte-bound sidecar and
  `INDEPENDENTLY_VERIFIED` review at intake.
- `CANDIDATE` — metadata reviewed; at least one named evidence item is
  still open (license, Nepal coverage, timing verification, non-event
  frame, or peer-review status). Maps to `CANDIDATE_ONLY` posture.
- `BLOCKED` — a structural disqualifier exists for the declared role
  (paywall, no Nepal coverage, prohibited redistribution, wrong data
  class). The blocker is intrinsic or external-procurement; it is not
  resolved by waiting.
- `DEFERRED` — the *class* cannot produce admissible evidence with any
  located source. Reserved for the engineered dam-breach vertical:
  `DEFERRED_NO_OPEN_TIMED_SOURCE` stands.

`reviewer` records the reviewing lane. Every record here carries a
single-lane review; independent second review and third-party
adjudication remain intake-stage requirements (D2 §9). `confidence`
describes confidence in the *decision*, not in the source.

**License-evidence distinction (binding).** A resolved metadata license
tag is *not* redistribution permission and *not* event-source
qualification: metadata license resolution ≠ redistribution permission
≠ event-source qualification. License evidence records the terms that
would govern bytes *if obtained* — file access may still be
request-gated or login-gated (e.g. `zhong_2024_ria_inventory`
carries a CC BY 4.0 tag but `access_right=restricted` on the record).
Where a record's `license_evidence`/`redistribution_terms` fields use
informal terms like "open" or "free", they describe a metadata-level
license class only — never a grant of access by themselves — and
`QUALIFIES` still requires every named evidence field cleared for the
declared role.

## Snow avalanche vertical

```yaml
- source_id: hiaval_v1_3_0
  exact_version: "v1.3.0; concept DOI 10.5281/zenodo.7066940"
  doi_or_url: "10.5281/zenodo.18257425; NHESS 23:2569"
  license_evidence: "UNRESOLVED — README/Zenodo CC BY 4.0 vs repo-level CC0 tag"
  redistribution_terms: "unverified until license resolved in writing"
  nepal_coverage: "HMA-wide, 8 countries; Nepal subset count not stated"
  event_count_if_known: "681+ events, 1972–2025 (HMA-wide)"
  timing_class: "EXACT_DAY ~95%; month ~97%; 1 unknown-year record"
  spatial_semantics: "slope_generalized lat/lon (97%) — general mountain slope, not release/runout"
  observation_opportunity: "none — reporting-effort biased; no frame enumeration"
  non_event_feasibility: "infeasible alone; must pair with an independent frame (S1 scenes, basin inventory)"
  reviewer: "RunB metadata review; second review pending at intake"
  decision: CANDIDATE
  blocker: "license resolution; Nepal subset count; no intrinsic non-event frame"
  confidence: "medium — metadata verified; three evidence items open"

- source_id: kneib_s1_everest_deposits
  exact_version: "zenodo.10895011; TC 18:2809 (2024)"
  doi_or_url: "10.5281/zenodo.10895011"
  license_evidence: "UNRESOLVED — Zenodo record license tag unconfirmed"
  redistribution_terms: "unverified pending license tag"
  nepal_coverage: "Everest region (Nepal/China extent split unverified), Hispar, Mont Blanc; glacier surfaces only"
  event_count_if_known: "5-yr window 2017-11–2022-10; deposit count per record"
  timing_class: "INTERVAL_8_30D — scene-interval brackets (6–12 d), never release timestamps"
  spatial_semantics: "deposit_polygon, 10 m class; Automated_outlines_dates + _ManualUpd variants"
  observation_opportunity: "YES — Sentinel1_date file enumerates every analyzed scene pair; event-free covered scenes give principled negatives"
  non_event_feasibility: "feasible via Sentinel1_date opportunity frame; absence-as-negative prohibited"
  reviewer: "RunB metadata review; second review pending at intake"
  decision: CANDIDATE
  blocker: "Zenodo license tag; Nepal/China extent split; ~4,000 m2 size floor; radar shadow/layover; wet-snow false positives"
  confidence: "medium-high — strongest opportunity frame in this vertical"

- source_id: safe_hma_annual_deposits
  exact_version: "figshare.28869170 v3; Earth's Future e2025EF006503"
  doi_or_url: "10.6084/m9.figshare.28869170"
  license_evidence: "CC BY 4.0 (figshare record)"
  redistribution_terms: "attribution; derivatives permitted"
  nepal_coverage: "all HMA, ~60M deposits / 10,701 catchments; Nepal inside Ganga–Brahmaputra tile"
  event_count_if_known: "annual deposit frequency 1990–2023"
  timing_class: "COARSE_OR_UNRESOLVED — year-class only"
  spatial_semantics: "30 m raster deposit/runout zones"
  observation_opportunity: "partial — annual binary presence, detection-conditional"
  non_event_feasibility: "partial — detection-conditional negatives only"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "year-class timing excludes occurrence targets; descriptive susceptibility use only"
  confidence: "medium"

- source_id: nepal_drr_portal_moha
  exact_version: "live portal, no versioned release"
  doi_or_url: "drrportal.gov.np"
  license_evidence: "bulk-export terms undocumented"
  redistribution_terms: "unknown"
  nepal_coverage: "national incident log"
  event_count_if_known: "not established"
  timing_class: "EXACT_DAY (report-derived)"
  spatial_semantics: "admin_unit + place text"
  observation_opportunity: "none — impact-triggered reporting"
  non_event_feasibility: "infeasible"
  reviewer: "RunB metadata review"
  decision: BLOCKED
  blocker: "no documented bulk-export or redistribution terms"
  confidence: "medium"

- source_id: desinventar_nepal
  exact_version: "desinventar.net npl"
  doi_or_url: "desinventar.net"
  license_evidence: "terms unverified"
  redistribution_terms: "unverified"
  nepal_coverage: "1971+; AVALANCHE + SNOW STORM types"
  event_count_if_known: "not established"
  timing_class: "EXACT_DAY (report-derived)"
  spatial_semantics: "admin_unit"
  observation_opportunity: "none"
  non_event_feasibility: "infeasible"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "formal redistribution terms; impact-triggered sampling"
  confidence: "low-medium"

- source_id: emdat_avalanche
  exact_version: "public.emdat.be"
  doi_or_url: "public.emdat.be"
  license_evidence: "CC-BY-NC-ND"
  redistribution_terms: "no derivatives — governed intake artifacts cannot be republished"
  nepal_coverage: "global; high inclusion threshold"
  event_count_if_known: "not established"
  timing_class: "EXACT_DAY"
  spatial_semantics: "admin/text geocode"
  observation_opportunity: "none"
  non_event_feasibility: "infeasible"
  reviewer: "RunB metadata review"
  decision: BLOCKED
  blocker: "ND clause prohibits derivative artifacts; thresholded, impact-biased sampling"
  confidence: "medium"

- source_id: avalcd_tajikistan
  exact_version: "zenodo.15863589"
  doi_or_url: "10.5281/zenodo.15863589"
  license_evidence: "CC BY-NC 4.0"
  redistribution_terms: "non-commercial"
  nepal_coverage: "none — Tajikistan HMA only"
  event_count_if_known: "n/a for Nepal"
  timing_class: "n/a"
  spatial_semantics: "n/a"
  observation_opportunity: "n/a"
  non_event_feasibility: "n/a"
  reviewer: "RunB metadata review"
  decision: BLOCKED
  blocker: "zero Nepal coverage — rejected for the Nepal pilot scope"
  confidence: "high"
```

## GLOF vertical

```yaml
- source_id: icimod_hmaglofdb_v1_3_0
  exact_version: "v1.3.0; paper essd-15-3941-2023"
  doi_or_url: "10.26066/RDS.1973283; zenodo.18257243"
  license_evidence: "RESOLVED — CC BY 4.0 governs (RDS declaration; Zenodo CC0 tag superseded, conservatively read as CC BY 4.0). Prior status 2026-09-16: UNRESOLVED — RDS states CC BY 4.0, Zenodo record shows CC0 (metadata-tag evidence in SOURCE_EVIDENCE_ADDENDUM_V0.md)"
  redistribution_terms: "CC BY 4.0 attribution governs bytes if obtained — metadata-tag resolution only; not byte-verified and not by itself a grant of access"
  nepal_coverage: "HMA 766 events 1533–2025; ~7.6% (~53) Nepal in v1.0 — v1.3.0 Nepal count unverified"
  event_count_if_known: "766 events (HMA); ~53 Nepal (v1.0 count)"
  timing_class: "published v1.0 statistics: EXACT_DAY 39% / month 47% / year-uncertain 26% (±3d caveat; Sat_evidence brackets) — v1.3 distribution PAYLOAD-GATED until bytes; canonical day semantics: event day is the last-day-or-peak-flood day for multi-day events"
  spatial_semantics: "lake_point (within-lake, not breach point) + impact_point (Observation/Deposit tag)"
  observation_opportunity: "PAYLOAD-GATED — GLM3 independent review: the lake-inventory join keys are GL_ID / LakeDB_ID / G_ID (payload columns, coverage unverified until bytes), NOT the GF_ID event key; event-free lake periods feasible only IF the join coverage and owner-approved external linkage exist (see opportunity_frame below)"
  non_event_feasibility: "feasible via lake-level opportunity frame (ICIMOD 2015 Koshi/Gandaki/Karnali; NSIDC HMA_GLI; Wang 2020 ESSD; zenodo.17948783) — admissibility of the external frame is an owner-policy question, not settled here"
  current_status:
    license: "RESOLVED — CC BY 4.0 governs (RDS declaration; Zenodo CC0 tag superseded, conservatively read as CC BY 4.0)"
    payload_verification: "PENDING (no bytes acquired); Zenodo record lists a 107,879-byte payload with a published MD5 — recorded as metadata only; acquisition requires independent SHA-256 byte-binding"
    nepal_v1_3_count: "PENDING (v1.0 said ~53/7.6%; v1.3 count unverified until bytes)"
    opportunity_frame: "PENDING-OWNER-POLICY (no native non-event frame; external lake-inventory linkage needs owner decision)"
  reviewer: "RunB metadata review; second review pending at intake"
  decision: CANDIDATE
  blocker: "Nepal count in v1.3.0 (payload-gated); GL_ID/LakeDB_ID/G_ID join coverage (payload-gated); opportunity-frame owner policy (external lake-inventory linkage vs source-native frame); day-precision share limits horizon admissibility — published v1.0 reports 39% day / 47% month / 26% year-uncertain; v1.3 distribution PAYLOAD-GATED"
  confidence: "medium-high — nearest candidate vertical-wide"

- source_id: hmaglofdb_recurrence_note
  exact_version: "v1.3.0 schema note"
  doi_or_url: "same record as icimod_hmaglofdb_v1_3_0"
  license_evidence: "inherits parent record"
  redistribution_terms: "inherits parent record"
  nepal_coverage: "schema: GF_ID is the integer event-identity key; the Repeat field is the recurrence indicator; the _Z suffix convention is paper-reported but UNVERIFIED against the integer GF_ID schema until payload inspection; paper reports 23% of events from 3 ephemeral ice-dammed lakes"
  event_count_if_known: "23% recurrence share"
  timing_class: "inherits parent"
  spatial_semantics: "inherits parent"
  observation_opportunity: "paper-reported: recurrence grouped by base GF_ID (integer event key; Repeat field flags recurrence; _Z suffix convention UNVERIFIED until payload inspection) before dedup"
  non_event_feasibility: "inherits parent"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "recurrence conflation risk — grouped, never merged (see EVENT_PACKAGE_SPEC dedup rules)"
  confidence: "medium"
```

## Ice/rock avalanche and glacier-failure vertical

```yaml
- source_id: essd_2026_481_glacier_failure_db
  exact_version: "zenodo.19477907/.19477908 (canonical DOI unresolved); ESSD discussion preprint Aug 2026"
  doi_or_url: "10.5281/zenodo.19477907"
  license_evidence: "ESSD preprints CC BY 4.0; Zenodo license field unconfirmed"
  redistribution_terms: "expected attribution; unverified on record"
  nepal_coverage: "global excl. ice sheets; 502 events / 228 glaciers / 11 RGI v7 regions; Nepal included, count unstated"
  event_count_if_known: "502 events"
  timing_class: "day/month/year where available + Season + Min/Max-Date ranges — mixed"
  spatial_semantics: "source-slope + RGI v7 glacier linkage"
  observation_opportunity: "RGI v7 glacier population provides a non-event denominator"
  non_event_feasibility: "feasible via RGI v7 denominator"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "peer review incomplete (discussion preprint — not citable); canonical DOI; license field; Nepal subset count"
  confidence: "medium — deferred for any forecast pilot until review completes"

- source_id: zhong_2024_ria_inventory
  exact_version: "zenodo.10080068; Geomorphology 109048 (2024)"
  doi_or_url: "10.5281/zenodo.10080068"
  license_evidence: "CC BY 4.0 tag on record (addendum) — but access_right=restricted: files are request-gated despite the open tag"
  redistribution_terms: "request-gated pending access grant; CC BY 4.0 governs obtained bytes"
  nepal_coverage: "60 large HMA rock-ice avalanches; Nepal subset count unstated"
  event_count_if_known: "60 events; >=1366 fatalities"
  timing_class: "event dates in inventory — per-event precision unverified"
  spatial_semantics: "source-slope characterization + cascade flag; static"
  observation_opportunity: "none enumerated"
  non_event_feasibility: "infeasible alone"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "restricted file access (external grant required); Nepal subset; reporting bias pre-2010"
  confidence: "medium"

- source_id: kaab_2021_detachments
  exact_version: "tc-15-1751-2021"
  doi_or_url: "10.5194/tc-15-1751-2021"
  license_evidence: "CC BY"
  redistribution_terms: "attribution"
  nepal_coverage: "none — 20 detachments, zero Nepal sites"
  event_count_if_known: "20 detachments"
  timing_class: "event dates published"
  spatial_semantics: "detachment source geometry"
  observation_opportunity: "taxonomic gold standard, not a Nepal frame"
  non_event_feasibility: "n/a for Nepal"
  reviewer: "RunB metadata review"
  decision: BLOCKED
  blocker: "no Nepal coverage; usable only as taxonomy reference"
  confidence: "high"

- source_id: science_china_glacier_slope_inventory
  exact_version: "unverified — 727 events 1901–2019"
  doi_or_url: "unverified"
  license_evidence: "unverified"
  redistribution_terms: "unverified"
  nepal_coverage: "HMA scope; Nepal subset unverified; machine-readable access unverified"
  event_count_if_known: "727 events"
  timing_class: "unverified"
  spatial_semantics: "unverified"
  observation_opportunity: "unverified"
  non_event_feasibility: "unverified"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "access path and license both unverified"
  confidence: "low"
```

## Landslide (rainfall) vertical

```yaml
- source_id: burrows_timed_monsoon_landslides
  exact_version: "zenodo.7970874; NHESS 22:2637 (2022)"
  doi_or_url: "10.5281/zenodo.7970874"
  license_evidence: "CC BY 4.0 (verified on record — addendum)"
  redistribution_terms: "attribution"
  nepal_coverage: "Nepal incl. western extension; 2015/17/18/19 monsoons"
  event_count_if_known: "per record; ~30% of mapped slides carry S1-constrained timing"
  timing_class: "INTERVAL_8_30D — ~12-day window on ~30% of slides; remaining ~70% untimed"
  spatial_semantics: "slide polygons"
  observation_opportunity: "image-opportunity frame via S1 acquisitions"
  non_event_feasibility: "feasible at slice granularity on the timed subset; 70% untimed must be censored, never dropped"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "license field; 70% untimed censoring rule; >=30d horizon floor"
  confidence: "medium"

- source_id: jones_2021_monsoon_landslides
  exact_version: "NGDC items 166945/166966; Nat Comms 12:26964 (2021)"
  doi_or_url: "NGDC 166966"
  license_evidence: "CANDIDATE — NERC standard terms, unverified on record"
  redistribution_terms: "unverified"
  nepal_coverage: "~42,000 km2 central-eastern Nepal; 12,838 polygons; 29 monsoon slices 1988–2018"
  event_count_if_known: "12,838 polygons"
  timing_class: "COARSE_OR_UNRESOLVED — monsoon-slice window; 2011–12 unmapped (SLC-off)"
  spatial_semantics: "slide polygons"
  observation_opportunity: "GOOD at slice granularity — mapping extent documented"
  non_event_feasibility: "feasible at monsoon-slice granularity only"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "license field; slice timing excludes occurrence targets; descriptive/coarse-window research only"
  confidence: "medium"

- source_id: icimod_rds_landslide_sets
  exact_version: "RDS.34425/.34426/rds.31016"
  doi_or_url: "10.26066/RDS.34425 et al."
  license_evidence: "CC BY 4.0 verified on Koshi records"
  redistribution_terms: "attribution"
  nepal_coverage: "Koshi basin 1990/2010; 14 Gorkha districts"
  event_count_if_known: "epochal snapshots"
  timing_class: "COARSE_OR_UNRESOLVED — epochal"
  spatial_semantics: "slide polygons"
  observation_opportunity: "limited"
  non_event_feasibility: "limited"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "epochal timing; partial coverage"
  confidence: "medium"

- source_id: nasa_coolr_hma_ls_v002
  exact_version: "NSIDC-CPRD-HMA-LS-CAT-2 V002"
  doi_or_url: "NSIDC-CPRD-HMA-LS-CAT-2"
  license_evidence: "open + citation; downstream redistribution unverified"
  redistribution_terms: "unverified"
  nepal_coverage: "global/HMA ~2,800 events incl. Nepal"
  event_count_if_known: "~2,800 events"
  timing_class: "EXACT_DAY (report-derived)"
  spatial_semantics: "point/polygon mixed"
  observation_opportunity: "POOR — report sampling conflates detection"
  non_event_feasibility: "infeasible alone"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "report-sampled detection bias; redistribution terms"
  confidence: "medium"

- source_id: desinventar_bipad_landslides
  exact_version: "desinventar.net / bipadportal.gov.np"
  doi_or_url: "desinventar.net; bipadportal.gov.np"
  license_evidence: "terms unverified"
  redistribution_terms: "unverified"
  nepal_coverage: "1971+ impact records"
  event_count_if_known: "not established"
  timing_class: "EXACT_DAY (report-derived)"
  spatial_semantics: "admin_unit"
  observation_opportunity: "POOR — impact-triggered"
  non_event_feasibility: "infeasible"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "formal redistribution terms; impact-triggered sampling"
  confidence: "low-medium"
```

## Landslide (coseismic) vertical

```yaml
- source_id: usgs_gorkha_2015_landslides
  exact_version: "10.5066/F7DZ06F9; shapefiles 20170209"
  doi_or_url: "10.5066/F7DZ06F9"
  license_evidence: "USGS public domain (verified)"
  redistribution_terms: "public domain; citation requested"
  nepal_coverage: ">30,000 km2 Nepal Himalaya; 24,915 full / 24,795 source polygons"
  event_count_if_known: "24,915 polygons"
  timing_class: "EXACT_TIMESTAMP trigger window (2015-04-25 06:11 UTC mainshock + aftershock sequence); per-feature dates not stored"
  spatial_semantics: "source vs full-area polygons separated; documented mapping extent + obscured-area flags"
  observation_opportunity: "GOOD — mapping extent + obscured-area flags give defensible negatives"
  non_event_feasibility: "feasible — documented extent with obscured-area mask"
  reviewer: "RunB metadata review"
  decision: QUALIFIES
  blocker: "role-scoped: coseismic trigger class only — usable as seismic-trigger labels or adjacent-class controls, never rainfall-pilot labels; single trigger sequence"
  confidence: "high for the declared role"

- source_id: gnyawali_adhikari_gorkha
  exact_version: "10.5066/F7028Q2X"
  doi_or_url: "10.5066/F7028Q2X"
  license_evidence: "CANDIDATE — non-USGS authored record"
  redistribution_terms: "unverified"
  nepal_coverage: "Gorkha sequence area"
  event_count_if_known: "per record"
  timing_class: "sequence window — 'sequence rather than single mainshock'"
  spatial_semantics: "slide polygons"
  observation_opportunity: "moderate"
  non_event_feasibility: "moderate"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "license posture; sequence-window timing on per-feature labels"
  confidence: "medium"
```

## LDOF (natural landslide dam) vertical

```yaml
- source_id: jiang_essd_2026_107_ldof_db
  exact_version: "zenodo.19198720; ESSD-2026-107 preprint"
  doi_or_url: "10.5281/zenodo.19198720"
  license_evidence: "CANDIDATE — standard CC-BY expected, unverified"
  redistribution_terms: "unverified"
  nepal_coverage: "902 global events to ~2020; Himalayan belt dense; Nepal subset unverified"
  event_count_if_known: "902 events"
  timing_class: "day-to-month precision; news/literature-derived"
  spatial_semantics: "dam-site + breach parameters + quality flags"
  observation_opportunity: "event-free river-reach frames feasible"
  non_event_feasibility: "feasible via river-reach frames"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "preprint status; license field; Nepal subset count; dam-formation vs breach timing kept separate"
  confidence: "medium"

- source_id: usgs_ofr_91_239_landslide_dams
  exact_version: "USGS OFR 91-239"
  doi_or_url: "USGS OFR 91-239"
  license_evidence: "public domain (verified)"
  redistribution_terms: "public domain"
  nepal_coverage: "463 historical landslide dams, pre-1991 global"
  event_count_if_known: "463 events"
  timing_class: "COARSE_OR_UNRESOLVED — historical"
  spatial_semantics: "dam-site records"
  observation_opportunity: "historical baseline only"
  non_event_feasibility: "not a frame — context reference"
  reviewer: "RunB metadata review"
  decision: QUALIFIES
  blocker: "role-scoped: historical-frequency context only; not an event-label source for timed experiments"
  confidence: "medium-high for the declared role"
```

## Dam breach (engineered) vertical — DEFERRED

```yaml
- source_id: icold_wrd_incidents
  exact_version: "icold-cigb.org WRD"
  doi_or_url: "icold-cigb.org"
  license_evidence: "paywalled 3-yr license"
  redistribution_terms: "closed"
  nepal_coverage: "global dam attributes; failures only in member publications"
  event_count_if_known: "n/a"
  timing_class: "n/a"
  spatial_semantics: "n/a"
  observation_opportunity: "n/a"
  non_event_feasibility: "n/a"
  reviewer: "RunB metadata review"
  decision: BLOCKED
  blocker: "paywalled — not an open source"
  confidence: "high"

- source_id: borealis_dam_failure_db
  exact_version: "10.5683/SP2/E7Z09B"
  doi_or_url: "10.5683/SP2/E7Z09B"
  license_evidence: "CC0 base + record-specific citation terms (Dataverse API, addendum)"
  redistribution_terms: "CC0 with mandatory citation of dataset + its underlying references"
  nepal_coverage: "3,861 cases; Nepal engineered count unverified (expected <=2)"
  event_count_if_known: "3,861 cases"
  timing_class: "unverified"
  spatial_semantics: "dam-site"
  observation_opportunity: "unverified"
  non_event_feasibility: "unverified"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "Nepal engineered subset unverified and expected near-empty"
  confidence: "medium"

- source_id: grand_v1_3_gdw
  exact_version: "figshare.25988293 v1 (2024-07-25); product = Global Dam Watch (GDW) database version 1.0 — NOT GRanD v1.3. The earlier 'GRanD v1.3 / GDW' label conflated two distinct products; the figshare record is the GDW v1.0 deposit (title verified on record, addendum). source_id retained unchanged for record/addendum continuity."
  doi_or_url: "10.6084/m9.figshare.25988293"
  license_evidence: "CC BY 4.0 (verified on figshare record — addendum)"
  redistribution_terms: "attribution"
  nepal_coverage: "dam locations incl. Kulekhani — attributes, not failures"
  event_count_if_known: "not an event inventory"
  timing_class: "n/a"
  spatial_semantics: "dam-site points + reservoir attributes"
  observation_opportunity: "n/a as event source"
  non_event_feasibility: "n/a"
  reviewer: "RunB metadata review"
  decision: BLOCKED
  blocker: "not an event source — GDW v1.0 is a dam-attribute layer; candidate exposure/context layer for a separately declared impact analysis only"
  confidence: "high"

- source_id: nepal_engineered_breach_reports
  exact_version: "reports only — no structured inventory"
  doi_or_url: "—"
  license_evidence: "n/a"
  redistribution_terms: "n/a"
  nepal_coverage: "Koshi embankment failures 1963/1971/1991/2008-08-18 (levee/embankment subclass, not storage dam)"
  event_count_if_known: "report-derived, unstructured"
  timing_class: "day-class from reports (2008-08-18)"
  spatial_semantics: "embankment reach"
  observation_opportunity: "none"
  non_event_feasibility: "infeasible"
  reviewer: "RunB metadata review"
  decision: DEFERRED
  blocker: "no open timed source located in a bounded search — not a proof of absence; engineered class stays DEFERRED_NO_OPEN_TIMED_SOURCE"
  confidence: "medium — search-bounded"
```

## Forecast and reforecast archives (metadata-verified only)

Archive-class rows are metadata-verified only: issue-time
retrievability, cycle completeness, and availability latency require
per-experiment verification at intake (gate G14). No provider archives
*public-retrievable* time; `referenceTime` proves issue, not
availability, so every archived vintage carries a preregistered
conservative issue+dissemination latency margin (D2 §1).

```yaml
- source_id: tigge_ecds_cma
  exact_version: "rolling archive, 2006→"
  doi_or_url: "TIGGE via ECDS/CMA portals"
  license_evidence: "per-provider licensing incl. CC BY and CC BY-NC — per-centre verification required before redistribution"
  redistribution_terms: "per-centre; mixed including non-commercial"
  nepal_coverage: "global ensembles"
  event_count_if_known: "n/a — forecast archive"
  timing_class: "issue-time vintages"
  spatial_semantics: "grid fields"
  observation_opportunity: "n/a"
  non_event_feasibility: "n/a"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "registration delayed 48 h; per-provider license review incomplete — access/licence gate, not an intrinsic disqualification"
  confidence: "medium"

- source_id: ncar_rda_ds084001_gfs
  exact_version: "ds084001; RDA-listed span 2015-01-15 → 2026-10-02 — early-2026 freeze/AWS-migration caveat, tail availability PAYLOAD-GATED"
  doi_or_url: "NCAR RDA ds084001"
  license_evidence: "free, CC-BY-4.0 (verified on RDA record)"
  redistribution_terms: "attribution"
  nepal_coverage: "global 0.25°"
  event_count_if_known: "n/a"
  timing_class: "issue-time vintages"
  spatial_semantics: "grid fields"
  observation_opportunity: "n/a"
  non_event_feasibility: "n/a"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "cycle completeness + per-cycle issue-time retrievability unverified; RDA-listed span ends 2026-10-02 (early-2026 freeze/AWS migration — tail-cycle availability must be proven at retrieval)"
  confidence: "medium-high"

- source_id: ncei_nomads_archive
  exact_version: "GEFS official archive 2008-01-01 → 2020-09-23 (post-2020 GEFS is NODD-only — not officially archived, CURRENT_FEED class); GFS 1° 2005→"
  doi_or_url: "NCEI NOMADS archive"
  license_evidence: "free"
  redistribution_terms: "open"
  nepal_coverage: "global"
  event_count_if_known: "n/a"
  timing_class: "issue-time vintages"
  spatial_semantics: "grid fields"
  observation_opportunity: "n/a"
  non_event_feasibility: "n/a"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "cycle completeness unverified"
  confidence: "medium"

- source_id: gefsv12_reforecast_aws
  exact_version: "noaa-gefs-retrospective; 2000–2019, 5–11 members"
  doi_or_url: "AWS noaa-gefs-retrospective"
  license_evidence: "free, no account"
  redistribution_terms: "open"
  nepal_coverage: "global"
  event_count_if_known: "n/a"
  timing_class: "REFORECAST — fixed-model hindcast, NOT an archive of real-time operational runs"
  spatial_semantics: "grid fields"
  observation_opportunity: "n/a"
  non_event_feasibility: "n/a"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "reforecast class — never proves operational availability"
  confidence: "high for the class"

- source_id: s2s_database_ecds
  exact_version: "1981→ centre-dependent"
  doi_or_url: "S2S database (ECDS)"
  license_evidence: "free; mixed per-centre licenses incl. CC BY-NC"
  redistribution_terms: "per-centre review required"
  nepal_coverage: "global"
  event_count_if_known: "n/a"
  timing_class: "REFORECAST + archived real-time"
  spatial_semantics: "grid fields"
  observation_opportunity: "n/a"
  non_event_feasibility: "n/a"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "per-centre license review required"
  confidence: "medium"

- source_id: c3s_seasonal
  exact_version: "hindcast 1993–2016; real-time 2017→"
  doi_or_url: "C3S seasonal"
  license_evidence: "free"
  redistribution_terms: "open"
  nepal_coverage: "global"
  event_count_if_known: "n/a"
  timing_class: "ARCHIVED initialized (seasonal)"
  spatial_semantics: "grid fields"
  observation_opportunity: "n/a"
  non_event_feasibility: "n/a"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "seasonal context only — not occurrence-horizon evidence"
  confidence: "medium"

- source_id: ecmwf_mars_operational
  exact_version: "1985→"
  doi_or_url: "ECMWF MARS"
  license_evidence: "Service Agreement; EUR 3k/yr; research fee waiver path"
  redistribution_terms: "agreement-gated"
  nepal_coverage: "global"
  event_count_if_known: "n/a"
  timing_class: "issue-time vintages (gold standard)"
  spatial_semantics: "grid fields"
  observation_opportunity: "n/a"
  non_event_feasibility: "n/a"
  reviewer: "RunB metadata review"
  decision: BLOCKED
  blocker: "procurement-gated — Nepal non-member; fee-waiver lead time; BLOCKED_EXTERNAL"
  confidence: "high"

- source_id: dynamical_org_open_meteo_single_runs
  exact_version: "GEFS 2020→; IFS ENS 2024→"
  doi_or_url: "dynamical.org / Open-Meteo Single Runs"
  license_evidence: "free/attribution"
  redistribution_terms: "attribution"
  nepal_coverage: "global"
  event_count_if_known: "n/a"
  timing_class: "third-party archive — no provider publication-time proof"
  spatial_semantics: "grid fields"
  observation_opportunity: "n/a"
  non_event_feasibility: "n/a"
  reviewer: "RunB metadata review"
  decision: CANDIDATE
  blocker: "secondary evidence only — cannot substitute provider-side availability proof"
  confidence: "medium"

- source_id: era5_era5land_imdaa
  exact_version: "ERA5/ERA5-Land(+T)/IMDAA; 1940/1950→"
  doi_or_url: "C3S CDS; IMDAA"
  license_evidence: "free"
  redistribution_terms: "open"
  nepal_coverage: "global/regional"
  event_count_if_known: "n/a"
  timing_class: "REANALYSIS — retrospective class"
  spatial_semantics: "grid fields"
  observation_opportunity: "n/a"
  non_event_feasibility: "n/a"
  reviewer: "RunB metadata review"
  decision: BLOCKED
  blocker: "class exclusion — never forecast-skill evidence; retrospective regime path only (the separately authorized P5-C diagnostic is a descriptive lane, not forecast evidence)"
  confidence: "high"

- source_id: ecmwf_open_data_nomads_rt_imd
  exact_version: "~12 runs / ~7 d rolling"
  doi_or_url: "ECMWF Open Data; NCEP NOMADS; IMD plots"
  license_evidence: "open feeds"
  redistribution_terms: "open"
  nepal_coverage: "global"
  event_count_if_known: "n/a"
  timing_class: "CURRENT_FEED — rolling, not an archive"
  spatial_semantics: "grid fields"
  observation_opportunity: "n/a"
  non_event_feasibility: "n/a"
  reviewer: "RunB metadata review"
  decision: BLOCKED
  blocker: "class exclusion — cannot supply historical vintages"
  confidence: "high"
```

## Outcome summary

| Decision | Sources |
|---|---|
| `QUALIFIES` (role-scoped) | `usgs_gorkha_2015_landslides` (coseismic labels/controls), `usgs_ofr_91_239_landslide_dams` (historical context) |
| `CANDIDATE` | HiAVAL, Kneib S1, SAFE-HMA, DesInventar (both), HMAGLOFDB (+recurrence), essd-2026-481, Zhong RIA, Science China, Burrows, Jones, ICIMOD RDS LS, NASA COOLR, Gnyawali–Adhikari, Jiang LDOF, Borealis, ds084001 GFS, NCEI NOMADS, GEFSv12, TIGGE, S2S, C3S, dynamical.org/Open-Meteo |
| `BLOCKED` | Nepal DRR Portal, EM-DAT, AvalCD, Kääb 2021, ICOLD, GDW v1.0 (`grand_v1_3_gdw`, as event source), ECMWF MARS, ERA5/ERA5-Land/IMDAA (forecast-evidence role), CURRENT_FEED sources |
| `DEFERRED` | `dam_breach_engineered` — `DEFERRED_NO_OPEN_TIMED_SOURCE` stands |

No source is pre-nominated for a pilot. The pilot-selection outcome
recorded in D1 stands: `NO_QUALIFYING_PILOT_SOURCE` — GLOF and snow
avalanche remain the nearest candidates, each blocked on the named
evidence items above.
