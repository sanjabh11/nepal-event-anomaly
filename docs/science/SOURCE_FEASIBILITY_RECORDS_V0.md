# Source Feasibility Records — v0

**Status:** `METADATA_REVIEW_COMPLETE` (metadata-only lane audit). All
sources are `CANDIDATE_ONLY` until license/version/geography/fields/
timing are verified at intake. Compiled from
four independent metadata-only review lanes on 2026-09-13.

**Scope label (clarified 2026-09-15):** this record is a dated
metadata-only snapshot. No candidate-source payload bytes were fetched
for this record — "no data was downloaded" applies to this
metadata-review lane only. It does not assert that no data exists
anywhere in the project: the ERA5-Land bytes used by Run A
(`gmm_confirmation_20260915T052240Z`) are a separate,
previously-acquired dataset under the distinct P5-C
bounded-acquisition authorization, documented in
`run_a/RUN_A_EVIDENCE_SUMMARY_V0.md` and
`run_a/HYBRID_ROUTE_RECONCILIATION_V0.md`.

**Supersession note (2026-09-16; amended 2026-09-18):** the License
cells below are the dated 2026-09-13 metadata-lane record and are
deliberately **not rewritten**, except the ICIMOD HMAGLOFDB cell,
which was reconciled 2026-09-18 to the single current-status
vocabulary below (its prior `UNRESOLVED` wording is preserved in-cell
as dated history). Since this snapshot,
`run_b/SOURCE_EVIDENCE_ADDENDUM_V0.md`
(public metadata APIs, retrieval 2026-09-15) and
`OPEN_DISTRIBUTION_NOTE_V0.md` resolved the metadata-license **tags**
for the rows still marked **UNRESOLVED** / "Verify on record" here —
concretely: HiAVAL v1.3.0 (record tag CC0; conservative governing
term CC BY 4.0), Kneib et al. Sentinel-1 deposits (CC BY 4.0),
ICIMOD HMAGLOFDB v1.3.0 (RESOLVED — CC BY 4.0 governs: RDS
declaration; Zenodo CC0 tag superseded, conservatively read as
CC BY 4.0),
and Zhong et al. RIA (CC BY 4.0 tag but `access_right=restricted` —
file access is request-gated). Resolved tags change no posture:
payload/redistribution qualification, Nepal subset counts, extent
splits, file schemas, and every item under "Outstanding evidence
items" remain intake-gated; all sources stay `CANDIDATE_ONLY` and
`NO_QUALIFYING_PILOT_SOURCE` stands.

## Current status index (2026-09-17)

One line per source: metadata-license **tag** status vs payload-access
status vs current decision.  **Metadata-license tag resolution is NOT
payload access or redistribution permission; no source is qualified
until byte-verified evidence plus independent review exist at
intake.**  The dated rows below are preserved verbatim.

**Snow avalanche**

- HiAVAL — tag resolved (record CC0; conservative governing term CC BY 4.0) / payload unverified / `CANDIDATE_ONLY`.
- Kneib et al. Sentinel-1 deposits — tag resolved (CC BY 4.0) / payload unverified; Nepal/China extent split unresolved / `CANDIDATE_ONLY`.
- SAFE-HMA — tag resolved (CC BY 4.0) / payload unverified / `CANDIDATE_ONLY`.
- Nepal DRR Portal (MoHA) — bulk-export terms undocumented / payload unverified / `CANDIDATE_ONLY`.
- DesInventar Nepal — terms unresolved / payload unverified / `CANDIDATE_ONLY`.
- EM-DAT — CC-BY-NC-ND (no derivatives) / payload unverified / `CANDIDATE_ONLY`.
- AvalCD — CC BY-NC 4.0, no Nepal coverage / not applicable / `REJECTED` for Nepal pilot.

**GLOF**

- ICIMOD HMAGLOFDB v1.3.0 — license: RESOLVED — CC BY 4.0 governs (RDS declaration; Zenodo CC0 tag superseded, conservatively read as CC BY 4.0) / payload_verification: PENDING (no bytes acquired) / nepal_v1_3_count: PENDING (v1.0 said ~53/7.6%; v1.3 count unverified until bytes) / opportunity_frame: PENDING-OWNER-POLICY (no native non-event frame; external lake-inventory linkage needs owner decision) / `CANDIDATE_ONLY` (strongest candidate).

**Ice/rock avalanche / glacier failure**

- essd-2026-481 glacier-failure DB — tag resolved (CC BY 4.0 on canonical versioned DOI zenodo.19477908) / payload unverified; discussion preprint / `CANDIDATE_ONLY`.
- Zhong et al. RIA — CC BY 4.0 tag but `access_right=restricted` (request-gated) / payload access blocked / `BLOCKED_EXTERNAL`.
- Kääb et al. 2021 — CC BY / payload unverified; no Nepal sites / `CANDIDATE_ONLY` (weak Nepal source).
- Science China glacier-slope-failure inventory — license unverified / machine-readable access unverified / `CANDIDATE_ONLY`.

**Landslide**

- USGS Gorkha — USGS public domain / payload unverified / `CANDIDATE_ONLY` (seismic trigger class only).
- Gnyawali & Adhikari Gorkha — no license field on record; public-domain presumption rejected / payload unverified / `CANDIDATE_ONLY`.
- Burrows et al. timed monsoon set — tag resolved (CC BY 4.0) / payload unverified / `CANDIDATE_ONLY`.
- Jones et al. 30-yr monsoon — NGDC 166966 license unresolved / payload unverified / `CANDIDATE_ONLY`.
- ICIMOD RDS landslide sets — CC BY 4.0 verified on Koshi records / payload unverified / `CANDIDATE_ONLY`.
- NASA COOLR GLC + HMA LS V002 — open with citation; redistribution unresolved / payload unverified / `CANDIDATE_ONLY`.
- DesInventar/BIPAD — terms unresolved / payload unverified / `CANDIDATE_ONLY`.

**Dam breach / LDOF**

- ICOLD WRD — paywalled 3-yr license / `BLOCKED_EXTERNAL` (not open).
- Borealis worldwide failure DB — Open Dataverse / Nepal engineered count unverified / `CANDIDATE_ONLY`.
- GRanD/GDW (figshare.25988293) — CC BY; record is GDW v1.0, not GRanD v1.3 / payload unverified / exposure layer only.
- Jiang et al. LDOF — tag resolved (CC BY 4.0) / preprint; Nepal subset unverified / `CANDIDATE_ONLY`.
- USGS OFR 91-239 — public domain / historical baseline only.
- Nepal engineered breaches — no structured timed source located (not a proof of absence) / `DEFERRED_NO_OPEN_TIMED_SOURCE`.

**Forecast / reforecast archives** — metadata review only; none
qualified for forecast use:

- TIGGE (ECDS/CMA) — per-centre CC BY / CC BY-NC split documented; registration delayed 48 h / issue-time retrieval + per-centre license review required / `CANDIDATE_ONLY`.
- NCAR RDA ds084001 GFS — CC BY 4.0; bounded span 2015→2025 verified / `CANDIDATE_ONLY`.
- NCEI NOMADS — free; cycle completeness unverified / `CANDIDATE_ONLY`.
- GEFSv12 reforecast (AWS) — free; reforecast only — does not prove operational availability / `CANDIDATE_ONLY`.
- S2S database (ECDS) — per-centre mixed licenses incl. CC BY-NC; review required / `CANDIDATE_ONLY`.
- C3S seasonal — free; seasonal context only / `CANDIDATE_ONLY`.
- ECMWF MARS operational — procurement-gated (Service Agreement, ~€3k/yr, Nepal non-member) / `BLOCKED_EXTERNAL`.
- dynamical.org / Open-Meteo — third-party archive; no provider publication-time proof / secondary only.
- ERA5 / ERA5-Land(+T) / IMDAA — REANALYSIS — never forecast-skill evidence.
- ECMWF Open Data / NOMADS RT / IMD — CURRENT_FEED, ~7-day rolling — NOT archives.

## Snow avalanche

| Source | Version/DOI | License | Coverage | Time precision | Spatial semantics | Non-event frame | Posture |
|---|---|---|---|---|---|---|---|
| HiAVAL | v1.3.0 = zenodo.18257425 (concept 10.5281/zenodo.7066940); NHESS 23:2569 | **UNRESOLVED** — README/Zenodo CC BY 4.0 vs repo-level CC0 tag (superseded — see 2026-09-16 note: CC0 tag, conservative CC BY 4.0) | HMA, 8 countries, 681+ events 1972–2025; Nepal event count not stated | 95% day, 97% month, 1 unknown-year | Lat/lon 97% — "general mountain slope," NOT release/runout | None — reporting-effort biased | CANDIDATE_ONLY |
| Kneib et al. Sentinel-1 deposits | zenodo.10895011; TC 18:2809 | **UNRESOLVED** — Zenodo record license tag unconfirmed (superseded — see 2026-09-16 note: CC BY 4.0) | Everest region (Nepal/China split unverified), Hispar, Mt Blanc; glacier surfaces only | Scene-interval (6–12 d), never release time | Deposit polygons, 10 m class; `Automated_outlines_dates` + `_ManualUpd` variants | YES — `Sentinel1_date` file enumerates every analyzed scene pair | CANDIDATE_ONLY |
| SAFE-HMA | figshare.28869170 v3; Earth's Future e2025EF006503 | CC BY 4.0 | All HMA, ~60M deposits / 10,701 catchments; Nepal in Ganga–Brahmaputra tile | YEAR-class (annual presence count) | 30 m raster deposit/runout zones | Partial — annual binary presence, detection-conditional | CANDIDATE_ONLY |
| Nepal DRR Portal (MoHA) | drrportal.gov.np | Bulk-export terms undocumented | National, incident log | Day-class | Admin unit + place text | None — impact-triggered | CANDIDATE_ONLY |
| DesInventar Nepal | desinventar.net npl | CANDIDATE_ONLY | 1971+; AVALANCHE + SNOW STORM types | Day-class | Admin units | None | CANDIDATE_ONLY |
| EM-DAT | public.emdat.be | CC-BY-NC-ND (no derivatives — weakest) | Global; high inclusion threshold | Day-class | Admin/text geocode | None | CANDIDATE_ONLY |
| AvalCD (zenodo.15863589) | — | CC BY-NC 4.0 | Tajikistan HMA; no Nepal | — | — | — | REJECTED for Nepal pilot |

## GLOF

| Source | Version/DOI | License | Coverage | Time precision | Spatial semantics | Non-event frame | Posture |
|---|---|---|---|---|---|---|---|
| ICIMOD HMAGLOFDB | v1.3.0 (RDS DOI 10.26066/RDS.1973283; zenodo.18257243; paper essd-15-3941) | RESOLVED — CC BY 4.0 governs (RDS declaration; Zenodo CC0 tag superseded, conservatively read as CC BY 4.0). Prior status 2026-09-16: UNRESOLVED — RDS CC BY 4.0 vs Zenodo CC0 | HMA 766 events 1533–2025; Nepal ~7.6% (~53) in v1.0 | paper/v1.0-era: day ~27% (±3d), month ~45%, year uncertain ~27% — v1.3 distribution PAYLOAD-GATED; `Sat_evidence` brackets | `Lat/Lon_lake` (within-lake, not breach) + `Lat/Lon_impact` (Observation/Deposit tag) | PAYLOAD-GATED — lake joins via `GL_ID`/`LakeDB_ID`/`G_ID` columns (GLM3-corrected — not `GF_ID`; coverage unverified until bytes; ICIMOD 2015 Koshi/Gandaki/Karnali; NSIDC HMA_GLI; Wang 2020 ESSD; zenodo.17948783) | CANDIDATE_ONLY (strongest candidate) |
| Recurrence note | — | — | `_Z` suffix on GF_ID (paper-reported; v1.3 semantics PAYLOAD-GATED); 23% of events from 3 ephemeral ice-dammed lakes | — | — | Group by base GF_ID per paper; exact semantics PAYLOAD-GATED | — |

## Ice/rock avalanche / glacier failure

| Source | Version/DOI | License | Coverage | Time precision | Notes | Posture |
|---|---|---|---|---|---|---|
| essd-2026-481 glacier-failure DB | zenodo.19477907/.19477908 | ESSD preprints CC BY 4.0; Zenodo field unconfirmed | Global excl. ice sheets: 502 events / 228 glaciers / 11 RGI v7 regions; Nepal included, count unstated | Day/month/year when available + Season + Min/Max-Date ranges | Discussion preprint (open review, Aug 2026); RGI v7 linkage gives non-event denominator | CANDIDATE_ONLY — not citable until peer review |
| Zhong et al. RIA inventory | zenodo.10080068; Geomorphology 109048 | Verify on record (superseded — see 2026-09-16 note: CC BY 4.0 tag, `access_right=restricted`) | 60 large HMA rock-ice avalanches, ≥1366 fatalities | Event dates in inventory | Source-slope characterization + cascade flag; static | CANDIDATE_ONLY |
| Kääb et al. 2021 detachments | tc-15-1751-2021 | CC BY | 20 detachments, no Nepal sites | — | Taxonomic gold standard, weak Nepal source | CANDIDATE_ONLY |
| Science China glacier-slope-failure inventory | — | Unverified | 727 events 1901–2019 | — | Machine-readable access unverified | CANDIDATE_ONLY |

## Landslide

| Source | Version/DOI | License | Coverage | Time precision | Non-event frame | Posture |
|---|---|---|---|---|---|---|
| USGS Gorkha (Roback/Clark/West) | 10.5066/F7DZ06F9; shapefiles 20170209 | USGS public domain | >30,000 km² Nepal Himalaya; 24,915 full / 24,795 source polygons | Exact trigger window (2015-04-25 mainshock + sequence) | GOOD — mapping extent + obscured-area flags | CANDIDATE_ONLY (seismic trigger class only) |
| Gnyawali & Adhikari Gorkha | 10.5066/F7028Q2X | CANDIDATE_ONLY (non-USGS authored) | Gorkha sequence | Sequence window — "sequence rather than single mainshock" | Moderate | CANDIDATE_ONLY |
| Burrows et al. timed monsoon set | zenodo.7970874; NHESS 22:2637 | CANDIDATE_ONLY (expected CC BY) | Nepal incl. western extension; 2015/17/18/19 monsoons | ~12-day window on ~30% of slides; rest untimed | Moderate — timed subset only | CANDIDATE_ONLY |
| Jones et al. 30-yr monsoon | NGDC items 166945/166966; Nat Comms 12:26964 | CANDIDATE_ONLY (NERC standard) | ~42,000 km² central-eastern Nepal; 12,838 polygons | Monsoon-slice window (~few months); 2011–12 unmapped (SLC-off) | GOOD at slice granularity | CANDIDATE_ONLY |
| ICIMOD RDS landslide sets | RDS.34425/.34426/rds.31016 | CC BY 4.0 verified on Koshi records | Koshi basin 1990/2010; 14 Gorkha districts | Epochal snapshots | Limited | CANDIDATE_ONLY |
| NASA COOLR GLC + HMA LS V002 | NSIDC-CPRD-HMA-LS-CAT-2 | Open + citation; downstream redistribution CANDIDATE_ONLY | Global/HMA ~2,800 events incl. Nepal | Day-class (report-derived) | POOR — report sampling conflates detection | CANDIDATE_ONLY |
| DesInventar/BIPAD | desinventar.net / bipadportal.gov.np | CANDIDATE_ONLY | 1971→ impact records | Day-class | POOR | CANDIDATE_ONLY |

## Dam breach / LDOF

| Source | Version/DOI | License | Coverage | Verdict |
|---|---|---|---|---|
| ICOLD WRD + incident pubs | icold-cigb.org | Paywalled 3-yr license | Global dam attributes; failures only in member publications | NOT open |
| Borealis worldwide failure DB | 10.5683/SP2/E7Z09B | Open Dataverse | 3,861 cases; Nepal engineered count unverified (expected ≤2) | CANDIDATE_ONLY |
| GRanD v1.3 / GDW | figshare.25988293 | CC BY | Dam locations incl. Kulekhani — attributes, not failures | Exposure layer only |
| Jiang et al. ESSD-2026-107 LDOF | zenodo.19198720 | CANDIDATE_ONLY (std CC-BY expected) | 902 global events to ~2020, breach params + quality flags; Himalayan belt dense | CANDIDATE_ONLY (preprint) |
| USGS OFR 91-239 | — | Public domain | 463 historical landslide dams, pre-1991 | Historical baseline only |
| Nepal engineered breaches | Reports only | — | Koshi embankment 1963/1971/1991/2008-08-18 (levee subclass, not storage dam) | No structured inventory located in bounded search (not a proof of absence) → engineered class `DEFERRED_NO_OPEN_TIMED_SOURCE` |

## Forecast / reforecast archives

| Source | Class | Span | Access | Verdict |
|---|---|---|---|---|
| TIGGE (ECDS/CMA) | ARCHIVED_OPERATIONAL ensembles | 2006→ | Registration **delayed 48 h**; **per-provider licenses incl. CC BY and CC BY-NC** | Candidate — metadata verified; issue-time retrieval + per-centre license still required |
| NCAR RDA ds084001 GFS 0.25° | ARCHIVED_OPERATIONAL | **2015-01-15 → 2025-05-28 (bounded archive, per official RDA page)** | Free, CC-BY-4.0 | Candidate — bounded span verified |
| NCEI NOMADS archive | ARCHIVED_OPERATIONAL | GEFS 2008–2020; GFS 1° 2005→ | Free | Candidate — cycle completeness unverified |
| GEFSv12 reforecast (AWS noaa-gefs-retrospective) | REFORECAST — **not** an archive of real-time operational runs | 2000–2019, 5–11 members | Free, no account | Candidate — reforecast only; does not prove operational availability |
| S2S database (ECDS) | REFORECAST + ARCHIVED real-time | 1981→ centre-dependent | Free; mixed licenses incl. CC BY-NC per centre | Candidate — per-centre license review required |
| C3S seasonal | ARCHIVED initialized | hindcast 1993–2016; RT 2017→ | Free | Candidate — seasonal context only |
| ECMWF MARS operational | ARCHIVED_OPERATIONAL | 1985→ | Service Agreement; €3k/yr; research fee waiver; Nepal non-member | Candidate — `BLOCKED_EXTERNAL` procurement |
| dynamical.org / Open-Meteo Single Runs | third-party archive | GEFS 2020→; IFS ENS 2024→ | Free/attribution | Secondary — no provider publication-time proof |
| ERA5 / ERA5-Land(+T) / IMDAA | REANALYSIS | 1940/1950→ | Free | Never forecast-skill evidence |
| ECMWF Open Data / NOMADS RT / IMD plots | CURRENT_FEED | ~12 runs / ~7 d rolling | — | NOT archives |

## Outstanding evidence items (block intake per vertical)

1. Zenodo `10895011` license tag; Everest-domain Nepal/China extent split.
2. HiAVAL Nepal event count (filter `Country=Nepal`) and machine-readable license file.
3. HMAGLOFDB — `payload_verification` PENDING (no bytes acquired);
   `nepal_v1_3_count` PENDING (v1.0 said ~53/7.6%; v1.3 count
   unverified until bytes); lake-ID join coverage;
   `opportunity_frame` PENDING-OWNER-POLICY (external lake-inventory
   linkage vs source-native frame — owner decision). License question
   RESOLVED — CC BY 4.0 governs (RDS declaration; Zenodo CC0 tag
   superseded, conservatively read as CC BY 4.0).
4. essd-2026-481 peer-review status; canonical Zenodo DOI (.907 vs .908); license.
5. zenodo.7970874 and NGDC 166966 license fields.
6. ESSD-2026-107 LDOF Nepal subset + license; Borealis Nepal engineered count.
7. DesInventar/BIPAD/DRR-portal formal redistribution terms.
8. ECMWF MARS fee-waiver application (procurement lead time, non-member state).
9. Per-cycle completeness spot-checks at first intake (requires account registration — post-P3).
