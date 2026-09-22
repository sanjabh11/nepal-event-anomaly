# P5 Release-Integrity Threat Model — v0

**Status: internal control inventory — not an external certification**

This document maps release-integrity attack classes to the controls present
in the governed Nepal repository. It describes what the current v9 release
chain is designed to detect; it does not claim that the chain is externally
replicated, remotely executed, cryptographically signed by an independent
party, or scientifically positive. Scientific terminal statuses and owner
gates remain unchanged.

## 1. Trust boundaries

| Boundary | Trusted input | Required posture |
|---|---|---|
| Repository bytes | Current Git worktree, manifest, tracked source and tests | HEAD, content head, manifest commit, and manifest digest must agree |
| Evidence roots | External evidence payloads and release records | Every included payload must resolve, hash, and sidecar-validate |
| Release builders | Coordinator-run scripts | Exclusive-create outputs; no historical replacement |
| Validators | Read-only scripts and live bytes | A validator failure is a release failure, not a warning |
| Owner decisions | Explicit records with identity and time | A recommendation or template is not approval |
| Scientific claims | Gate observations, terminal status, authority flags | Preserve the claim ceiling and all-false authority surface |
| External proof | Remote CI, preservation, publication, field data | Not present in this internal release surface |

## 2. Attack and drift matrix

| ID | Attack or drift class | Failure it could cause | Enforcing control and test surface | Current state |
|---|---|---|---|---|
| RI-01 | Stale path-to-SHA binding | A closure names one file but hashes bytes from another version | scripts/validate_evidence_index.py; scripts/validate_release_closure.py; tests/test_evidence_index_validator.py; tests/test_release_closure_validator.py | Defended locally |
| RI-02 | Seed-then-replace output | A historical artifact is silently overwritten after its digest is recorded | scripts/p5_safe_io.py; tests/test_p5_safe_io.py; exclusive-create writers | Defended locally |
| RI-03 | Free-form suite counts | A receipt claims counts that were not produced by the recorded test run | scripts/build_p5_suite_receipt.py; tests/test_suite_receipt.py; receipt schema and count equality checks | Defended locally |
| RI-04 | Suite receipt/tree drift | A green suite receipt is attached to a later repository HEAD | receipt repository_head and manifest binding; scripts/validate_release_closure.py with current-tree; tests/test_release_closure_v4.py | Defended locally when current-tree validation is run |
| RI-05 | Environment drift | A replay or closure is interpreted as reproducible under a different package set | closure environment digest and recomputation; tests/test_release_closure_validator.py | Defended at recorded-environment level |
| RI-06 | Unplanned index output | Closure or verification files appear after indexing without a declared slot | scripts/generate_evidence_index_v2.py; scripts/validate_evidence_index.py; planned exclusion owner checks | Defended locally |
| RI-07 | Hidden or duplicated evidence payload | A payload is omitted, indexed twice, or assigned to two physical roots | exhaustive inventory and physical-byte assignment checks in the index validator; tests/test_evidence_index_validator.py | Defended for the declared root map |
| RI-08 | Missing or stale sidecar | A payload is changed without the release surface noticing | index sidecar checks; release closure path/SHA checks; tests/test_materialize_evidence_sidecars.py | Defended locally |
| RI-09 | Incident evidence contamination | Quarantine or mutation artifacts are mistaken for scientific release payloads | scripts/validate_incident_surface.py; tests/test_incident_surface.py; incident-surface exclusion record | Defended locally |
| RI-10 | Replay scope inflation | Artifact hashing is described as independent model reproduction | scripts/replay_p5.py; scripts/replay_seasonal_p5.py; tests/test_replay_never_reexecutes.py; persisted report replay_scope check | Defended for the two replay modules |
| RI-11 | Replay writer overwrite | Re-running a replay replaces a canonical report | scripts/p5_safe_io.py and replay report write-once path; round-1 exporter tests; coordinator run discipline | Defended for write-once report paths |
| RI-12 | Validator report side effect | A validator invocation writes or replaces a report unexpectedly | round-2 ladder omits every validator report-out option and uses argv with shell=False | Partially defended; direct manual report-out use remains caller responsibility |
| RI-13 | Manifest revision injection | An option-like or non-commit revision is bound as content | scripts/rebind_manifest.py resolve_commit guard; tests/test_rebind_manifest_cli.py | Defended locally |
| RI-14 | Manifest surface drift | New tracked files escape the governed manifest | scripts/rebind_manifest.py scope_audit; manifest scope-exclusion baseline; manifest verification | Defended at rebind time |
| RI-15 | Claim-language drift | Documentation quietly promotes descriptive evidence to warning or operational authority | nepal.research_v0.cli claim-scan; existing workflow claim scan; tests/test_promotion_protocol_consistency.py | Defended for scanned surfaces |
| RI-16 | Gate vocabulary drift | Documentation and engine silently disagree about required gates or statuses | tests/test_promotion_protocol_consistency.py derives names from nepal.research_v0.gates and nepal.science_v0.regimes | Defended for the tested protocol |
| RI-17 | Approval inference | A null owner field or recommendation is treated as an approval | closure approval-consistency validation; owner-disposition records; Arm C and Option 3 templates | Defended structurally; human authentication is outside the repository |
| RI-18 | Shell or path injection in the ladder | A configured path executes unintended commands or escapes the repository | scripts/run_verification_ladder.py uses argv lists, shell=False, path declarations, and no report-out writes; tests/test_verification_ladder.py | Defended for the ladder |
| RI-19 | Ladder fail-open behavior | One failed validator is hidden by later green steps | scripts/run_verification_ladder.py records every step and returns LADDER_FAIL or LADDER_INCOMPLETE; tests/test_verification_ladder.py | Defended locally |
| RI-20 | False clean-tree result | Untracked or modified files remain outside the final release snapshot | ladder clean_tree command includes tracked and untracked status; final coordinator verification | Defended when the ladder is run before publication |
| RI-21 | Seismic/GLOF leakage | Seismic bytes enter the GLOF predictor or regime frame | seismic contract isolation, source-role validation, authority flags, Option 3 template | Defended by declared interfaces; no seismic execution is authorized |
| RI-22 | Arm C post-hoc feature search | Pressure-level channels are expanded or selected after seeing results | Arm C amendment template; existing feature-manifest and amendment gates | Not executable until owner completes the amendment |

## 3. Controls the current release does not provide

The following are deliberate limits, not silently passing controls:

1. No independent remote CI run has been observed. The draft workflow is
   manual-only and its action references still require full-SHA pinning before
   remote enablement.
2. No independent host, archival service, signed attestation, or public
   preservation copy is bound into the release chain.
3. A validator can be misused by a human who explicitly supplies a
   report-out path; the round-2 ladder avoids that interface but does not
   change existing validator behavior.
4. The clean-room exporter’s root_map.json intentionally retains source host
   paths for internal provenance. It is not an externally sanitized bundle
   until a redacted root-map policy is approved.
5. Artifact-integrity replay does not refit the GMM. The model-reexecution
   report is a separate activity and must not be conflated with replay.
6. Local contract tests do not establish data rights, buyer acceptance,
   field performance, warning authority, or scientific truth.
7. Arm C, Option 3 seismic execution, ObsPy admission, association, and
   publication remain owner-gated.

## 4. Audit-5 operating checklist

Before any future release-chain rebind, the coordinator should record:

- repository HEAD, content head, manifest SHA, tree state, and disk state;
- exact suite receipt counts, warnings, skips, and collection count;
- current-tree closure status, index status, incident-surface status, and both
  replay scopes;
- every ladder step, command digest or argv, expected exit, observed exit,
  and captured failure;
- claim-scan scope, including every workflow file;
- authority flags and owner-approval fields;
- any post-publication commit, with a new closure rather than an amended
  historical closure.

The release is not complete if any step is NOT_CONFIGURED, if a replay scope
is absent, if a planned slot is treated as closed before publication, or if
the current HEAD differs from the bound receipt and closure.

## 5. Residual risk rating

| Risk class | Rating | Disposition |
|---|---:|---|
| Internal byte/path/count integrity | P0 | Controlled by validators and exclusive-create writers |
| Replay proof-boundary confusion | P0 | Controlled by scope vocabulary and round-2 regression test |
| Ladder sequencing or hidden failure | P0 | Controlled by the round-2 ladder; coordinator must run it |
| Owner approval and dependency admission | P1 | Explicitly pending; never inferred |
| Single-host and remote reproducibility | P2 | Open disclosure; requires external infrastructure |
| Scientific power and future positive interpretation | P1 | Addressed by the statistical note and a future amendment |
| Operational or warning authority | P0 | Permanently outside the current claim ceiling |
