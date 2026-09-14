# Gap Register v0 — audited status

**Recorded:** 2026-09-13; **audited & hardened:** 2026-09-14.
Status vocabulary (honest, per audit): `RESOLVED` (semantic enforcement
in code+tests or external evidence), `SHAPE-ONLY`/`DOCUMENTATION` (form
exists, semantics unproven), `POLICY_DEFINED` (binding rule; execution
gated on P3/P5), `BLOCKED_EXTERNAL` (procurement/registration),
`GATED_ON_DATA` (cannot be resolved without approved intake).

## Original 38 gaps — post-hardening disposition

| ID | Audited status → now | What hardened |
|---|---|---|
| G01 | Partial → RESOLVED | Ledger records root/HEAD/guard; additive files committed under controlled commit |
| G02 | Open → POLICY_DEFINED | D1/D2 exist; human P3 approval still required — no substitute exists |
| G03 | Documentation → RESOLVED | `VERTICAL_IDS`/`MECHANISM_IDS` allowlists enforced in `HazardVerticalSpecV0` |
| G04 | Metadata-only → RESOLVED (inventory) | Records separated metadata vs verified evidence; `EVIDENCE_VERIFIED` needs sidecar hash + `INDEPENDENTLY_VERIFIED` |
| G05 | External-blocked → BLOCKED_EXTERNAL | HiAVAL/HMAGLOFDB/S1 licenses marked UNRESOLVED in docs; `CANDIDATE_ONLY` enforced |
| G06 | Partial → POLICY_DEFINED | Nepal-vs-HMA counts flagged; reproducible count is an intake artifact |
| G07 | Shape-only → RESOLVED | `require_finite_seconds`/`parse_strict_utc`; NaN/inf/negative/naive rejected; class consistency checked |
| G08 | Shape-only → RESOLVED | Targets derive from `opportunity_state`, not caller bool; malformed/inverted intervals → censored |
| G09 | Partial → RESOLVED | `GEOMETRY_ROLES` allowlist + lat/lon bounds in `EventLabelV0` |
| G10 | Partial → RESOLVED | `cascade_atomicity_problems` rejects unmapped members, not just split conflicts |
| G11 | Shape-only → RESOLVED | `ObservationOpportunityV0`: ordered windows + coverage/state consistency |
| G12 | Shape-only → RESOLVED | `ControlWindowV0` requires linked `opportunity_state=="OBSERVED_FULL"` for NEGATIVE |
| G13 | Partial → RESOLVED | Cutoff chain includes `forecast_initialization`; `archive_availability >= issue`; `local_retrieval >= archive` |
| G14 | External-blocked → BLOCKED_EXTERNAL | Archive feasibility metadata recorded; issue-time retrieval + cycle completeness verified only at intake |
| G15 | Partial → RESOLVED | Field-class↔data-class consistency map; reanalysis rejected in forecast gate |
| G16 | Heuristic → RESOLVED (code) | `field_class` registry + `source_lineage` required; exposure denylist; semantic audit still intake-gated |
| G17 | Heuristic → RESOLVED (code) | Name regex + denylist + lineage; renamed payloads caught by registry requirement |
| G18 | Partial → RESOLVED | Envelope lifecycle hardened; statuses allowlisted; blockers propagate |
| G19 | Partial → RESOLVED (expanded) | AST scans: imports, dynamic import/eval/exec, write-mode opens, mutation attrs; runtime smoke test |
| G20 | Heuristic → RESOLVED (code) | Lineage+validity checks; feature-level temporal gate on `availability_time` |
| G21 | Partial → RESOLVED | `ASSIGNMENT_RULES` allowlist (no random), complete `event_assignments` mapping, ≥2 eval regions |
| G22 | Documentation → POLICY_DEFINED | Version-drift re-audit rule stands; runtime enforcement at intake |
| G23 | Shape-only → RESOLVED | `adjudication_state` + ≥2 `reviewer_ids` required for adjudicated labels; `DISAGREEMENT_RETAINED` never positive |
| G24 | Documentation → POLICY_DEFINED | `UNDERPOWERED_DESCRIPTIVE_ONLY` status; numeric power gate at intake |
| G25 | Shape-only → RESOLVED | `RegimeArtifactV0`: ≥3 seeds, K=1 null, TRAIN_ONLY, label blinding, digest format |
| G26 | Documentation → POLICY_DEFINED | Association spec retained; evaluator post-P3 |
| G27 | Not implemented → GATED_ON_DATA | `ForecastExperimentV0` binds vintage digests + baselines + metrics; engine post-P3/P5 |
| G28 | Policy → RESOLVED (code) | Field-class/data-class consistency + provenance digests reject transferred K/performance payloads |
| G29 | Documentation → RESOLVED (code) | `evaluation_region_count >= 2` enforced on holdout + experiment |
| G30 | Boundary closed → RESOLVED | `preregistration.md` + framework surfaces: zero tracked diff |
| G31 | Partial → RESOLVED | Legacy commands commented out (inert); README banner; CLI has no data actions |
| G32 | Unverified → RESOLVED | `sha256_file` hashes actual bytes, refuses symlinks/missing; envelope digests computed post-validation; `validate-envelope` recomputes self-hash |
| G33 | Partial → RESOLVED (code) | `evidence_sidecar_sha256` + `evidence_review_state` machine-enforced for `EVIDENCE_VERIFIED` |
| G34 | Guard-only → RESOLVED (guard) | Ledger records pre/post-check rule; guard observed working below reserve |
| G35 | Test-layer → RESOLVED (honesty) | Docs label all tests contract-layer; status vocabulary corrected |
| G36 | Fails direct construction → RESOLVED | `ResearchClaimEnvelopeV0.__post_init__` rejects true authority flags |
| G37 | Documentation → POLICY_DEFINED | Shadow-eval spec retained; not authorized |
| G38 | Process → RESOLVED (this cycle) | Disjoint lanes + serial merge + committed manifest; reproducible |

## Post-audit defect register (A01–A24)

| ID | Severity | Disposition |
|---|---|---|
| A01 untracked files | High | RESOLVED — controlled commit of additive files + `ARTIFACT_MANIFEST_V0.json` |
| A02 direct authority flags | Critical | RESOLVED — `__post_init__` raises on any true authority flag |
| A03 fabricated hash strings | Critical | RESOLVED — `matrix_path`/`policy_path` required; bytes re-hashed; symlink/mismatch rejected |
| A04 unvalidated records | Critical | RESOLVED — record protocol required; `problems()` must be empty before hashing |
| A05 blockers ignored | High | RESOLVED — execution statuses refuse while `unresolved_blockers` non-empty |
| A06 weak event times | High | RESOLVED — strict-UTC, ordered intervals, finite uncertainty, precision consistency |
| A07 weak observation/control | High | RESOLVED — ordered windows, coverage/state consistency, OBSERVED_FULL linkage |
| A08 naive/NaN cutoff | Critical | RESOLVED — explicit UTC only, finite only, init+archive fields in chain |
| A09 invalid horizon inputs | High | RESOLVED — every component finite+nonnegative else no horizons |
| A10 caller-boolean targets | Critical | RESOLVED — `opportunity_state` derived input; malformed intervals → censored |
| A11 weak vintage | High | RESOLVED — init≤issue≤valid chain, archive fields required, ordering enforced |
| A12 incomplete cascades | Critical | RESOLVED — unmapped members rejected; basin rules enforced; region floor |
| A13 weak regime/exp records | High | RESOLVED — ≥3 seeds, digest format, vintage digests bound |
| A14 name-only leakage | Critical | RESOLVED — `field_class` registry + `source_lineage` + denylist + class↔data consistency |
| A15 lossy canonical JSON | High | RESOLVED — strict JSON-native types, `allow_nan=False`, no `default=` |
| A16 shallow CLI | High | RESOLVED — `validate-envelope` recomputes self-hash+digests; finite numeric args |
| A17 thin isolation | Medium | RESOLVED — expanded AST scans + runtime smoke test |
| A18 weak source evidence | High | RESOLVED — `evidence_sidecar_sha256`/`evidence_review_state` fields enforced |
| A19 no eval engines | Critical | GATED_ON_DATA — correct by design; post-P3/P5 only |
| A20 no real-data verification | Critical | GATED_ON_DATA — correct by design; post-P3/P5 only |
| A21 suite-count drift | High | RESOLVED — exact current results published in ledger/manifest |
| A22 no manifest | Medium | RESOLVED — `ARTIFACT_MANIFEST_V0.json` + controlled commit |
| A23 overclaimed statuses | High | RESOLVED — vocabulary reclassified; "eligible"/"verified" removed from unverified sources |
| A24 no claim-lint command | Medium | RESOLVED — `claim-scan` CLI + `scan_claims_text` implemented and tested |

## Honest residual risks

- **P3 human approval is still required** — no code substitutes for it.
- License items (HiAVAL, HMAGLOFDB, zenodo.10895011/7970874/166966)
  are `BLOCKED_EXTERNAL` — sources stay `CANDIDATE_ONLY`.
- ECMWF MARS is procurement-gated (non-member, fee waiver) —
  `BLOCKED_EXTERNAL`.
- Publication-time ≠ issue-time is mitigated by preregistered latency
  margins + `archive_availability`/`local_retrieval` ordering, not
  eliminated; cycle completeness must be spot-checked at intake.
- Feature-field registry is shape-level defense; semantic verification
  of actual feature VALUES requires intake data (A20 — gated).
- Pilot outcome stands at `NO_QUALIFYING_PILOT_SOURCE`.
