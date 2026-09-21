# P5 audit-3 implementation loop

Tier: 3
Objective: close the release-integrity gaps identified by the audit-3 plan without changing the scientific estimands, acquiring new data, or changing authority flags.

Current baseline:

- repository HEAD: `3e943319b712540f83cd70b929fb52a1874d983d`
- tree: clean before implementation
- manifest: 151 files verified
- recorded full suite: 3714 passed / 6 skipped / 0 failed / 57 warnings
- current evidence index: 31 explicitly listed files; exhaustive coverage is not yet proven

Phase order:

1. Exhaustive evidence-index v2 and seismic contract hardening (parallel, disjoint ownership).
2. Coordinator replay proof-boundary separation.
3. Coordinator non-overwriting writers and provenance fields.
4. v2 release bundle, documentation reconciliation, and claim scan.
5. Single coordinator manifest rebind and final verification.

Hard stops:

- no seismic acquisition or ObsPy admission;
- no Arm C data acquisition;
- no authority-flag changes;
- no historical evidence overwrite;
- no manifest rebind before all implementation and verification checks pass.

## Audit-4 current-tree correction (2026-09-21)

Audit-4 found the v4 bundle internally consistent but bound to a stale
snapshot (d789814 vs HEAD aa3caa9; manifest ac7921 vs db842e; receipt
3885/7/0 vs current 3886/6/0). Phase order:

1. Freeze record marking v4 `VALID_FOR_FROZEN_SNAPSHOT_ONLY` + incident
   record for the disposition-v2 overwrite.
2. Parallel agents: current-tree validator mode (FROZEN_SNAPSHOT vs
   CURRENT_TREE status split, env-digest recompute) + receipt v2
   (manifest-sha binding, execution-window fields).
3. Coordinator: docs, single content commit, one rebind, then the
   serialized chain — receipt v2, replay v6 reports, index v5
   (CLOSURE_PENDING), closure v5 (CURRENT_TREE_CLOSURE_OK), verification
   v3, INCIDENT_SURFACE_V2. No commits after receipt generation.

Hard stops: v4 bytes untouched; no acquisition; owner gates unchanged;
"all gaps closed" remains prohibited.
