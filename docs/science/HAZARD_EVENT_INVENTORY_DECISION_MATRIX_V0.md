# Hazard & Event-Inventory Decision Matrix — v0

**Status:** `DESIGN_DRAFT_COMPLETE` — pending human approval (P3 gate).
**Scope:** research only. This document authorizes nothing: no download,
no FMX freeze, no clustering, no operational, warning, production, or
scientific-validation claim.
**Companion:** `INFORMATION_CUTOFF_TARGET_POLICY_V0.md` (timestamp/target
rules), `SOURCE_FEASIBILITY_RECORDS_V0.md` (full per-source metadata),
`GAP_REGISTER_V0.md` (gap closure status).

Every source below is `CANDIDATE_ONLY` unless its exact version, license,
geography, fields, and timing semantics are independently verified at
intake. Event counts are inventory-fitness evidence only — never skill.

## Vertical ontology (G03)

Four independent verticals with explicit exclusions. Compound and cascade
relations are recorded as `parent/child/cascade` groupings, never merged
into one label.

| Vertical | Physical event unit | Explicit exclusions | Compound-event relations |
|---|---|---|---|
| `snow_avalanche` | Snow release/deposit event | Ice-rock avalanche, glacier detachment, GLOF, debris flow, dam breach | May feed slushflow/LDOF cascades; grouped, not merged |
| `ice_rock_avalanche` | Source-slope ice-rock failure or glacier detachment | Pure snow avalanche; downstream flood/impact is a separate label | Parent of GLOF/LDOF/debris cascades (e.g., Seti 2012) |
| `glof` | Glacial-lake outburst/release at the source lake | Downstream arrival, damage, exposure (separate labels) | Child of slope failures into lakes; parent of downstream flood |
| `landslide_rainfall` | Slope initiation, rainfall/monsoon trigger | Coseismic-only class is a separate trigger label | Parent of LDOF and debris-flow cascades |
| `landslide_coseismic` | Slope initiation, earthquake trigger | Rainfall-triggered slides | Trigger-specific; not usable for rainfall forecast pilots |
| `ldof` | Natural landslide-dam formation/breach outburst | Engineered dam failure | Child of `landslide_*`; own breach-timing semantics |
| `dam_breach_engineered` | Breach of an engineered structure | Natural dams; levee/embankment breaches are a subclass | Downstream flood is separate label |

## Decision matrix

| Vertical | Candidate source posture | Main timing/spatial limitation | Required controls | Decision |
|---|---|---|---|---|
| Snow avalanche | **Kneib et al. 2024 (TC) Sentinel-1 Everest deposit dataset** (Zenodo `10.5281/zenodo.10895011`): 5-yr window (11/2017–10/2022), polygon deposits, ~6–12-day scene intervals, explicit per-scene `Sentinel1_date` observation-opportunity file → principled negatives; license **UNRESOLVED** (Zenodo record tag unconfirmed). **HiAVAL v1.3.0** (Zenodo `10.5281/zenodo.18257425`; license **UNRESOLVED**: README/Zenodo CC BY 4.0 vs repo-level CC0 tag — stays `CANDIDATE_ONLY` until written resolution): 681+ events HMA-wide, ~95% day precision, but slope-generalized coordinates and reporting-effort bias, no non-event frame. **SAFE-HMA** (figshare `10.6084/m9.figshare.28869170`, CC BY 4.0): annual deposit frequency 1990–2023, YEAR-class only. | Scene-interval labels bracket releases (never timestamps); ≤~4,000 m² size floor; radar shadow/layover; wet-snow false positives; HiAVAL coords are not release points; no Nepal event count stated | `Sentinel1_date` opportunity frame; scene-quality controls; event-free covered scenes; no absence-as-negative; HiAVAL can *narrow* event time inside S1 intervals | `PILOT_CANDIDATE_PENDING_EVIDENCE` — pending: Zenodo license tag; Nepal/China extent split of Everest domain; HiAVAL Nepal subset count |
| Ice/rock avalanche | **essd-2026-481** global glacier-failure DB (502 events, RGI v7 linkage, Min/Max-Date fields) — discussion preprint, not peer-reviewed → `CANDIDATE_ONLY`. **Zhong et al. 2024** (zenodo.10080068): 60 large HMA RIAs, published, static. **HiAVAL** ice subset; mountaineering events segregated in `HiAVALclimDB.csv`. Kääb 2021 detachments: no Nepal sites. | Event-time and source-location uncertainty; taxonomy mixes detachments/ice/rock-ice/compound; strong reporting bias pre-2010 | Independent source adjudication; cascade grouping; basin holdouts; RGI v7 glacier population as non-event denominator | `DEFERRED_NO_OPEN_TIMED_SOURCE` for forecast pilot unless essd-2026-481 completes peer review AND passes license/timing/non-event gates; descriptive use only meanwhile |
| GLOF | **ICIMOD HMAGLOFDB v1.3.0** (RDS DOI `10.26066/RDS.1973283`; paper `10.5194/essd-15-3941-2023`): 766 events 1533–2025; license **UNRESOLVED**: RDS states CC BY 4.0, Zenodo record shows CC0 — stays `CANDIDATE_ONLY` until written resolution; ~53 Nepal events in v1.0 (7.6%); day precision ~27%, month ~45%, year uncertain ~27%; explicit lake-point vs impact-point fields; `_Z` recurrence suffix (23% of events from 3 ephemeral lakes); joinable to ICIMOD/RGI lake inventories for non-event frames. | Only ~27% exact-day (±3d caveat); lake point ≠ breach point; recurrence conflation risk; remote-area undercount | Lake-level opportunity frame via HMA lake inventories; event-free lake periods; recurrence grouping by base `GF_ID`; basin holdouts | `PILOT_CANDIDATE_PENDING_EVIDENCE` — nearest candidate; pending: Nepal count in v1.3.0, lake-ID join coverage, CC-BY/CC0 resolution, horizon feasibility at ~27% day precision |
| Landslide (rainfall) | **Burrows et al.** (Zenodo `10.5281/zenodo.7970874`; NHESS 2022): Sentinel-1-constrained timing to ~12-day window for ~30% of monsoon slides, 2015/17/18/19. **Jones et al. 2021** (NGDC 166966): 12,838 polygons, 29 monsoon slices 1988–2018, season-window class. **NASA COOLR/HMA LS V002**: day-class but report-sampled, impact-biased. ICIMOD Koshi/14-district sets: epochal, partial coverage. | No open Nepal source gives exact-day rainfall-trigger labels; best is 12-day window on a 30% subset; annual/monsoon-slice elsewhere | Image-opportunity frame; trigger-class separation; timed-subset honesty (70% untimed must be censored) | Descriptive/coarse-window research only — `PILOT_CANDIDATE_PENDING_EVIDENCE` at ≥30-day horizon pending license checks |
| Landslide (coseismic) | **USGS Gorkha** (`10.5066/F7DZ06F9`, public domain): 24,915 polygons, source vs full area separated, documented mapping extent + obscured-area flags → defensible negatives. Exact trigger window (2015-04-25 06:11 UTC + aftershock sequence). | Single trigger sequence; per-feature dates not stored | Trigger-specific controls; obscured-area mask | Right labels, wrong trigger class for a rainfall forecast pilot — usable as seismic-trigger labels or adjacent-class controls only |
| LDOF (natural dam) | **Jiang et al. ESSD-2026-107** global landslide-dam DB (902 events, breach params, quality flags; zenodo.19198720 — preprint); USGS OFR 91-239 (463, pre-1991); Nepal events from reports (Sunkoshi/Jure 2014 breach 7 Sep; Melamchi 2021) — no curated Nepal LDOF inventory. | Day-to-month precision; news/literature-derived | Dam-formation vs breach timing kept separate; event-free river-reach frames | `PILOT_CANDIDATE_PENDING_EVIDENCE` pending ESSD-2026-107 license/Nepal-subset verification |
| Dam breach (engineered) | **No qualifying open Nepal inventory located in a bounded search** — this is not a proof of absence. ICOLD paywalled; Borealis global failure DB (3,861 cases, `10.5683/SP2/E7Z09B`) likely ≤2 Nepal engineered cases (unverified); GRanD/GDW = exposure layer only. Nepal's documented engineered breaches are levee/embankment failures (Koshi 2008-08-18) from reports, not inventories. | No open timed source located | — | `DEFERRED_NO_OPEN_TIMED_SOURCE` (search-bounded, not proven absent) |

## Forecast-source feasibility (P0 gate G14)

A forecast pilot requires archived issue-time forecast or reforecast data
with init/issue/valid/vintage metadata. Verified candidates (metadata
only — full records in `SOURCE_FEASIBILITY_RECORDS_V0.md`):

| Tier | Source | Class | Span | Access |
|---|---|---|---|---|
| Ensemble archive | TIGGE (ECDS/CMA) | ARCHIVED_OPERATIONAL ensembles | 2006→ | Registration **delayed 48 h**; **per-provider licensing incl. CC BY and CC BY-NC** — verify per centre before redistribution |
| GFS archive | NCAR RDA ds084001 | ARCHIVED_OPERATIONAL | **2015-01-15 → 2025-05-28 (bounded, not open-ended)**; NCEI NOMADS GEFS 2008–2020 | Free, CC-BY-4.0 |
| Reforecast | GEFSv12 (AWS `noaa-gefs-retrospective`) + S2S | REFORECAST — fixed-model hindcast, **NOT an archive of real-time operational runs** | 2000–2019; 1981→ centre-dependent | Free |
| Gold standard, gated | ECMWF MARS | ARCHIVED_OPERATIONAL | 1985→ | €3k/yr; research fee waiver; Nepal non-member → lead-time risk |
| Seasonal | C3S seasonal + S2S real-time | ARCHIVED initialized | hindcast 1993–2016; RT 2017→ | Free; per-centre licenses mixed incl. CC BY-NC |
| Never skill evidence | ERA5, ERA5-Land(+T), IMDAA | REANALYSIS | 1940/1950→ | Retrospective regime path only |
| Not archives | ECMWF Open Data (~12 runs), NCEP NOMADS (~7d), IMD plots | CURRENT_FEED | rolling | Cannot supply historical vintages |

Every archive-class row is **metadata-verified only**: issue-time
retrievability, cycle completeness, and availability latency still
require per-experiment verification at intake (G14).

**Known epistemic gap:** no provider archives *public-retrievable* time;
`referenceTime` proves issue, not availability. Consistent with the prior
GFS adjudication (`MATERIAL_HISTORICAL_LEAKAGE_RISK`), all archived
vintages must apply a preregistered conservative issue+dissemination
latency margin rather than trusting object timestamps.

## Pilot-selection gate

A vertical reaches `PILOT_GATE_PASSED` only when **all** hold:

1. Exact source version and redistribution terms frozen.
2. ≥5 eligible event groups and ≥2 independent basin groups (inventory
   fitness only).
3. ≥3 disjoint geographic groups for train/validation/locked test.
4. Event-time precision + verified observation latency supports ≥1
   predeclared horizon per the policy table.
5. Source-independent or independently sampled non-event frame exists.
6. Archived forecasts/reforecasts provide issue/init/valid/vintage
   metadata.
7. Event, control, basin, and cascade assignments frozen before
   eligibility filtering.
8. No Langtang single-box validation, B priority feature, exposure
   feature, or prior K/performance transfer.
9. Human approval exists for both v0 artifacts.

**Tie-break order:** (1) lowest timing uncertainty + observation latency;
(2) strongest independent non-event frame; (3) reproducible archived
forecast availability; (4) license certainty; (5) basin diversity;
(6) event count.

**Current outcome:** `NO_QUALIFYING_PILOT_SOURCE`. GLOF and snow
avalanche are the nearest candidates, each blocked on named evidence
items in the table above. Nothing here pre-nominates a vertical.
