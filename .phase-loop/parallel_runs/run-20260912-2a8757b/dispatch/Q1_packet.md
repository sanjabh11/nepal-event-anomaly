# DISPATCH PACKET — Q1 (Independent Read-Only Verifier)
**RUN_ID:** `run-20260912-2a8757b` | **Status:** READY TO DISPATCH
**Model label:** GLM-5.2 (record actual runtime model ID; do not assume availability — report UNAVAILABLE if absent)

## Anti-hallucination preamble (paste verbatim at top)
You are one isolated worker in a gated, evidence-first Phase B-to-F campaign.
Do not infer completion from narrative, prior reports, green structural tests, filenames, or another agent's prose. Inspect the live filesystem and execute bounded checks.
Every claim must be backed by: exact path, file size, SHA-256, schema/semantic validation, source+processing lineage, contract binding, and command+exit-code evidence.
Allowed states: READY, BLOCKED, INCOMPLETE, FAILED, UNAVAILABLE, NEEDS_INPUT.
Never: invent hashes/values/URLs/files/credentials/model IDs; modify code, manifests, data roots, preregistration, or gate booleans; perform duplicate bulk downloads; write outside your packet directories; claim production/warning/authority/field validation.
If anything is ambiguous, stop and emit NEEDS_INPUT or UNAVAILABLE.

## Identity & bindings
- Authoritative root: `/Users/sanjayb/nepal-event-anomaly` — verify you are NOT in `/Users/sanjayb/avalanche-insight-hub-t2a`
- Expected HEAD: `2a8757b200eac0bb1921015c47eae25120f692a9`
- RUN_CONTEXT: `/Users/sanjayb/nepal-event-anomaly/.phase-loop/parallel_runs/run-20260912-2a8757b/RUN_CONTEXT.json`
- Dirty state: 37 status entries, unstaged diff sha256 `5798d7fe99dc5feaf524fdac92e63c4c8f9c80eb9edb9a4ef4fa8e099a750586` — verify it is UNCHANGED at end of your review
- Live manifest: 36 entries (29 READY / 6 UNAVAILABLE / 1 INCOMPLETE), file sha256 `a20762d5cb9fce1ac2cafb9b8dcf3d24dea1c0c6dbffe4fa01ee509d802cad71`

## Write allowlist (only)
- `/Users/sanjayb/nepal-event-anomaly/.phase-loop/parallel_runs/run-20260912-2a8757b/packets/Q1/`
- `/Users/sanjayb/nepal-event-anomaly/.phase-loop/parallel_runs/run-20260912-2a8757b/reviews/Q1/`

## Independently verify (each finding: path, observed, expected, reproduction command)
1. Root identity and base HEAD; dirty-state preservation (re-hash diff at end)
2. Live manifest counts direct from disk; every READY artifact exists on disk with matching SHA-256
3. Canonical vs pretty-JSON hash behavior (manifest self-hash warning in handoff_gate_result.json)
4. **Contract-hash naming ambiguity:** handoff gate `contract_sha256`=240a3615 is the DATA contract file hash; `framework_contract_sha256`=ecda408e is STALE (pre-2a8757b); current runtime framework hash=7748b089 via `contract --verify`. Confirm/deny with evidence.
5. Exact target grid: 300x300, 100 m, EPSG:32645; raster units, dtype, finite values, ranges, nodata, CRS, affine, bounds, shape
6. GHSL: measured non-negative m²/cell — not binary presence
7. S1: nested observability_by_unit map, unit count 90000, fractions in [0,1]; metadata coverage is NOT SLC coherence
8. S2 selected-scene lineage; hanging-ice = support proxy with -1 unscreenable preserved
9. OSM/WorldPop registration; source URL/version/hash/retrieval-timestamp evidence
10. Waiver records for UNAVAILABLE sidecars; RGI/Farinotti conditional blockage pending crosswalk
11. Package-extra inventory; duplicate-contract drift; stale documentation
12. Raw-SLC boundary (must be zero); no symlink/path escape
13. B ranked-versus-gated status distinction in docs and CLI
14. Frozen guards: preregistration.md `0e7ce3c2…`, gmm_false_positive_results.json `fb711617…`, locked_jja_events.json `536125c4…`, features_nepal_jja_2001_2026.csv `444f2ac0…`

## Constraints
- Read-only everywhere except allowlist. Small metadata probes only if zero material disk/credential cost.
- Do NOT re-download; do NOT run the pipeline; do NOT edit gate artifacts.

## Return
REVIEW_PASS / REVIEW_BLOCKED / REVIEW_INCOMPLETE with a discrepancy ledger. Every discrepancy: path, observed value, expected value, reproduction command.

## Recommended skills for the Q1 session
`agent-watchdog` (audit-another-agent pattern), `verification-loop`, `conformance-gate`, `agent-phase-ratchet`.
