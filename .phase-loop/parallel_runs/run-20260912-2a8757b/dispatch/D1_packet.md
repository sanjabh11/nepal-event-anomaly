# DISPATCH PACKET — D1 (Data & Provenance Lane)
**RUN_ID:** `run-20260912-2a8757b` | **Status:** HELD — do not dispatch until disk >= 5 GiB free
**Model label:** GLM-5.3 (record actual returned model ID + endpoint; if unavailable report UNAVAILABLE)

## Anti-hallucination preamble (paste verbatim at top)
You are one isolated worker in a gated, evidence-first Phase B-to-F campaign.
Do not infer completion from narrative, prior reports, green structural tests, filenames, or another agent's prose. Inspect the live filesystem and execute bounded checks.
Every claim must be backed by: exact path, file size, SHA-256, schema/semantic validation, source+processing lineage, contract binding, and command+exit-code evidence.
Allowed states: READY, BLOCKED, INCOMPLETE, FAILED, UNAVAILABLE, NEEDS_INPUT. READY is forbidden unless all required evidence is present. A ranked screen is not a passed B_TO_C gate. A loader success is not scientific validation. Metadata coverage is not SLC coherence. A source-database mechanism label is not independent adjudication.
Never: invent hashes/values/URLs/files/credentials/model IDs; use synthetic data as real evidence; flip booleans; weaken thresholds; delete/reset/clean user files; overwrite the existing reconciled root; write outside the declared allowlist; accept arbitrary caller paths or gate booleans; claim production/warning/authority/field validation.
If root, contract, credentials, model identity, disk guard, or input semantics are ambiguous, stop and emit NEEDS_INPUT or UNAVAILABLE. Use atomic writes and checkpoints. Redact secrets. Bounded timeouts.

## Identity & bindings
- Authoritative root: `/Users/sanjayb/nepal-event-anomaly`
- Base HEAD: `2a8757b200eac0bb1921015c47eae25120f692a9` (2026-09-12T11:42+05:30, "Implement fail-closed framework v1 gates")
- RUN_CONTEXT: `/Users/sanjayb/nepal-event-anomaly/.phase-loop/parallel_runs/run-20260912-2a8757b/RUN_CONTEXT.json` — read first
- Data contract (feature_contract.py) sha256: `240a3615f89cdd9c49b6833623084d4dbdd5b5f85c5b50e1fe4ea7c84513ec0a`
- Framework contract RUNTIME sha256 (current): `7748b089b8bf29a705090e0ba7751a5f8ecd15ad51272bfd090c54ce0e1b6c69`
  - NOTE: `handoff_gate_result.json` records stale `ecda408e…` from before today's contract commit. Bind to the RUNTIME value above; flag if it changed again.
- Preregistration sha256 (frozen): `0e7ce3c2e347a955f7495d719bb9232465ac9c25a5266656865800dbc963da7c`

## Write allowlist (exclusive lease — nothing else)
- `/Users/sanjayb/nepal-event-anomaly/data/framework_inputs_v1_reconciled_candidates/run-20260912-2a8757b/`
- `/Users/sanjayb/nepal-event-anomaly/.phase-loop/parallel_runs/run-20260912-2a8757b/packets/D1/`

## Never touch
`nepal/`, `tests/`, `preregistration.md`, `data/framework_inputs_v1_reconciled/` (live root — read-only), `data/framework_inputs_v1/`, legacy `data/*.json`/`*.nc`, frozen artifacts.

## Live manifest baseline (verify, don't trust)
36 entries: 29 READY / 6 UNAVAILABLE / 1 INCOMPLETE. File sha256 `a20762d5cb9fce1ac2cafb9b8dcf3d24dea1c0c6dbffe4fa01ee509d802cad71`, ~877 MB.
Reconcile counts from disk, not from prior reports.

## Known gaps to close or classify (from GLM2 reconciled plan + live manifest)
- sentinel2_dry_season_metadata — INCOMPLETE (no STAC catalog access); produce selected-12 scene manifest with scene IDs, dates, selection rule, metadata hash, algorithm/config hash
- hanging_ice_support_grid — label as SUPPORT PROXY, not detection; preserve -1 unscreenable semantics; add valid-scene counts per cell or bound sidecar
- sentinel1_compatible_pair_table / per_unit_observability — nested observability_by_unit, exact unit IDs, pair-table hash, pair count, metadata-only method, fraction [0,1]
- ghsl_built_up_surface — measured non-negative m²/cell semantics, not binary mask
- hydrorivers_drainage — subset extraction (~1.5 GB source — check disk first)
- worldpop_population — clipped intermediate, loaded-but-inactive context
- era5_winter_b_screening, era5_sd_snow_depth, era5_sf_snowfall — acquire or mark UNAVAILABLE with evidence
- rgi60_to_rgi7_crosswalk — process rgi6_links.csv; keep Farinotti claims conditionally blocked until complete
- cnki_cas_tibet_bureau_search, permafrost_proxy, thermal_layers_sidecar — waiver records if unavailable
- OSM building subsets — register with exact URLs, versions, retrieval timestamps, file hashes, script hashes, clip/resample methods

## Gate IDs owned
G02, G05, G08–G11, G20, G26–G30, G38–G40

## Hard rules
- One material bulk download at a time. Re-check `df -k` before each.
- Scan for raw SLC after every acquisition; STOP immediately if any appears.
- Candidate manifest self-hash does NOT authorize it. Report CANDIDATE_READY only.
- If a source cannot be acquired/verified → UNAVAILABLE or BLOCKED with evidence, plus a waiver record.

## Required outputs (in packets/D1/)
candidate manifest; complete artifact inventory; source lineage; S2 scene manifest; S1 pair/observability lineage; semantic validation report; OSM/WorldPop registration; waiver list; raw-SLC scan result; canonical evidence packet; command log with exit codes; actual model ID + endpoint record.

## Recommended skills for the D1 session
`ecc-ml-engineering` (dataset audit, model provenance), `mle-workflow` (data contracts), `agent-phase-ratchet` (phase ledger), `get-available-resources` (disk guard enforcement).
