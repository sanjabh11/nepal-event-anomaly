# Governance Exercise Record — v0

**Recorded:** 2026-09-16. **Lane:** Round-6 documentation residuals
(GOV-02). **Status:** `DOCUMENTATION` — a bounded record of
governance controls that have been *exercised at design level*
inside this repository. It changes no approval, no posture, and no
code.

## 1. Scope and honesty bound

This record is **bounded**: it documents governance mechanisms that
have actually fired or been demonstrated inside this repository — at
the level of policy text, enforced code gates, and preserved
on-disk evidence under the gitignored `research_runs/` tree. It is
**not** a real-artifact governance exercise: no intake artifact, no
external approval, and no operational decision has been governed
end-to-end. Nothing here authorizes acquisition, intake,
forecasting, warning-path use, production, or authority action.
Standing postures are unchanged: `warning_path_authorized=false`,
`production_authorized=false`, `promotion_eligible=false`;
`NO_QUALIFYING_PILOT_SOURCE`; `WARNING_PATH_AUTHORIZED: NO`.

## 2. Controls exercised (with evidence)

| Control | Evidence of exercise | Where recorded |
|---|---|---|
| Disk-reserve guard fired | Free space dipped to ~7.1 GiB mid-cycle and the "guard correctly refused writes"; ~8.2 GiB free at baseline, ~15 GiB at the 2026-09-14 re-check | `P0_BASELINE_LEDGER.md` field table, "Disk" row (line 13); G34 "guard observed working below reserve" (`GAP_REGISTER_V0.md` line 47); the 8 GiB `shutil.disk_usage` guard is enforced in code (P5-10, `GAP_REGISTER_V0.md` line 268) |
| Quarantine-with-reason on failure | Preflight failure exits 1 with no artifacts; failed runs are quarantined with the error recorded — never silently dropped | `GAP_REGISTER_V0.md` CFM-10 (line 227) and the O05 deletion/quarantine policy (lines 305–306) |
| Preserved failed/stalled run roots | The stalled pure-CDS attempt `research_runs/gmm_confirmation_20260915T020537Z/` (5/78 months) and `shard_2001_2007/` were preserved under the gitignored run root and reused as comparison evidence, not deleted; the original Run A root was likewise "preserved as immutable evidence" when its corrected derivative was produced | `run_a/HYBRID_ROUTE_RECONCILIATION_V0.md` §4 (lines 103–105); `GAP_REGISTER_V0.md` reconciliation-round-2 preamble (line 341). `research_runs/` is gitignored — these roots exist on disk but are not distribution surfaces |
| Retention policy | Governed artifacts are confined to the manifest scope (`docs/science`, `nepal/research_v0`, `tests/test_research_v0_*`, `.github/workflows`, `README.md`); intake bytes (raw source payloads) are excluded and must carry their own per-source license record before they may exist in the tree | `GAP_REGISTER_V0.md` "Governance policies (O05)" (lines 292–298) |
| Privacy/redaction policy | Reporter- or observer-identifying fields are prohibited in committed artifacts; source sidecars record license, coverage, timing, and review metadata only | `GAP_REGISTER_V0.md` (lines 299–301) |
| Access/authority policy | All authority-bearing fields (`human_approved`, `approver_*`) are structural claims, not authentication; the P3 decision requires external, authenticated human attestation | `GAP_REGISTER_V0.md` (lines 302–304); `P3_ATTESTATION_RECORD_V0.md` "Authentication channel" |

## 3. What was NOT exercised (honest residual)

- No governance decision over a real intake artifact — every source
  remains `CANDIDATE_ONLY`; intake-gated items stay gated.
- No multi-party or independent review — the P3 attestation is
  owner-directed (`P3_ATTESTATION_RECORD_V0.md`); GOV-01's
  downstream human approvals remain open-by-design.
- No incident-response, revocation, or post-publication takedown
  path has ever been needed or exercised.
- The preserved run roots above are gitignored working-tree
  evidence: they demonstrate the retention/quarantine *mechanism*
  on real directories, but they are not governed artifacts in the
  manifest scope.

## 4. Non-authorization clause

This record may not be read as governance sign-off for any pilot,
forecast, warning-path, production, or authority action. It
documents that declared controls exist and have fired in bounded
form — nothing more.
