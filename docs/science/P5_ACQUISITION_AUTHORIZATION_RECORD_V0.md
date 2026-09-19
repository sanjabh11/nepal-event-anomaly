# P5 Acquisition Authorization Record — v0

> **Status: `EXECUTED — owner-directed in-session attestation,
> 2026-09-19` — bounded retrieval authorized for the scope below only.**
> This record adopts the field list of the shared draft
> `/tmp/P5_ACQUISITION_AUTHORIZATION_DRAFT_V0.md` (there is no second,
> competing field list). Every machine-decidable field below is filled
> and evidence-bound; the owner-only fields were supplied by the
> repository owner on 2026-09-19 via in-session instruction and are
> recorded verbatim. The attestation is owner-directed: **no
> independent third-party or cryptographic signature is claimed** —
> the same channel accepted in `P3_ATTESTATION_RECORD_V0.md`.
>
> A completed P5 authorizes **bounded retrieval only**: it is not a
> pilot selection, not a source qualification, not `EVIDENCE_VERIFIED`,
> and not an operational approval. All authority flags remain false:
> `warning_path_authorized=false`, `production_authorized=false`,
> `promotion_eligible=false`.
>
> Template reference: `docs/science/P3_ATTESTATION_TEMPLATE.md`
> sha256 `a34a1d433233636c13c6eb1fc63600041eeeabb6e2ea820c7d3e71432edf99f9`
> (recomputed 2026-09-19 from live bytes — matches the shared draft's
> recorded value).

---

## 0. Relationship to the existing P5-C record

`docs/science/P3_ATTESTATION_RECORD_V0.md` §"P5-C bounded-acquisition
authorization" authorizes exactly 78 monthly ERA5-Land JJA requests for
one 1 deg x 1 deg cell, for `EXPLORATORY_DESCRIPTIVE_SINGLE_CELL`, with
a `research_runs/` run root. That record names no event source, no DOI,
no licence scope, no evidence root outside `research_runs/`, no reviewer
identity, and no opportunity-frame decision — and it explicitly excludes
multi-basin fitting. **It is insufficient for the present scope**, and
none of its terms are extended or implied by this record. This record is
a separate, additive authorization request.

## 1. Binding values (machine-decidable; accepted by owner 2026-09-19)

| Field | Value |
|---|---|
| Owner decision | Bounded retrieval authorized — owner-directed attestation executed 2026-09-19 |
| Opportunity-frame policy | **Option A** — an independently byte-bound lake-inventory source may supply the opportunity frame, provided linkage keys and uncertainty are recorded |
| Evidence root (accepted default) | `/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19/` — fresh, absolute, **outside the repository and outside `data/`** |
| Evidence sub-roots | `glof-events/`, `glof-lakes/`, `era5-multibasin/`, `seismic-4w/`, plus `retrieval/`, `licence/`, `quarantine/` |
| Storage reserve | Abort acquisition below **8 GiB** free on the evidence-root volume; pre- and post-write checks on every write |
| Retrieval limits | **<= 1 GiB total bytes**, **<= 40 requests** *(raised to <= 120 total for the P5-C `sd`+`sf` snow leg by owner-directed amendment 2026-09-19 — see the P5-C retrieval-limits row)*, **<= 1 concurrent request**, sequential, **dry-run first**; single session inside `2026-09-19T00:00Z .. 2026-09-20T00:00Z` |
| Stop rules | Halt and quarantine on **any** of: computed digest != published MD5; licence text changed on either record; scope or version drift (not v1.3.0); storage floor reached; endpoint divergence from the declared DOI targets; linkage keys unrecordable; reviewer-flagged anomaly |
| Required evidence per retrieval | `retrieval_record.json`, `licence_snapshot.json` (landing bytes + digest + `retrieval_utc`), `request_metadata.json`, published MD5 where served, SHA-256 sidecars, exact per-member manifest |
| Quarantine rule | Any mismatch is moved atomically to `quarantine/` with the reason recorded; never partially promoted into `bytes/` |
| Authority flags | `warning_path_authorized=false`, `production_authorized=false`, `promotion_eligible=false` |

## P5-A — GLOF event source: HMAGLOFDB

| Field | Value |
|---|---|
| Source + exact version + DOI | `icimod_hmaglofdb_v1_3_0`; v1.3.0; RDS DOI `10.26066/RDS.1973283`; Zenodo mirror `10.5281/zenodo.18257243`; concept DOI `10.5281/zenodo.7271187`; GitHub tag `v1.3.0` = tree `1d975de` |
| Licence scope | **CC BY 4.0 governs** (RDS declaration; GitHub `LICENSE`; Zenodo `cc-zero` tag superseded, conservatively read as CC BY 4.0). Attribution: cite ICIMOD per RDS terms; licence snapshot saved at retrieval |
| Payload identity | `fidelsteiner/HMAGLOFDB-v1.3.0.zip`; **107,879 bytes**; published **MD5 `b6af9657ed28d793b058789835dd4ac8`**; HTTP `content-length` independently observed as 107,879 (2026-09-19) |
| Member files expected inside the archive | `HMAGLOFDB.csv` (449,837 B), `HMAGLOFDB_removed.csv` (27,942 B), `schema.ini` (204 B), `HMAGLOFDB_Metadata.yml` (34,748 B), `LICENSE` (7,048 B), `Code/HMAGLOFDB_PubAnalysis.R` (32,175 B) |
| QC cut-off disclosed by publisher | Quality-controlled to **2025-12-31**; rows dated later require evaluation and cross-check |
| Evidence root | `glof-events/` under the shared evidence root |
| Storage reserve | 8 GiB floor (shared root) |
| Retrieval limits | Within the shared cap; keep the archive as the byte-bound artifact and record deterministic per-member digests — the archive is never silently unpacked in place |
| Stop rules | Shared set; PLUS: `content-length` or MD5 disagreement halts before promotion |
| Column mapping source | `schema.ini` + `HMAGLOFDB_Metadata.yml` **inside the acquired bytes** — never inferred from documentation or memory |

## P5-B — GLOF opportunity frame (admissible under Option A)

| Field | Value |
|---|---|
| Owner policy decision | **Option A** — external, independently byte-bound lake-inventory source may supply the frame, with linkage keys and uncertainty recorded |
| Source + exact version + DOI | ICIMOD 2015 potentially-dangerous-glacial-lake inventory; RDS DOI `10.26066/RDS.1971950`; published 2020-09-10; Koshi, Gandaki and Karnali basins (plus Tibet Autonomous Region and India) |
| Licence scope | **CC BY 4.0** (declared on the RDS record: "Creative Commons Attribution 4.0 International"). Citation: ICIMOD (2020), `https://doi.org/10.26066/RDS.1971950`. Licence snapshot saved at retrieval |
| Evidence root | `glof-lakes/` under the shared evidence root |
| Storage reserve | 8 GiB floor (shared root) |
| Retrieval limits | Within the shared cap |
| Stop rules | Shared set; PLUS: if the lake-ID linkage to HMAGLOFDB `GL_ID` / `LakeDB_ID` / `G_ID` cannot be recorded, the frame is quarantined and the opportunity frame stays `PENDING-OWNER-POLICY` |
| Recorded uncertainty | A linkage-uncertainty ledger is mandatory: unlinked inventory lakes are **recorded, never silently dropped** |
| Access mode | `RW` — owner-declared 2026-09-19: agent read/write retrieval into the declared evidence root. The RDS landing endpoint answers a non-browser client with HTTP 302; if anonymous retrieval fails, the stop rule fires and the owner downloads the payload into the evidence root instead (no scope change). No credential is placed in the repository |
| Fallback (requires an owner amendment, not a silent substitution) | `zenodo.17948783` — "Inventory of Glacial Lakes in High Mountain Asia for the Periods 2016-2017 and 2022-2024" (CC BY 4.0, open, published MD5s). This is a **different source**, not the ICIMOD 2015 inventory. **Amended 2026-09-19 (owner-directed):** this wider inventory is adopted as the *anchor-derivation source only* for `mahakali` and `bagmati` (zero lakes in the PDGL 2015 inventory), and uniformly re-derives all five basin anchors for robustness; the PDGL 2015 inventory remains the opportunity frame unchanged. Retrieval of the amendment source is bounded to its published files with MD5 verification. **Boundary source (owner-directed 2026-09-19):** lake-to-basin attribution for the wider inventory uses `RDS 7952` — "Sub-sub-basins of Hindu Kush Himalaya (HKH) Region" (third-level basin boundaries of the ten HKH major basins incl. the Ganges system containing koshi/gandaki/karnali/mahakali/bagmati) — owner download into the evidence root under access mode `RW`, same stop rules |

## P5-C — Multi-basin feature source: ERA5-Land

| Field | Value |
|---|---|
| Relationship to prior P5-C | **Separate authorization.** The existing P5-C record covered one cell and one descriptive diagnostic, and excluded multi-basin fitting; nothing from it is extended here |
| Source | ECMWF CDS ERA5-Land `reanalysis-era5-land`, hourly, copernicus licence (CC BY 4.0 attribution) |
| Scope | **Five predeclared basin anchor points** — `koshi`, `gandaki`, `karnali`, `mahakali`, `bagmati` (the five basins named in `run_b/REGIME_PROTOCOL_V0.md` section 1) — JJA only, **2001-2025 inclusive** (2026 excluded), the seven existing pre-registered variables `t2m`, `d2m`, `u10`, `v10`, `tp`, `sf`, `sd` (`snow_depth_water_equivalent`, never `sde`) |
| Authorized route | **Amended 2026-09-19 (owner-directed, second amendment):** one contiguous `reanalysis-era5-land-timeseries` request per anchor covering `2001-01-01` to `2025-12-31`, all seven pre-registered variables. Rationale recorded: JJA-exact retrieval on the main dataset exceeds the <=40-request cap at CDS cost limits (~75 requests needed, proven by 403 "cost limits exceeded" on a 5-year/7-variable request); timeseries accepts only a single contiguous date range, so JJA filtering is a downstream deterministic operation. **Non-JJA months are retained in byte-bound evidence and excluded from analysis; the feature scope remains JJA 2001-2025 exactly.** The pure 390-request monthly route remains **not** authorized. **Amended 2026-09-19 (owner-directed, third amendment):** Google Earth Engine `ECMWF/ERA5_LAND/HOURLY` (project `avalanche-hub`, ADC-authenticated, verified live 2026-09-19) is added as an authorized delivery channel **for the `sd`+`sf` snow leg only** — box-mean extraction over the frozen anchor boxes (same spatial semantics as the CDS pulls, not nearest-cell), bands `snow_depth_water_equivalent` + `snowfall_hourly`, JJA 2001-2025, delivered as digested CSV tables under the existing evidence contract. Rationale: CDS queue latency (~10 min/request server-side) makes ~73 remaining requests borderline inside the retrieval window; EE is a rehosted copy of the same ERA5-Land dataset — the byte container differs (CSV vs netCDF) and is declared, not hidden. The CDS snow leg continues concurrently; whichever channel lands full coverage first is operative, the other is recorded as superseded evidence |
| Evidence root | `era5-multibasin/` under the shared evidence root |
| Storage reserve | 8 GiB floor |
| Retrieval limits | **Amended 2026-09-19 (owner-directed):** the shared `<= 40` request cap is raised to **`<= 120` requests total for this record** for the `sd`+`sf` snow leg — CDS cost limits force ~1-year JJA granularity per request (proven: 7var×5yr and 2var×5yr both 403 "cost limits exceeded"; 1var×1yr passes), and 3 anchors x 25 JJA years x 2 snow vars needs ~75 requests. Byte cap `<= 1 GiB` unchanged (actual ~34 MB). All other limits, stop rules, and the storage floor unchanged. Rationale: preserves the pre-registered 7-variable frame on the already-authorized main-dataset route |
| Stop rules | Shared set; PLUS: any incomplete or post-window payload halts; an incomplete merge is quarantined rather than partially kept; any `sde` payload is rejected |
| Anchor derivation rule (accepted by owner 2026-09-19) | Each anchor is the **centroid of that basin's lakes in the P5-B inventory**, computed once, **frozen before any acquisition**, and bound by digest into the retrieval record and the feature `source_manifest` lineage. Anchors are never revised after seeing values, and no anchor coordinate may be chosen by an agent. **Execution note 2026-09-19:** the PDGL inventory contains lakes in only three of the five basins (koshi 42, gandaki 3, karnali 2 — mahakali and bagmati zero), so three anchors are frozen and in effect now; The wider-inventory amendment was executed (`zenodo.17948783` + RDS 7952 attribution): **operative anchors are the HMA-derived set** — koshi 28.072090/87.043138 (n=1436), gandaki 28.633171/84.722626 (n=301), karnali 29.771839/82.198603 (n=767), bound in `retrieval/anchor_derivation_record.json`. **Final scope: 3 basins** — mahakali and bagmati have zero glacial lakes in BOTH inventories and no named polygons in RDS 7952; a lake-derived anchor for either is impossible by construction (geographic fact, not a data gap). The PDGL-derived anchor set and its ERA5 bytes are retained as superseded interim evidence |

## P5-D — Seismic source: 4W nodal array (bounded qualification slice)

| Field | Value |
|---|---|
| Source | FDSN network `4W` via EarthScope `station` + `dataselect` services |
| Scope | **Bounded slice only** — up to four declared stations inside the 27.0-29.0 N / 84.0-86.5 E corridor, channels `DP1`/`DP2`/`DPZ` at 500 Hz, windowed per station epoch inside 2023-04-01 to 2023-05-09 |
| Exact station list | `374`, `312`, `302`, `1158` — the four longest continuous epochs (30 days, 2023-04-09 → 2023-05-09/10) per live FDSN station metadata; owner-accepted 2026-09-19 |
| Licence interpretation | FDSN archive research-reuse for non-commercial research with citation of network `4W` — owner-directed reading recorded 2026-09-19 |
| Decoder precondition | STEIM1/STEIM2 admission requires a pinned `obspy==1.5.1` wheel **and** the seam contract decision **and** representative-byte qualification. The seam remains closed until that contract is ratified; STEIM bytes are inadmissible before then |
| Evidence root | `seismic-4w/` under the shared evidence root |
| Storage reserve | 8 GiB floor; **slice cap <= 10 GB**. The full four-station 500 Hz multi-month window is **not** authorized here and requires a separate amendment with external storage |
| Retrieval limits | Metadata availability probe first; then at most one station per 24 h, four stations total |
| Stop rules | Shared set; PLUS: wrong station/channel/epoch StationXML rejects; STEIM without a qualified decoder rejects; sample-count or time-span mismatch is quarantined |
| Posture | Seismic input is not a Nepal predictor and is not associable; the retrospective-only imperative stands |

## Review and adjudication (owner-only — not delegable)

| Field | Value |
|---|---|
| Reviewer 1 (intake review) | `Sanjay` — owner-designated reviewer; `review_date` 2026-09-19 |
| Reviewer 2 (intake review) | `Ravi` — owner-designated reviewer; `review_date` 2026-09-19 |
| Adjudicator (disagreement resolution) | `Sanjay B` — repository owner |
| Independent-review minimum | Two **uniquely named** reviewers are required; the evidence-sidecar gate rejects duplicate names and fewer than two, so a single-lane review cannot carry `EVIDENCE_VERIFIED` |
| Review evidence | Each reviewer records decision, date, and the evidence digests reviewed — appended after intake, never before |

## Owner execution

| Field | Value |
|---|---|
| Owner name | `Sanjay B` |
| Authentication channel | Owner-directed in-session instruction — verbatim: "sign on my behalf, and proceed" — delivered to the GLM3 review lane and recorded 2026-09-19. No secret is placed in the repository; no independent third-party or cryptographic signature is claimed (`P3_ATTESTATION_RECORD_V0.md` precedent) |
| Attestation date | `2026-09-19` |
| Signature | `Sanjay B — owner-directed in-session attestation, 2026-09-19` |

## Attestation string (owner signs against this scope)

> I authorize only the bounded retrieval described in P5-A through
> P5-D of this record. This authorizes no source qualification, no
> independent-verification claim, no association, no forecast
> execution, and no production or operational action. Every authority
> flag remains false.

## Explicit non-authorization clause

Nothing in this record may be read as pilot selection, forecast-skill
evidence, an alerting authorization of any kind, production
authorization, or competent-authority approval. The pilot outcome
remains `NO_QUALIFYING_PILOT_SOURCE` and the posture remains
`DESIGN_DRAFT_COMPLETE` with `WARNING_PATH_AUTHORIZED: NO`.

## Annex A — read-only evidence gathered 2026-09-19 (no payload retrieved)

| Check | Endpoint | Observed |
|---|---|---|
| HMAGLOFDB Zenodo record | `https://zenodo.org/api/records/18257243` | v1.3.0, published 2026-01-15, `access_right=open`, licence tag `cc-zero`, creators Steiner (ORCID 0000-0002-0063-0067) and Shrestha (ICIMOD), related identifier GitHub tag `v1.3.0` |
| HMAGLOFDB payload | `.../files/fidelsteiner/HMAGLOFDB-v1.3.0.zip/content` | HTTP 200, `application/octet-stream`, `content-length: 107879`, anonymous access, no sign-in |
| HMAGLOFDB RDS copy | `https://rds.icimod.org/Home/DataDetail?metadataId=1973283` | "GLOF database of High Mountain Asia", published 2022-10-19, updated December 2025, **CC BY 4.0**, 766 GLOFs 1533-2025, 23 percent recurring from three ephemeral ice-dammed lakes, contact Finu Shrestha |
| Lake-inventory record | `...?metadataId=1971950` | Potentially dangerous glacial lakes, Koshi/Gandaki/Karnali + Tibet + India, published 2020-09-10, **CC BY 4.0**, hazard rank per lake, 5 m / 12.5 m ALOS DEM, contact Sudan Bikash Maharjan |
| RDS endpoint behaviour | HEAD on both RDS landing URLs | HTTP 302 for a non-browser client — the reason the P5-B access mode required an owner declaration (supplied: `RW`) |
| Repository byte inventory | `https://api.github.com/repos/fidelsteiner/HMAGLOFDB/git/trees/v1.3.0` | tree `1d975de`; member sizes recorded in P5-A |
| Publisher QC note | repository `README.md` at tag `v1.3.0` | quality control completed to 2025-12-31; later-dated rows need cross-checking |
| obspy availability | PyPI JSON API for `obspy==1.5.1` | released 2026-08-28, `requires_python >=3.8`, cp314 macOS arm64 wheel published; no `py3-none-any` wheel exists |

No dataset payload, file body, or waveform byte was retrieved for this
annex. Every value above comes from a metadata API or landing page.

## Annex B — owner fields (all supplied 2026-09-19)

1. Reviewer 1 `Sanjay`, Reviewer 2 `Ravi`, adjudicator `Sanjay B`
   (repository owner), `review_date` 2026-09-19. Two uniquely named
   reviewers satisfy the evidence-sidecar minimum; independence is an
   owner declaration.
2. Owner `Sanjay B`; authentication channel = owner-directed
   in-session instruction ("sign on my behalf, and proceed"),
   recorded 2026-09-19; attestation date 2026-09-19; signature =
   owner-directed attestation (no independent signature claimed).
3. P5-B access mode `RW` — agent read/write retrieval into the
   evidence root; owner-download fallback if anonymous retrieval
   fails (302).
4. P5-D station list `374`, `312`, `302`, `1158`; licence
   interpretation = FDSN research-reuse, non-commercial, cite
   network `4W`.

All four items are complete. This record authorizes **bounded
retrieval only** under the limits and stop rules above; Phase 2 may
begin under those constraints.