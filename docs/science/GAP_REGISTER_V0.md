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

## Post-audit defect register, round 2 (B01–B42)

| ID | Severity | Disposition |
|---|---|---|
| B01 weak approval dates/approver | Critical | RESOLVED — strict ISO/UTC date+timestamp, `approver_attestation`, `approval_scope="design_review_only"`, pilot-rule allowlist |
| B02 outside-root approval files | Critical | RESOLVED — `artifact_root` required; paths must resolve inside it with expected D1/D2 filenames |
| B03 malformed envelope fields | Critical | RESOLVED — `__post_init__` enforces nonempty ID + strict 64-hex digests + digest-map shape |
| B04 empty execution envelopes | Critical | RESOLVED — `STATUS_REQUIRED_RECORDS` graph; every execution status requires its record types |
| B05 unrelated record sets | Critical | RESOLVED — `RECORD_TYPES` allowlist + cross-record foreign keys (source, vertical, opportunity, holdout, vintage) |
| B06 fake sidecar digest | Critical | RESOLVED — `source_evidence_problems` hashes actual sidecar bytes under an evidence root |
| B07 thin source records | High | RESOLVED — `EVIDENCE_VERIFIED` requires all qualification fields incl. coverage/method/cadence/as-of |
| B08 fake pilot-gate sources | Critical | RESOLVED — `PILOT_GATE_PASSED` cross-checked against bound EVIDENCE_VERIFIED sources |
| B09 interval>uncertainty | Critical | RESOLVED — `uncertainty_seconds >= event interval width` enforced |
| B10 duplicate reviewers | High | RESOLVED — `reviewer_ids` must be unique |
| B11 unlinked opportunities | High | RESOLVED — `frame_ids`/`source_id`/`source_as_of` required for observed states |
| B12 caller-asserted controls | High | RESOLVED — `derive_control_state` derives from validated opportunity + intervals |
| B13 unresolved overlap positive | Critical | RESOLVED — ambiguous/unresolved overlap is dominant; POSITIVE needs clean contained adjudicated only |
| B14 incomplete cutoff chain | Critical | RESOLVED — archive/retrieval fields required on `CutoffRecordV0`; full ordering enforced |
| B15 vintage strings only | Critical | RESOLVED — `archive_payload_sha256` + `retrieval_record_sha256` required on admissible vintages |
| B16 incomplete holdout universe | Critical | RESOLVED — complete event→group coverage both directions; named regions; duplicates rejected |
| B17 labels/empty FMX as features | Critical | RESOLVED — `catalog_label` banned as predictor; valid window + vintage digest required; empty feature set rejects |
| B18 claim-scan misses | High | RESOLVED — casefold + separator normalization + structural status-field parsing |
| B19 exposure targets/bad horizons | Critical | RESOLVED — `EXPERIMENT_TARGETS={"occurrence"}`; horizon restricted to policy allowlist; digest non-emptiness |
| B20 empty stability digest | Critical | RESOLVED — `stability_report_digest`, `k_selection_digest`, `source_digests` all required 64-hex |
| B21 cascade dup/invalid split | High | RESOLVED — duplicate event IDs and invalid split names rejected |
| B22 TOCTOU/root ambiguity | High | RESOLVED — `hash_artifact` resolves once, enforces containment, returns relpath+size+sha256 |
| B23 ambiguous manifest head | High | RESOLVED — manifest carries `baseline_head`/`artifact_head`/`repository_relative_root`/`self_excluded` + size+sha256 per file |
| B24 fabricated CLI envelopes | Critical | RESOLVED — `validate-envelope` re-hashes real matrix/policy files and record payloads (`--records-dir`) |
| B25 static-only isolation | Medium | RESOLVED — monkeypatched socket/write-open/cwd test proves no runtime side effects |
| B26 red suite | High | RESOLVED — pinned `.venv` built from `requirements.txt`; `ruptures==1.1.9` present; interpreter ends `/python` |
| B27 unresolved licenses | Critical | BLOCKED_EXTERNAL — unchanged; sources stay CANDIDATE_ONLY |
| B28 archive completeness | Critical | GATED_ON_DATA — per-provider cycle-completeness check is an intake artifact |
| B29 discovery bias rules | High | POLICY_DEFINED — dedup/censoring/negative-frame rules in cutoff policy |
| B30 no qualified pilot | Critical | UNCHANGED — `NO_QUALIFYING_PILOT_SOURCE`; no default vertical |
| B31 power/uncertainty | Critical | POLICY_DEFINED — `power_report_digest` + `uncertainty_method` required on experiments; prospective computation at intake |
| B32 dependence-aware controls | High | POLICY_DEFINED — basin/event-season grouping + embargo enforced in holdout record |
| B33 regime drift untested | High | POLICY_DEFINED — era-boundary/drift rules recorded; execution gated |
| B34 no rolling replay | Critical | GATED_ON_DATA — correct by design |
| B35 thin provenance | High | RESOLVED — manifest records baseline/artifact HEAD, root, interpreter, requirements hash |
| B36 narrative bypass | Critical | RESOLVED — `claim-scan` scans Markdown/JSON/CLI output; normalization catches variants |
| B37 compound event model | High | RESOLVED (shape) — `parent_event_id`/`duplicate_of`/`cascade_group_id` on labels; dedup at intake |
| B38 value-level exposure split | Critical | POLICY_DEFINED — field-class registry enforced at shape level; value-level audit is intake-gated |
| B39 baseline strategy | High | POLICY_DEFINED — climatology/rule/regularized baselines already required; null/ablation added to policy |
| B40 shadow path | Medium | POLICY_DEFINED — unauthorized until post-P7 |
| B41 output artifact binding | High | RESOLVED — experiment requires power/uncertainty artifacts; output manifest binding at execution |
| B42 atomicity/resume | High | POLICY_DEFINED — atomic per-artifact commit + resumable manifest rule recorded |

## Post-audit defect register, round 3 (C01–C30)

| ID | Severity | Disposition |
|---|---|---|
| C01 free-text attestation | Critical | PARTIAL — `approver_role` + exact scope-statement attestation required; true identity authentication remains a human P3 property no code can fake |
| C02 impossible dates | High | RESOLVED — calendar-aware `datetime.strptime` validation |
| C03 sidecar not wired | Critical | RESOLVED — `evidence_root` required whenever verified sources/artifacts/execution statuses are bound; bytes re-hashed in `build_claim_envelope` |
| C04 class-name allowlist | Critical | RESOLVED — exact `type(x) is cls` identity against `RECORD_CLASSES` |
| C05 vacuous FKs | Critical | RESOLVED — parent references are unconditional; empty parent sets reject |
| C06 thin forecast graph | Critical | RESOLVED — `FORECAST_EXPERIMENT_ONLY` requires experiment+vintage+holdout+labels+opportunities+controls+source+artifacts |
| C07 optional CLI bundle | Critical | RESOLVED — execution statuses require `--matrix-path/--policy-path/--records-dir` |
| C08 stale manifest/ledger | High | RESOLVED — manifest V0.2 schema + content-commit/manifest-commit protocol; ledger refreshed post-verify |
| C09 no CI | High | RESOLVED — `.github/workflows/research-v0.yml` added (frozen-diff guard, contract tests, claim lint, disk reserve) |
| C10 inside-root symlink | High | RESOLVED — `hash_artifact` rejects the symlink before resolution |
| C11 ignored optional times | High | RESOLVED — optional cutoff event times validated; vintage-bound cutoffs require them |
| C12 KeyError horizon class | Medium | RESOLVED — `as_event_class` total coercion; invalid input → no horizons |
| C13 raw caller state | Critical | RESOLVED — `assign_target_state_typed`/`derive_control_state_typed` accept exact-typed records only |
| C14 missing uncertainty | High | RESOLVED — `uncertainty_seconds` mandatory; `interval` precision must measure INTERVAL_LE_7D/8_30D |
| C15 unverified label sources | Critical | RESOLVED — execution statuses require labels' sources to be bound AND EVIDENCE_VERIFIED |
| C16 fabricated frames/controls | High | PARTIAL — control window must equal bound opportunity window and agree on state; frame-manifest byte binding arrives at intake |
| C17 unmapped regions | High | RESOLVED — regions must be drawn from declared groups; Langtang-only explicitly rejected |
| C18 unverified feature values | High | PARTIAL — `EvidenceArtifactV0` binds feature matrices to bytes; value-level semantic audit is intake-gated |
| C19 format-only digests | Critical | RESOLVED — experiment/regime digests must equal bound `EvidenceArtifactV0` sha256s verified against real files |
| C20 no feature-matrix record | Critical | RESOLVED — `EvidenceArtifactV0` added with type allowlist + byte binding |
| C21 regex claim scan | Critical | RESOLVED — YAML/single-quote/`=`/escaped/array forms covered |
| C22 fake verified posture | Critical | RESOLVED — verified posture now requires byte-bound sidecar + envelope-level evidence_root |
| C23 unresolved licenses | Critical | BLOCKED_EXTERNAL — unchanged by design |
| C24 ascertainment bias | High | POLICY_DEFINED — detection-opportunity/coverage/dedup rules recorded; measured at intake |
| C25 unqualified verticals | Critical | UNCHANGED — no default pilot; `NO_QUALIFYING_PILOT_SOURCE` |
| C26 no evaluators | Critical | GATED_ON_DATA — correct by design |
| C27 no power results | Critical | GATED_ON_DATA — prospective computation at intake |
| C28 drift untested | High | GATED_ON_DATA — policy recorded |
| C29 atomicity untested | High | POLICY_DEFINED — atomic commit/resume rules documented; exercised at intake |
| C30 worker provenance | High | POLICY_DEFINED — worker manifest protocol recorded |

## Post-audit defect register, round 4 (D01–D12)

Self-audit after external advisor unreachable (codex CLI config error,
gemini CLI arg mismatch, OpenRouter key expired — recorded as tool
truth).

| ID | Defect | Disposition |
|---|---|---|
| D01 relative paths resolve to CWD | High | RESOLVED — `_resolve_against` binds relative paths to declared root |
| D02 evidence_root outside artifact_root | High | RESOLVED — evidence_root must resolve inside artifact_root |
| D03 same file as matrix+policy | Medium | RESOLVED — distinct-resolved-path check |
| D04 non-string blockers | Low | RESOLVED — entries must be non-empty strings |
| D05 artifact_root = `/` | Medium | RESOLVED — filesystem root rejected |
| D06 within-group cascade duplicates | High | RESOLVED — per-group member-ID uniqueness |
| D07 label/source version mismatch | High | RESOLVED — bound-source version must equal declared `source_version` |
| D08 unbound lineage refs | Medium | RESOLVED — `parent_event_id`/`duplicate_of` must reference bound event IDs; self-reference rejected |
| D09 duplicate frame_ids | Low | RESOLVED — uniqueness enforced |
| D10 evidence_root not in envelope | Low | RESOLVED — echoed when bound |
| D11 CI guard `origin/main` fragility | Medium | RESOLVED — push/PR split, `fetch-depth: 0`, `github.event.before` |
| D12 name-keyed status graph | Info | ACCEPTED — names are display keys; identity enforced by `type(x) is cls` upstream |

## Post-audit defect register, round 4 extended (E-series)

| ID | Defect | Disposition |
|---|---|---|
| E01 CLI no typed reconstruction | Critical | RESOLVED — `deserialize_record` enforces exact type tags, declared fields, no extras; CLI replays `_validate_record` + status graph + cross-record + byte evidence |
| E02 CLI paths unbound to envelope | Critical | RESOLVED — envelope carries canonical artifact_root/matrix_path/policy_path; CLI resolves those exact files, rejects supplied alternatives |
| E03 forecast graph missing vertical spec | Critical | RESOLVED — `HazardVerticalSpecV0` required for FORECAST_EXPERIMENT_ONLY |
| E04 artifact role confusion | Critical | RESOLVED — `ARTIFACT_ROLE_TYPES` + per-role artifact-type binding; `STATUS_REQUIRED_ARTIFACT_TYPES` coverage enforced |
| E05 vintage digests not byte-bound | Critical | RESOLVED — `archive_payload_path`/`retrieval_record_path` required and byte-verified under evidence_root |
| E06 sidecar bytes ≠ evidence content | Critical | RESOLVED — sidecar must be JSON binding source_id/version/license/reviewers/decision to the record |
| E07 regex-only dates, loose types | High | RESOLVED — calendar-aware `_date` everywhere; bool/int/NaN exclusions |
| E08 optional issue time / horizon | Critical | RESOLVED — vintage `issue_time` required; horizon must be admissible for every bound label's measured class; vintage↔cutoff linkage required |
| E09 ghost holdout IDs | Critical | RESOLVED — `event_assignments` keys must equal the bound event universe exactly |
| E10 cascade not enforced | Critical | RESOLVED — cascade groups derived from bound labels; `cascade_atomicity_problems` invoked during graph binding |
| E11 caller-asserted control state | Critical | RESOLVED — declared state must equal `derive_control_state_typed` recomputation |
| E12 duplicate record IDs | High | RESOLVED — per-type primary-ID uniqueness |
| E13 unbound regions | High | RESOLVED — regions must be drawn from locked test groups |
| E14 freeze-like statuses | Critical | RESOLVED — no status carries "ready/authorized/qualified"; `PILOT_GATE_PASSED` requires bound verified sources |
| E15 envelope missing provenance | High | RESOLVED — envelope echoes canonical root, D1/D2 paths, record type tags, evidence_root |
| E16 scanner misses deep forms | Critical | RESOLVED — unicode escapes, array members, `=` separators, numeric truthy, `yes/on` all detected |
| E17 feature value inspection | Critical | GATED_ON_DATA — value-level matrix audit is an intake artifact; structural + byte binding now enforced |
| E18 no output/metric artifacts | Critical | RESOLVED — FORECAST_EXPERIMENT_ONLY requires feature_matrix+forecast_output+power_report+uncertainty_report artifact types |
| E19 regime provenance | High | RESOLVED — REANALYSIS-only data_class, ≥3 distinct nonneg seeds, `null_model_digest` bound to null_model artifact |
| E20 cutoff unbound | High | RESOLVED — cutoff requires event_id+source_id when vintage-bound; every bound vintage must have a bound cutoff |
| E21 cross-source targets | High | RESOLVED — typed target functions require label.source_id == opportunity.source_id |
| E22 root races/symlinks | High | RESOLVED — symlink-before-resolution, canonical roots, filesystem-root rejection; TOCTOU documented out of authority path |
| E23 code can't authenticate humans | Critical | POLICY_DEFINED — `human_approved` remains a human P3 property; code enforces structure only |
| E24 string-only reviewer state | High | PARTIAL — reviewer IDs + sidecar reviewer_ids required; independence declaration is a human gate |

## Governance policies (O05)

- Artifact retention: governed artifacts live only under `docs/science`,
  `nepal/research_v0`, `tests/test_research_v0_*`, `.github/workflows`,
  and `README.md` — the manifest scope. Intake artifacts (raw source
  bytes) are excluded from this manifest and must carry their own
  per-source license record before they may exist in the tree.
- Privacy/redaction: reporter or observer-identifying fields are
  prohibited in committed artifacts; source sidecars record license,
  coverage, timing, and review metadata only.
- Access: all authority-bearing fields (`human_approved`,
  `approver_*`) are structural claims, not authentication; the P3
  decision requires external, authenticated human attestation.
- Deletion/quarantine: failed or fabricated artifacts are quarantined
  (never silently dropped) and recorded with reason.

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
