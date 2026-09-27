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

## Audit-4 current-tree correction and successor (2026-09-21)

Audit-4 first found the v4 bundle internally consistent but bound to a
stale snapshot. The v5/v6 repair chain is retained immutably. The
successor pass reconciles the remaining release metadata and binds the
current tree as v9:

1. Explicit closure identity inputs and focused regression tests.
2. Current documentation, manifest wording, and phase ledger update.
3. One content commit, one manifest rebind, and one machine-generated
   suite receipt v4.
4. Exhaustive index v9, detached closure v9 binding incident surface V2,
   and current-tree validator.
5. Full suite, replay, index, closure, claim, manifest, and tree checks;
   no commits after receipt generation.

Hard stops: v4–v6 bytes untouched; no acquisition; no dependency
admission; owner gates and authority flags unchanged; “all gaps closed”
remains prohibited.

## Round-3 integration and next-gate decision (2026-09-22)

Round-3 adds only release-integrity and preflight surfaces: an independent
model-re-execution report validator, a clean-room exported-bundle verifier,
an Arm C metadata-only dry-run tool, bounded seismic candidate inventory and
re-execution interpretation notes, and their focused tests. The eight files
were integrated in content commit `fb10798`; focused verification was 18
passed and the source/document claim scans were clean.

The first manifest rebind followed as `cace202`. Before any successor release
publication, each phase-ledger correction must be syntactically and
semantically validated. The coordinator must serialize manifest rebind,
machine suite receipt, exhaustive evidence index, release closure,
incident-surface validation, replay/model-proof validation, claim scan, and
final manifest and tree checks. No post-publication repository commit may be
made without a new release successor.

Arm C remains `SCOPE_APPROVED_RETRIEVAL_DEFERRED`: only a metadata/size
preflight is permitted at this stage, and the current preflight must remain
blocked until the grid resolution and metadata source are explicitly bound
in a successor amendment. No CDS payload retrieval is authorized here.
Seismic remains `NOT_REQUESTED`; ObsPy admission, multi-season expansion, and
external proof remain separate owner/external gates. No authority flag,
estimand, gate, null, embargo, basin mapping, or claim ceiling may change.

## Round-3 receipt correction (2026-09-22)

The first post-rebind machine receipt (`p5_suite_receipt_v8.json`) is retained
as immutable diagnostic evidence. It ran the governed `.venv` suite at 3,978
collected tests and recorded 3,971 passed, 6 skipped, 1 failed, and 57
warnings. The sole failure was the release-control test that detected the
manifest's historical `full_suite_venv` text still described the previous
3,960-test census after `collection_guard` had been rebound to 3,978.

This is a release-metadata inconsistency, not a scientific result or a reason
to weaken a gate. The coordinator must preserve v8, record the incident in
the phase ledger, update the manifest summary to the observed diagnostic
result, rerun the full suite, then bind the exact successful result in a
final receipt before generating the successor index and closure. No closure
may consume v8 as a successful suite receipt.
