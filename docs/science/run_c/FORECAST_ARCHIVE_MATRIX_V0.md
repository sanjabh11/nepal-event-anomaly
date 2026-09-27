# Forecast Archive Matrix — v0

**Status:** `METADATA_REVIEW_COMPLETE` (metadata-only lane audit).
**Scope:** research only. No archive was downloaded, no credentials were
used, and no forecast was executed. Every provider below is
`CANDIDATE_ONLY` until its evidence items are verified at intake. This
document evaluates *which archives could ever satisfy* the
`ForecastVintageV0` / `CutoffRecordV0` contract — it authorizes no
intake, freeze, or claim.

**Binding contracts:** `nepal/research_v0/records.py`
(`ForecastVintageV0`, `CutoffRecordV0`, `ForecastExperimentV0`),
`nepal/research_v0/policy.py` (`ForecastDataClass`, `CUTOFF_ORDER`),
and `docs/science/INFORMATION_CUTOFF_TARGET_POLICY_V0.md` §7
(forecast-data classes) and §1 (issue-time ≠ publication-time).

## 1. Gate model

A provider row is admissible as forecast evidence only if, at intake,
all of the following can be produced:

1. `data_class ∈ {REFORECAST, ARCHIVED_OPERATIONAL}`. `REANALYSIS` and
   `CURRENT_FEED` are structurally inadmissible for forecast skill.
2. Every `ForecastVintageV0` field can be populated and byte-bound:
   `vintage_id`, `provider`, `data_class`, `initialization_time`,
   `issue_time`, `valid_start`, `valid_end`, `archive_availability`,
   `archive_payload_sha256` + `archive_payload_path`,
   `retrieval_record_sha256` + `retrieval_record_path`,
   `model_version`, `license_id`, `archive_mechanism`.
3. The cutoff chain is satisfiable per retrieved unit:
   `source_observation_end <= source_processing_complete
   <= source_publication <= feature_availability
   <= forecast_initialization <= forecast_issue
   <= forecast_valid_start <= forecast_valid_end`, with
   `archive_availability >= forecast_issue` and
   `local_retrieval_time >= archive_availability`.
4. Because no provider archives *when an object became publicly
   retrievable*, every vintage carries a preregistered conservative
   issue+dissemination latency margin (policy §1). Provider access
   delays (48 h, 1 week) are floors for that margin, not the margin
   itself — the margin also covers index lag, mirror lag, and
   retrieval-observation uncertainty.
5. License permits research use and retention of a retrieval record.
   `CC BY-NC` terms are compatible with research-only use but bar any
   commercial pathway and are recorded verbatim in `license_id`.
6. Cycle completeness and per-cycle member counts are verified at
   intake by spot-check — matrix metadata is not completeness proof
   (B28: `GATED_ON_DATA`).

## 2. Required evidence fields per retrieved vintage

| Field | Source on provider side | Fallback when absent |
|---|---|---|
| `initialization_time` | init cycle in GRIB keys / filename / MARS `date+time` | reject vintage |
| `valid_start` / `valid_end` | init + step bounds, or product valid-time metadata | derive only from declared step convention; record derivation in retrieval record |
| `issue_time` | provider dissemination documentation + preregistered latency margin | reject vintage — object timestamps (e.g. S3 `Last-Modified`) are never issue evidence |
| `archive_availability` | verified retrievability at intake + delay floor | `issue_time + delay_floor` conservative bound |
| `model_version` | cycle model version (e.g. GEFSv12, IFS Cy49r1) | reject vintage — version drift must be declared per span |
| `archive_payload_*` | sha256 + path of the exact retrieved bytes under `evidence_root` | mandatory; no metadata-only binding |
| `retrieval_record_*` | sha256 + path of request JSON, request/response timestamps, byte counts | mandatory; provenance of *this* retrieval, not historical availability |
| `license_id` | provider licence page as of `evidence_as_of` | reject vintage |

## 3. Provider matrix

### 3.1 ECMWF TIGGE (ECDS / MARS TIGGE catalogue; mirrors: CMA portal, NCAR RDA)

| Attribute | Value (metadata-verified 2026-09-15; re-verify at intake) |
|---|---|
| `data_class` | `ARCHIVED_OPERATIONAL` (ensemble) |
| Access mode | ECDS (CDS-API) or MARS TIGGE catalogue after registration + licence acceptance; CMA portal mirror; NCAR RDA mirror (TIGGE collection — verify identifier at intake). ECDS migration completed 2026-05-27. |
| Licence/terms | TIGGE licence (rev. 2), per-provider — verified 2026-09-15 (`run_b/SOURCE_EVIDENCE_ADDENDUM_V0.md`): **CC BY 4.0** for DWD, ECCC, ECMWF, KMA, NCEP, UKMO; **CC BY-NC 4.0** for BoM, CMA, CPTEC, IMD, JMA, MF, NCMRWF; ECMWF TIGGE data additionally governed by ECMWF Terms of Use. Licence id must be recorded per centre per vintage; the CMA-portal path is CC BY-NC (non-commercial). Research-only use is compatible. |
| issue/init/valid/vintage fields | init cycle + step in GRIB keys / MARS keys; valid = init + step. No provider issue-time field → conservative margin rule (§1 gate 4). |
| Public-availability delay | **48 h after initialization** (TIGGE usage licence; verified). |
| Cycle completeness | Unverified — spot-check required per cycle/centre at intake. |
| Member counts | Centre-dependent; representative ranges: ECMWF ~51, NCEP ~21, CMC ~21, JMA ~27–51, DWD ~40, UKMO ~18–36, others smaller. Verify per centre at intake. |
| Spatial coverage over Nepal | Global grids (~0.25°–1.5° by centre). Spatially complete; terrain resolution is far coarser than Himalayan orography — a declared limitation, not a coverage gap. |
| Retrieval evidence required | ECDS/MARS request JSON, response manifest, retrieved GRIB bytes + sha256, licence acceptance timestamp, centre + cycle + member completeness check. |
| **Verdict** | `CANDIDATE_ONLY` — strongest multi-model ensemble archive; gate items: registration (post-P3), per-centre licence capture, cycle completeness, issue-time margin. |

### 3.2 S2S database (ECDS / MARS S2S catalogue)

| Attribute | Value |
|---|---|
| `data_class` | `REFORECAST` + `ARCHIVED_OPERATIONAL` (real-time legs) — keep legs as separate vintage records |
| Access mode | ECDS (CDS-API) or MARS S2S catalogue after registration + S2S licence acceptance; ECDS migration completed 2026-05-27. |
| Licence/terms | Per-provider — verified 2026-09-15 (`run_b/SOURCE_EVIDENCE_ADDENDUM_V0.md`): **CC BY 4.0** for ECCC, ECMWF, KMA, NCEP, HMCR, UKMO; **CC BY-NC 4.0** for BoM, CMA, CNR-ISAC, CNRM, CPTEC, IAP-CAS, JMA. Licence id must be recorded per centre per vintage. |
| issue/init/valid/vintage fields | init + step in keys; valid = init + step; reforecast legs carry fixed model-version dates. No provider issue-time field → conservative margin rule. |
| Public-availability delay | Real-time legs: **48 h** for CMA, CNR-ISAC, CPTEC, ECCC, ECMWF, HMCR, IAP-CAS, JMA, KMA, NCEP; **1 week** for BoM, CNRM, UKMO (verified 2026-09-15, addendum). **Reforecasts unrestricted** — licence text: "The reforecasts can be accessed without any restrictions." |
| Cycle completeness | Real-time legs since ~Jan 2015, centre-dependent; unverified — spot-check at intake. Reforecast cadence varies by centre (fixed schedules, e.g. monthly 4–6 inits). |
| Member counts | Centre-dependent, **4–101** real-time; reforecast sizes smaller (e.g. 7+1 to 32+1). Verify per centre at intake. |
| Spatial coverage over Nepal | Common 1.5° lat/lon archive grid — complete but very coarse over Himalayan terrain. |
| Retrieval evidence required | As §3.1, plus per-centre licence id and delay class, and separation of real-time vs reforecast legs into distinct vintages. |
| **Verdict** | `CANDIDATE_ONLY` — best subseasonal (7d–30d) ensemble evidence; coarse grid and centre-dependent licence/delay/frequency are the gate items. |

### 3.3 NCEP GEFSv12 reforecast (AWS `noaa-gefs-retrospective`)

| Attribute | Value |
|---|---|
| `data_class` | `REFORECAST` — fixed-model hindcast; **not** an archive of operational issue-time runs and cannot prove operational availability (policy §7). |
| Access mode | Public AWS S3 bucket, anonymous HTTPS; also NOAA `rzdm` FTP mirror (bandwidth-limited). No account required. |
| Licence/terms | NOAA NODD open-data terms: public use with attribution requested; no endorsement implication. Compatible with research use. **Classification note:** NODD rolling buckets are unofficial current-feed mirrors — they cannot serve as `ARCHIVED_OPERATIONAL` evidence; only the fixed reforecast product qualifies this row. |
| issue/init/valid/vintage fields | 00 UTC init in path `GEFSv12/reforecast/YYYY/YYYYMMDD00/`; valid = init + step. `issue_time` is not operational — record the *notional* issue as `init + preregistered margin` and the data class as `REFORECAST`. |
| Public-availability delay | N/A for reforecast (fixed retrospective product). |
| Cycle completeness | Daily 00Z, 2000-01-01 → 2019-12-31; directory-tree structure makes completeness mechanically checkable at intake. |
| Member counts | **5 members daily** (`c00` + `p01`–`p04`); **11 members once weekly** (extends to +35 days). |
| Spatial coverage over Nepal | Global sub-degree grid (~0.5° class; verify exact output grid at intake). |
| Retrieval evidence required | Object key list, byte digests, retrieval timestamps, member/cycle completeness check, model version string `GEFSv12`. |
| **Verdict** | `CANDIDATE_ONLY` — **closest to passing all gates** for the `REFORECAST` lane: zero access friction, permissive terms, fixed model version, mechanically checkable completeness. Limitations: 5-member daily ensemble; 2000–2019 span; cannot establish operational issue-time semantics. |

### 3.4 NCAR RDA ds084001 — GFS 0.25° GRIB2 archive

| Attribute | Value |
|---|---|
| `data_class` | `ARCHIVED_OPERATIONAL` (deterministic) |
| Access mode | Free NCAR RDA web/HTTPS after registration; subset services available. |
| Licence/terms | **CC BY 4.0** (verified 2026-09-15, `run_b/SOURCE_EVIDENCE_ADDENDUM_V0.md`). |
| issue/init/valid/vintage fields | 00/06/12/18Z cycles, init + forecast hour in filename/keys; valid = init + step. No issue-time field → conservative margin rule. |
| Public-availability delay | No declared delay; retrieval latency margin still required (§1 gate 4). |
| Cycle completeness | **RDA-listed span: 2015-01-15 → 2026-10-02** (per official RDA page; early-2026 freeze/AWS-migration caveat — the listed end is a listing claim, not retrieved evidence; tail-cycle availability PAYLOAD-GATED). Cycle-level completeness spot-check required at intake. |
| Member counts | Deterministic — 1 member (no ensemble). |
| Spatial coverage over Nepal | 0.25° global — best resolution in this matrix; still coarse vs. Himalayan orography. |
| Retrieval evidence required | RDA request record, GRIB bytes + sha256, cycle completeness check, licence capture. |
| **Verdict** | `CANDIDATE_ONLY` — strongest deterministic archive and best grid; single-member and tail-cycle availability (early-2026 freeze/AWS migration) are the gate items. |

### 3.5 NCEI NOMADS model archive (GEFS / GFS)

| Attribute | Value |
|---|---|
| `data_class` | `ARCHIVED_OPERATIONAL` |
| Access mode | Free HTTPS/FTP from NCEI; no account. |
| Licence/terms | US government work, attribution requested — treat as open; capture terms at intake. |
| issue/init/valid/vintage fields | init + forecast hour; valid = init + step. No issue-time field → conservative margin rule. |
| Public-availability delay | No declared delay; margin rule applies. |
| Cycle completeness | GEFS official archive 2008-01-01 → 2020-09-23 (post-2020 GEFS is NODD-only — `CURRENT_FEED` class, not `ARCHIVED_OPERATIONAL` evidence); GFS 1° ~2005→. Completeness unverified — spot-check at intake. |
| Member counts | GEFS ~21 members (era-dependent); verify per span. |
| Spatial coverage over Nepal | 0.5°–1° global grids. |
| Retrieval evidence required | Request record, bytes + sha256, per-span `model_version` (GEFS v10/v11/v12 drift across the span must be declared per vintage). |
| **Verdict** | `CANDIDATE_ONLY` — usable secondary archive; model-version drift and completeness are the gate items. |

### 3.6 C3S seasonal forecasts (CDS)

| Attribute | Value |
|---|---|
| `data_class` | `ARCHIVED_OPERATIONAL` (initialized seasonal) |
| Access mode | Free CDS after registration. |
| Licence/terms | Copernicus licence (permissive); capture per centre at intake. |
| issue/init/valid/vintage fields | Monthly init + monthly-step valid windows. |
| Public-availability delay | Centre-dependent publication lag; margin rule applies. |
| Cycle completeness | Hindcast 1993–2016 + real-time 2017→; completeness spot-check at intake. |
| Member counts | Multi-centre, ~10–50 per system. |
| Spatial coverage over Nepal | ~1° global. |
| Retrieval evidence required | As above, plus declared monthly-init semantics. |
| **Verdict** | `CANDIDATE_ONLY` — admissible only for declared seasonal-context analyses; monthly-init granularity cannot serve 6h–72h occurrence targets. |

### 3.7 ECMWF MARS operational archive

| Attribute | Value |
|---|---|
| `data_class` | `ARCHIVED_OPERATIONAL` |
| Access mode | Service Agreement (~€3k/yr); research fee waiver possible; Nepal is a non-member state — procurement lead time. |
| **Verdict** | `BLOCKED_EXTERNAL` — procurement; revisit only if waiver is granted. |

### 3.8 Third-party mirrors (dynamical.org, Open-Meteo Single Runs)

| Attribute | Value |
|---|---|
| `data_class` | Corresponds to `ARCHIVED_OPERATIONAL` content but with **no provider-side publication-time proof**. |
| Licence/terms | Free/attribution; verify per mirror. |
| **Verdict** | Secondary evidence only — may corroborate payload bytes; can never satisfy `archive_availability`/`issue_time` alone. |

### 3.9 Dedicated Himalayan / CD-persistent reforecast

Bounded search located **no dedicated Himalayan reforecast archive**.
IMD operational outputs are `CURRENT_FEED` only (no public archive);
IITM/IMD hindcast products are not openly machine-readable archives
(unverified — not a proof of absence); **IMDAA is `REANALYSIS`** and
inadmissible for forecast skill. Verdict:
`DEFERRED_NO_OPEN_TIMED_SOURCE` — any S2S/TIGGE/GEFSv12 cell over the
Himalayan domain is the admissible substitute.

### 3.10 Structurally excluded classes

| Class | Members | Rule |
|---|---|---|
| `REANALYSIS` | ERA5, ERA5-Land, ERA5T, IMDAA | Retrospective regime path only; **never** scored as forecast skill; rejected by `ForecastVintageV0` and the forecast feature gate. |
| `CURRENT_FEED` | ECMWF Open Data, NOMADS rolling, NOAA NODD rolling buckets (current-feed mirrors, incl. post-2020 material), IMD plots | Not historical archives — unofficial/rolling open-data mirrors are excluded from `ARCHIVED_OPERATIONAL` evidence and rejected by `ForecastVintageV0` for forecast-skill claims. |

## 4. Consolidated gate table

| Provider | data_class | Access friction | Licence gate | Issue-time evidence | Completeness | Ensemble | Nepal coverage | Composite verdict |
|---|---|---|---|---|---|---|---|---|
| GEFSv12 reforecast | REFORECAST | none (anonymous) | open | notional only | mechanically checkable | 5 daily / 11 weekly | ~0.5° | **Closest to passing all gates** (reforecast lane) |
| TIGGE (ECDS/MARS) | ARCHIVED_OPERATIONAL | registration | verified per-centre CC BY 4.0 / CC BY-NC 4.0 | margin rule | spot-check needed | 4–51 multi-centre | 0.25°–1.5° | `CANDIDATE_ONLY` — strongest ensemble archive; registration + per-centre licence gate |
| S2S | REFORECAST + ARCHIVED_OPERATIONAL | registration | verified per-centre CC BY 4.0 / CC BY-NC 4.0 | margin rule | spot-check needed | 4–101 | 1.5° | Best subseasonal lane; coarse grid |
| NCAR ds084001 | ARCHIVED_OPERATIONAL | registration | CC BY 4.0 | margin rule | bounded span, spot-check | 1 (deterministic) | 0.25° | Strongest deterministic archive |
| NCEI NOMADS | ARCHIVED_OPERATIONAL | none | open | margin rule | spot-check needed | ~21 | 0.5°–1° | Secondary archive; version drift declared |
| C3S seasonal | ARCHIVED_OPERATIONAL | registration | permissive | margin rule | spot-check needed | ~10–50 | ~1° | Seasonal context only |
| ECMWF MARS operational | ARCHIVED_OPERATIONAL | paid/waiver | agreement | provider semantics | full | full | full | `BLOCKED_EXTERNAL` |
| Third-party mirrors | (secondary) | none | attribution | **none** | unverified | varies | varies | Corroboration only |
| Himalayan dedicated | — | — | — | — | — | — | — | `DEFERRED_NO_OPEN_TIMED_SOURCE` |

## 5. Retrieval evidence checklist (intake artifact requirements)

Every retrieved vintage must produce, under `evidence_root`:

1. `archive_payload_path` + `archive_payload_sha256` — exact bytes.
2. `retrieval_record_path` + `retrieval_record_sha256` — request JSON,
   UTC request/response timestamps, endpoint, centre, cycle, members,
   bytes received, HTTP status, retry history.
3. Licence capture — licence id + acceptance timestamp + licence page
   snapshot digest.
4. Cycle/member completeness check — expected vs. received inventory.
5. `model_version` declaration; any mid-span version change splits
   vintages.
6. Issue-time margin declaration — the preregistered
   issue+dissemination latency margin used for `issue_time` and
   `archive_availability` (delay floors: TIGGE 48 h; S2S 48 h or 1 week
   by centre; others: provider-documented or conservative bound).

## 6. Verdict summary

- **Closest to passing all gates:** **GEFSv12 reforecast** for the
  `REFORECAST` experiment lane (no access friction, permissive terms,
  fixed model version, mechanically checkable completeness), with the
  standing limitation that reforecast evidence cannot demonstrate
  operational issue-time availability.
- **Closest `ARCHIVED_OPERATIONAL` candidates:** **TIGGE** (ensemble,
  multi-centre) and **NCAR ds084001** (deterministic, 0.25°), both
  pending registration, licence capture, completeness spot-checks, and
  issue-time margin declaration at intake.
- Licence evidence is now metadata-verified for TIGGE, S2S, and
  ds084001 (`run_b/SOURCE_EVIDENCE_ADDENDUM_V0.md`, retrieved
  2026-09-15). This lifts the licence-evidence item only:
  registration, per-centre licence binding to the actual centre used,
  cycle completeness, per-cycle issue-time retrievability, and
  historical availability remain unresolved and intake-gated.
- No provider currently satisfies all gates from metadata alone; every
  row remains `CANDIDATE_ONLY`.
- Nothing in this document authorizes downloads, scoring, or any
  operational use. `warning_path_authorized=false`;
  `production_authorized=false`; `promotion_eligible=false`.
