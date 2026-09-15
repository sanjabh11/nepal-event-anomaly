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

## Snow avalanche

| Source | Version/DOI | License | Coverage | Time precision | Spatial semantics | Non-event frame | Posture |
|---|---|---|---|---|---|---|---|
| HiAVAL | v1.3.0 = zenodo.18257425 (concept 10.5281/zenodo.7066940); NHESS 23:2569 | **UNRESOLVED** — README/Zenodo CC BY 4.0 vs repo-level CC0 tag | HMA, 8 countries, 681+ events 1972–2025; Nepal event count not stated | 95% day, 97% month, 1 unknown-year | Lat/lon 97% — "general mountain slope," NOT release/runout | None — reporting-effort biased | CANDIDATE_ONLY |
| Kneib et al. Sentinel-1 deposits | zenodo.10895011; TC 18:2809 | **UNRESOLVED** — Zenodo record license tag unconfirmed | Everest region (Nepal/China split unverified), Hispar, Mt Blanc; glacier surfaces only | Scene-interval (6–12 d), never release time | Deposit polygons, 10 m class; `Automated_outlines_dates` + `_ManualUpd` variants | YES — `Sentinel1_date` file enumerates every analyzed scene pair | CANDIDATE_ONLY |
| SAFE-HMA | figshare.28869170 v3; Earth's Future e2025EF006503 | CC BY 4.0 | All HMA, ~60M deposits / 10,701 catchments; Nepal in Ganga–Brahmaputra tile | YEAR-class (annual presence count) | 30 m raster deposit/runout zones | Partial — annual binary presence, detection-conditional | CANDIDATE_ONLY |
| Nepal DRR Portal (MoHA) | drrportal.gov.np | Bulk-export terms undocumented | National, incident log | Day-class | Admin unit + place text | None — impact-triggered | CANDIDATE_ONLY |
| DesInventar Nepal | desinventar.net npl | CANDIDATE_ONLY | 1971+; AVALANCHE + SNOW STORM types | Day-class | Admin units | None | CANDIDATE_ONLY |
| EM-DAT | public.emdat.be | CC-BY-NC-ND (no derivatives — weakest) | Global; high inclusion threshold | Day-class | Admin/text geocode | None | CANDIDATE_ONLY |
| AvalCD (zenodo.15863589) | — | CC BY-NC 4.0 | Tajikistan HMA; no Nepal | — | — | — | REJECTED for Nepal pilot |

## GLOF

| Source | Version/DOI | License | Coverage | Time precision | Spatial semantics | Non-event frame | Posture |
|---|---|---|---|---|---|---|---|
| ICIMOD HMAGLOFDB | v1.3.0 (RDS DOI 10.26066/RDS.1973283; zenodo.18257243; paper essd-15-3941) | **UNRESOLVED** — RDS CC BY 4.0 vs Zenodo CC0 | HMA 766 events 1533–2025; Nepal ~7.6% (~53) in v1.0 | Day ~27% (±3d), month ~45%, year uncertain ~27%; `Sat_evidence` brackets | `Lat/Lon_lake` (within-lake, not breach) + `Lat/Lon_impact` (Observation/Deposit tag) | GOOD — GF_ID joins lake inventories (ICIMOD 2015 Koshi/Gandaki/Karnali; NSIDC HMA_GLI; Wang 2020 ESSD; zenodo.17948783) | CANDIDATE_ONLY (strongest candidate) |
| Recurrence note | — | — | `_Z` suffix on GF_ID; 23% of events from 3 ephemeral ice-dammed lakes | — | — | Must group by base GF_ID | — |

## Ice/rock avalanche / glacier failure

| Source | Version/DOI | License | Coverage | Time precision | Notes | Posture |
|---|---|---|---|---|---|---|
| essd-2026-481 glacier-failure DB | zenodo.19477907/.19477908 | ESSD preprints CC BY 4.0; Zenodo field unconfirmed | Global excl. ice sheets: 502 events / 228 glaciers / 11 RGI v7 regions; Nepal included, count unstated | Day/month/year when available + Season + Min/Max-Date ranges | Discussion preprint (open review, Aug 2026); RGI v7 linkage gives non-event denominator | CANDIDATE_ONLY — not citable until peer review |
| Zhong et al. RIA inventory | zenodo.10080068; Geomorphology 109048 | Verify on record | 60 large HMA rock-ice avalanches, ≥1366 fatalities | Event dates in inventory | Source-slope characterization + cascade flag; static | CANDIDATE_ONLY |
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
3. HMAGLOFDB v1.3.0 Nepal count; lake-ID join coverage; CC-BY vs CC0 resolution.
4. essd-2026-481 peer-review status; canonical Zenodo DOI (.907 vs .908); license.
5. zenodo.7970874 and NGDC 166966 license fields.
6. ESSD-2026-107 LDOF Nepal subset + license; Borealis Nepal engineered count.
7. DesInventar/BIPAD/DRR-portal formal redistribution terms.
8. ECMWF MARS fee-waiver application (procurement lead time, non-member state).
9. Per-cycle completeness spot-checks at first intake (requires account registration — post-P3).
