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
