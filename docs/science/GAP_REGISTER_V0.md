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

## Confirmation-run defect register (CFM-series)

| ID | Defect | Disposition |
|---|---|---|
| CFM-01 complete input absent | Critical | RESOLVED (superseded by Run A completion) — merged file + feature CSV now exist under `research_runs/gmm_hybrid_20260915/`; see "Run A result" in P0_BASELINE_LEDGER and `run_a/RUN_A_EVIDENCE_SUMMARY_V0.md` |
| CFM-02 single-cell hard-coding | High | RESOLVED — outputs labelled `EXPLORATORY_DESCRIPTIVE_SINGLE_CELL`; scope note limits inference to the cell/period |
| CFM-03 silent feature reduction | High | RESOLVED — declared `GMM_FEATURES` required; missing columns fail the run; missingness reported |
| CFM-04 mixed units | High | RESOLVED — baseline-fitted StandardScaler + recorded feature units; scaler never sees target rows |
| CFM-05 wind direction / rolling window | Medium | RESOLVED — circular mean via mean-resultant components; `pdd_7day` requires full 7-day window |
| CFM-06 single-seed K selection | High | RESOLVED — three declared seeds (42, 7, 2024); per-seed BIC/AIC/convergence; modal-K frequency reported |
| CFM-07 no stability measure | High | PARTIAL — seed + temporal block bootstrap implemented; basin/geographic stability is data-gated |
| CFM-08 JS distance without uncertainty | High | RESOLVED — block-bootstrap 95% CI + within-baseline null p95; `exceeds_null` flag |
| CFM-09 invisible membership confidence | Medium | RESOLVED — mean/median max-posterior + ambiguous fraction reported |
| CFM-10 pass-on-error | High | RESOLVED — preflight failure exits 1 with no artifacts; failed runs are quarantined with recorded error |
| CFM-11 unbound results | High | RESOLVED — run bundle (`research_runs/gmm_confirmation/bundle.json`) records input/feature/output digests, config, env, seeds |
| CFM-12 overlay mistaken for detection | High | RESOLVED — fit and retrospective overlay separated; explicit note that event dates never touch fitting |
| CFM-13–CFM-16 multi-basin, association, forecast, P3/licenses | Critical | GATED_ON_DATA/BLOCKED_EXTERNAL — no change |

## P5 intake I/O contract (P5-A/B round)

| ID | Defect | Disposition |
|---|---|---|
| P5-01 writes under frozen `data/` | Critical | RESOLVED — `--run-root` on downloader/extractor/GMM; default `research_runs/gmm_confirmation/`; frozen roots rejected |
| P5-02 ZIP-wrapped CDS payload | Critical | RESOLVED — magic-byte detection (ZIP/HDF5/CDF); single-`.nc` safe unwrap; `valid_time`→`time` canonicalized |
| P5-03 `snow_depth`→`sde` semantics | Critical | RESOLVED — request `snow_depth_water_equivalent`; `sde` explicitly rejected, never renamed to `sd` |
| P5-04 merge hardcodes `time` | Critical | RESOLVED — canonicalized time axis, sorted/deduped, per-month hourly completeness recorded |
| P5-05 lat tie at 28.25 | Medium | RESOLVED — deterministic tie rule (higher lat 28.3); requested vs selected cell recorded |
| P5-06 missing vars → NaN | High | RESOLVED — `extract_raw_features` raises ValueError; sde-specific error |
| P5-07 raw/derived interface | Medium | RESOLVED — 7 raw vars required in source; 12-col daily schema documented + `feature_units.json` sidecar |
| P5-08 post-event rows | High | RESOLVED — 2026-08 request capped at day 25; extractor asserts no rows ≥ 2026-08-26 |
| P5-09 invalid calendar days | Medium | RESOLVED — `calendar.monthrange` day generation |
| P5-10 resume claims | Medium | RESOLVED — fresh-run roots only; `--force` or timestamped dir; no resume claim |
| CFM rolling PDD across gaps | High | RESOLVED — rolling within contiguous-date runs only |
| CFM post-dropna missingness | Medium | RESOLVED — per-column NaN computed pre-filter |
| CFM non-converged BIC winner | High | RESOLVED — converged-only K eligibility; all-fail → error |
| CFM preflight coverage | Medium | RESOLVED — ≥92 JJA rows/yr 2001-25; ≥86 pre-event rows 2026; post-cutoff rows rejected |
| CFM bootstrap count clobber | Medium | RESOLVED — 200 replicates honored; `n_replicates` reported |
| CFM modal-K tie | Medium | RESOLVED — `k_instability` reported when frequency < 1.0 |
| CFM bundle run-root/UTC | Medium | RESOLVED — UTC run_id, run_root, requested/actual cell |
| TEST-01 fixture tests | — | RESOLVED — `tests/test_p5_io_contract.py`, 22 tests incl. production-function and end-to-end layout probes |

## P5 integration round (downloader→extractor→GMM chain)

| ID | Defect | Disposition |
|---|---|---|
| P5-01 shared run root | Critical | RESOLVED — one top-level `RUN_ROOT` (`research_runs/gmm_confirmation/`); downloader `raw/monthly/merged/`, extractor `features/`, GMM `gmm/`; stages chain without copying |
| P5-02 GMM `--run-root` | Critical | RESOLVED — GMM reads `RUN_ROOT/features/`, writes `RUN_ROOT/gmm/`; all `data/` input dependence removed |
| P5-03 sd/SWE contract map | Critical | RESOLVED — additive mapping `snow_depth_water_equivalent → sd` in downloader+extractor; frozen contract file untouched; legacy mismatch documented |
| P5-04 fuzzy `snow_depth` acceptance | Critical | RESOLVED — exact-name acceptance only; `snow_depth`, `sde`, ambiguous names rejected |
| P5-05 cell provenance | High | RESOLVED — `run_metadata.json` (requested/selected cell, elevation, digests) written by extractor, bound into GMM results+bundle with mismatch flags |
| P5-06 unit contradiction | High | RESOLVED — `feature_units.json` sidecar consumed; `units_source` recorded; embedded defaults only as fallback |
| P5-07 timestamp/month validation | High | RESOLVED — exact UTC hourly set per month verified; wrong-month, duplicate, post-cutoff rejected |
| P5-08 completeness gate | Critical | RESOLVED — merge only when all months complete; `complete.json` marker after validation; `incomplete` status blocks downstream |
| P5-09 exact JJA universe | High | RESOLVED — exact 92-date/yr + 86-date 2026 universe; duplicates, non-JJA, non-finite, post-cutoff rejected |
| P5-10 disk reserve | Critical | RESOLVED — 8 GiB `shutil.disk_usage` guard before/during/after acquisition |
| P5-11 callable guards | Medium | RESOLVED — `ensure_not_frozen`/`_assert_safe_root` applied in callable functions, not just CLI |
| P5-13 CI | High | RESOLVED — P5 tests + dry-run + `--smoke` NetCDF gates added to research-v0 workflow |
| P5-14 register drift | High | RESOLVED — this round's table reflects verified live code |
| P5-15 `research_runs/` ignore | Medium | RESOLVED — added to `.gitignore` |
| P5-16 env smoke | Medium | RESOLVED — `--smoke` NetCDF round-trip gate (CI + CLI) |
| P5-C acquisition | Critical | RESOLVED (completed) — bounded Run A executed 2026-09-15 via hybrid ARCO/EDH/MARS route under P5-C authorization; route-vs-authorization reconciliation recorded in `run_a/HYBRID_ROUTE_RECONCILIATION_V0.md` (H01: owner-ratified amendment or method-only retention — owner decision) |

## P5 chain-execution round (JJA universality + edge censoring)

| ID | Defect | Disposition |
|---|---|---|
| AUD-01 command sequence | Medium | RESOLVED — README documents the 3-command chain with shared run root |
| P5-01 merged-path mismatch | Critical | RESOLVED — GMM bundle scans `run_root/merged/` (no `era5/` nesting) |
| P5-02 year-range parser | Critical | RESOLVED — `parse_year_range` enforces 2001≤start≤end≤2026, exits 1 before cdsapi import |
| P5-03 non-JJA resample rows | Critical | RESOLVED — daily frame filtered to JJA before rolling; 6,831 gap rows eliminated; `non_jja_rows_dropped` reported |
| P5-04 edge-censored rows | Critical | RESOLVED — `edge_censored` bool column; preflight accepts NaN only in `pdd_7day` on flagged rows; 156 rows classified, dropped pre-fit; `rows_used`/`edge_censored_dropped` in results |
| P5-05 canonical dry-run JSON | High | RESOLVED — full request dict per month, no client construction |
| P5-07 monotonic/canonical index | High | RESOLVED — preflight rejects non-monotonic and non-00:00 timestamps |
| P5-08 full-chain integration test | High | RESOLVED — `TestFullChain`: merged NetCDF → extract → derive → thermal → GMM → bundle digest |
| P5-14 ledger drift | High | RESOLVED — counts + commit refs rebound in manifest |
| P5-15 skip disclosure | Medium | RESOLVED — CI P5 step runs `-rs`; skip comment added |
| P5-16 README path clarity | Medium | RESOLVED — P5 confirmation-only diagnostic section added |

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

## Run A reconciliation round (H01–H15, 2026-09-15)

Post-completion audit of the hybrid Run A against its authorization
and ledger.  Lanes: `run-a/reconciliation` (code+docs), Run B evidence
addendum (license metadata), integrator ledger/register updates.

| ID | Disposition |
|---|---|
| H01 route vs authorization | DOCUMENTED — hybrid ARCO/EDH/MARS route recorded in `run_a/HYBRID_ROUTE_RECONCILIATION_V0.md`; owner decision pending (amend authorization or retain as method-only) |
| H02 provenance receipts | RESOLVED (bounded) — `provenance_receipts.json` binds endpoint, payload digests, sizes, mtime-derived retrieval UTC, license URLs; unrecoverable fields (job IDs, request digests) marked `UNVERIFIED` |
| H03 route harmonization | PARTIAL — local numeric comparison over 5 overlap months: u10/v10 bitwise-identical, t2m/d2m <=2.4e-4 K, sd/sf <=3.6e-6 m; tp is a semantics difference (running accumulation vs per-hour increment); full equivalence NOT demonstrated → Run A retained as implementation-confirmation |
| H04 non-JJA request scope | RESOLVED — ledger records requested vs used windows; merged output asserted JJA-only + pre-cutoff (`used_window_verified`) |
| H05 invalid calendar days | RESOLVED — request chunks built from `days_for_month`; month/day cross-products always valid; regression tests incl. leap-Feb and Aug-25 cutoff |
| H06 ledger totals zero | RESOLVED — `size_accounting` measured from bytes at close; existing ledger repaired (40.19 MB) |
| H07 naive timestamps | RESOLVED — UTC `Z` emission; legacy naive values preserved + `*_utc` normalization in repair tool |
| H08 auditable summary | RESOLVED — `run_a/RUN_A_EVIDENCE_SUMMARY_V0.md` binds run id, digests, counts, limitations |
| H09 replay completeness | RESOLVED — scaler params (mean/var/scale + feature order), full GMM config, best-model weights/means/covariances persisted; canonical JSON (sorted keys, strict natives, no `default=str`) |
| H10 CI claim-scan | RESOLVED — `claim-scan` recurses directories; CI uses identical file selection; nested-forbidden-token regression test added |
| H11 stale README | RESOLVED — Run A narrative corrected; `.venv/bin/python` consistent |
| H12 stale ledger/register | RESOLVED — P0 rebound to current content head; CFM-01 and P5-C rows corrected; this section added |
| H13 scope clarity | RESOLVED — `run_a/SCOPE_OVERLAY_V0.md` separates P3 design review, P5 diagnostic authorization, and future source-qualification/intake gates |
| H14 env disclosure | RESOLVED — rasterio skips + warnings disclosed in README; suite not claimed dependency-complete |
| H15 spec-vs-data status | RESOLVED — Run B/C labeled `SPECIFICATION_COMPLETE` with real data pending; no synthetic result presented as science |

## Newly discovered defect (audit-adjacent)

| ID | Severity | Disposition |
|---|---|---|
| RA-01 `sf_daily` inflation | High | CONFIRMED — CDS/MARS `sf` arrives as a running daily accumulation while ARCO `tp` is per-hour; the extractor summed the accumulated field, inflating `sf_daily` ~5.7x (e.g., 41.0 mm vs 4.04 mm closing value). Affects `features/` output of Run A only; fix = deaccumulate or take closing value. Recorded, NOT reprocessed — Run A stays method-only pending owner decision on rerun |

## Reconciliation round 2 (RA/RUN series, 2026-09-15)

Post-audit round-2 dispositions.  Corrected Run A derivative produced
from existing raw bytes — no new download, original preserved as
immutable evidence.

| ID | Disposition |
|---|---|
| RA-01 sf_daily inflation | RESOLVED — running-accumulation detected per-series; closing-value aggregation (00:00 stamp closes prior day; last in-day value + partial flag at boundary). 26 boundary-partial days: 25 x Aug-31 (closing stamp Sep-1 00:00 outside JJA) + 2026-08-25 (last pre-cutoff day lacks its Aug-26 close). Corrected derivative `gmm_hybrid_corrected_20260915` (run_id `gmm_confirmation_20260915T111937Z`): K=5 recomputed, JS 0.2702, all digests regenerated. Mean sf_daily 13.3 -> 1.06 mm. Auditor-found latent defect (sparse-snow reset dilution) fixed + regression-tested. |
| RA-02 naive timestamps | RESOLVED — repaired ledger keeps naive originals verbatim + `*_utc` fields + `timestamp_classification: legacy_naive_local_with_utc_normalization` |
| RA-03 stale counts/heads | RESOLVED — P0 current-verification snapshot updated (1300 passed); content-head deferred to manifest field |
| RA-04 receipt gaps | RESOLVED-BOUNDED — UNVERIFIED preserved for job IDs/request digests; per-route payload attribution fixed |
| RA-05 route authorization | DOCUMENTED — method-only pending owner ratification (reconciliation doc presents both options) |
| RA-06 harmonization | PARTIAL — 5-month overlap quantified; untested months marked unverified; tp semantics documented |
| RA-07 non-JJA request window | RESOLVED — requested-vs-used windows + JJA-only merged assertion |
| RA-08 expver=0005 | DOCUMENTED — preliminary 2026-08 sd/sf disclosed in derivation ledger; accepted for method-only use |
| RA-09 artifact rehash | RESOLVED — `rehash_report.json` per run root; 183 + 16 files hashed; all recorded digests match |
| RA-10 governed summary | RESOLVED — evidence summary + derivation ledger audit the result without vendoring raw bytes |
| RUN-01 spec-vs-data | RESOLVED — Run B/C remain `SPECIFICATION_COMPLETE; REAL_DATA_PENDING` |

| SRC-01..08 | PARTIAL — license evidence resolved via metadata APIs (see addendum); Nepal counts, schemas, extent splits, registration, and cycle completeness remain `GATED_ON_DATA`/`BLOCKED_EXTERNAL` at intake |
| DATA-01..05, FMX-01..02, REG-01..03, ASSOC-01, FCST-01..02 | GATED_ON_DATA — protocols exist (run_b/run_c specs); no real intake authorized |
| OPEN-01 | RESOLVED — `OPEN_DISTRIBUTION_NOTE_V0.md` scopes publishable vs external-restricted bytes |
| OPS-01 | DEFERRED — explicit deferral; no prospective/authority path |

## Reconciliation round 3 (P0/DOC/RUN/ENV, 2026-09-15)

| ID | Disposition |
|---|---|
| P0-01 count discrepancy | RESOLVED — census at that date: `--collect-only` = 1311 nodes = 1306 passed + 5 rasterio skips; manifest's 1306 verified correct (historical, superseded by Round-7 census). External 1300 was a stale/env-differentiated observation. |
| DOC-01 historical vs current counts | RESOLVED — README and P0 label 1282 as ledger-time history, 1306 as current; historical evidence retained |
| DOC-02 stale extractor comments | RESOLVED — both comments now state route-dependent semantics (ARCO increments vs MARS/EDH running accumulation) |
| RUN-01 derived-run provenance | RESOLVED — derivation ledger carries `run_kind=derived`, `input_run_bundle_sha256`, `acquisition_disk_check=not_applicable_derived_run` |
| ENV-01 warnings/skips | RESOLVED — 57 warnings at the verified round-5 baseline (supersedes this row's original 2026-09-15 count of 56): 56 xarray/netCDF4 deprecations in `test_p5_io_contract.py` + 1 sklearn `ConvergenceWarning` in `test_science_v0_regimes.py`, all library-level; skips disclosed |
| SRC-*/DATA-*/FMX-*/REG-*/ASSOC-*/FCST-* | UNCHANGED — remain `GATED_ON_DATA`/`BLOCKED_EXTERNAL` pending authorized real-data intake; no fabricated closure |
| OPS-01 | UNCHANGED — deferred by design |

## License-evidence resolution (S-series partial)

`run_b/SOURCE_EVIDENCE_ADDENDUM_V0.md` resolved license tags via
public metadata APIs (2026-09-15): HiAVAL v1.3.0 = CC0 (v1.1.0 was
CC BY 4.0 — conservative term CC BY 4.0 governs); HMAGLOFDB = CC BY
4.0 on canonical RDS (Zenodo mirror CC0); Kneib S1 = CC BY 4.0;
Burrows = CC BY 4.0; Jiang LDOF = CC BY 4.0; essd-2026-481 = CC BY
4.0 (canonical versioned DOI zenodo.19477908); SAFE-HMA = CC BY 4.0;
ds084001 GFS = CC BY 4.0; TIGGE/S2S per-centre CC BY/CC BY-NC split
documented.  **Adverse/new:** Zhong RIA `access_right=restricted`
(request-gated despite CC BY tag); Gnyawali-Adhikari record has no
license field and non-USGS originators (public-domain presumption
rejected); figshare.25988293 is GDW v1.0, not GRanD v1.3.  Remaining
intake-gated items: Nepal subset counts, file schemas, extent splits,
NGDC 166966 license.  These resolve G05/B27/C23 license sub-items
only — `EVIDENCE_VERIFIED` posture still requires byte-bound sidecar
+ independent review at intake.

## Swarm-C convergence dispositions (2026-09-15)

| ID | Disposition |
|---|---|
| I-01 forged digests | RESOLVED — adapter recomputes assignment/regime/freeze digests from the payload; tamper tests reject forged values |
| I-02 caller-overridden provenance | RESOLVED — label_blinding/fitted_on/mode read from payload; missing/false/mismatched reject |
| I-03 primitive coercion | RESOLVED — str unit_id/date required; only int regime_id converts |
| I-04 modal-K tie | RESOLVED — `_modal_k` picks smallest K on ties; unit-tested |
| I-05 artifact provenance | RESOLVED — feature order, FMX digest, config digest, fit/heldout groups, mask digest bound |
| I-06 shallow freeze | RESOLVED — deep copy; post-freeze nested mutation detected by digest verification |
| I-07 unbound train_mask | RESOLVED — length/dtype/nonempty-both-sides + declared group membership enforced |
| I-08 unverified control lineage | RESOLVED — `run_association` requires an opportunity registry; existence/unit/window/state verified; NEGATIVE requires OBSERVED_FULL |
| I-09 case lineage | RESOLVED — ForecastCase carries opportunity_id/outcome_source_id/cutoff_time; evaluate() fails closed when empty |
| I-10 order-sensitive digest | RESOLVED — canonical (case_id, opportunity_id) sort + baseline realignment before hashing; permutation-invariance tested |
| I-11 audit scope | RESOLVED — B4 audit labeled experiment-layer; replay bundles now carry the opportunity registry |
| DOC-01 count drift | RESOLVED — README/P0 record 1282/1306/1641 as dated history; 1687 at post-audit head (superseded 2026-09-16 by the round-5 census: 1875/5/0/57 at `content_head` 99ccec9, manifest commit `4e2ef91`; historical, superseded by Round-7 census) |
| DOC-02 metadata-only scope | RESOLVED — SOURCE_FEASIBILITY_RECORDS_V0 labeled dated metadata-only snapshot |
| GOV-01/02 | PARTIAL — the scope-separation part is RESOLVED (P3 design-only vs P5-C acquisition; see STATUS_SCOPE_RECONCILIATION_NOTE_20260915); GOV-02's bounded exercised-governance record now exists (`GOVERNANCE_EXERCISE_RECORD_V0.md`, added 2026-09-16); GOV-01's downstream human approvals remain open-by-design — no code substitutes for owner sign-off |
| H01/H03 | RESOLVED (wording) — Run A docs state hybrid route, method-only, bounded 5-month overlap, no K/JS transfer |

SRC-*/DATA-*/FMX-*/OPEN-01/GOV-03/OPS-01 remain
`GATED_ON_DATA` / `BLOCKED_EXTERNAL` / deferred — unchanged by design.

## Swarm-D convergence dispositions (2026-09-15, Round-4 audit)

| ID | Disposition |
|---|---|
| REG-01 calendar-bridging blocks | RESOLVED — bootstrap blocks are calendar ranges at declared cadence; `gap_policy="calendar"` only; cadence/gap/block-len bound in config digest + bootstrap record |
| REG-02 LORO semantics | RESOLVED — decisive metric is `ari_eval`: label-invariant agreement between reference and fold models on the excluded group's rows; locked-group coverage reported |
| REG-03 one-seed bypass | RESOLVED — `seed_policy` gate requires `fold_seed_policy="all"` for terminal stability; failed seeds demote |
| REG-04 era-blind nulls | RESOLVED — season-matched null resamples within (season, era) joint strata when era_col bound |
| REG-05 selection-inconsistent nulls | RESOLVED — every null replicate replays the declared BIC K-sweep; `null_k_distribution` recorded |
| REG-06 partial null digest | RESOLVED — null digests cover both families, seeds, replicate stats, selection metadata |
| REG-07 declared missingness unused | RESOLVED — policy applies to primary fit surface; stratified bands REFIT preprocessing+GMM per band |
| REG-08 silent effort NA | RESOLVED — undeclared effort without waiver FAILs; declared effort refits per declared strata policy |
| REG-09 era bypass | RESOLVED — era column without declared boundaries FAILs; era_col=None requires explicit waiver |
| REG-10 elevation escape | RESOLVED — numeric unit-bound elevation required; 1-D elevation ablation gates promotion (`elev_ablation_ari_max`) |
| REG-11 ungoverned frames | RESOLVED — `source_manifest` mandatory: `{fixture: true}` or bound `{source_id, source_digests, units, feature_allowlist, lineage}`; allowlist enforced against requested features |
| REG-12 preprocessing binding | RESOLVED — row_keys_digest + imputer/scaler + train mask bound; freeze rejects missing preprocessing |
| REG-13 CANDIDATE associable | RESOLVED — CANDIDATE_ONLY non-terminal for association; adapter rejects it; `terminal`/`associable` fields bound |
| REG-14 self-consistent forgery | RESOLVED — auditor requires the full declared gate universe; freeze recomputes every bound digest; fabricated gates cannot close |
| PROV-01 source/env/run binding | RESOLVED — source_manifest + environment_digest + run_manifest_digest required fields; non-fixture manifests need digests/units/allowlist/lineage |
| PROV-02 local-artifact bypass | RESOLVED — association consumes only serialized producer payloads; CANDIDATE/UNSTABLE rejected at adapter |
| PROV-03 malformed model | RESOLVED — finite non-negative summing-to-1 weights, finite means, square symmetric PSD covariances, k-consistency |
| ASSOC-01 arbitrary horizons | RESOLVED — look-back horizons strictly parsed; events wider than the horizon are excluded per cell with counted `precision_exclusions` |
| ASSOC-02 bootstrap-as-p | RESOLVED — Holm family runs on stratified label-shuffle permutation p-values; bootstrap intervals stay uncertainty-only |
| ASSOC-03 incomplete correction | RESOLVED — per-cell permutation p-values feed the single Holm family over regime x horizon x placement |
| ASSOC-04 missing spatial null | RESOLVED — geography-preserving date-shift null (`spatial_shift`) required; enriched regimes must beat it |
| ASSOC-05 per-cell null coverage | RESOLVED — every family cell carries an executed null (`null_coverage` bound in multiplicity) |
| ASSOC-06 diagnostic sensitivities | RESOLVED — axes dispositioned PASS/FAIL/NA with reasons; FAIL blocks support |
| ASSOC-07 hollow supported | RESOLVED — problems() revalidates family_pvals ranges, null coverage, Holm rejection per promoted regime, event-group floor |
| ASSOC-08 interval precision | RESOLVED — interval width propagated; precision-inadmissible events excluded per cell |
| EVAL-01 slice denominators | RESOLVED — met-season slices use opportunity window-midpoint membership; non-attributable axes count linked opportunities only |
| EVAL-02 missing-feed | RESOLVED — declared scenarios execute; scenario-specific denominators/metrics recorded |
| EVAL-03 clustering | RESOLVED — bootstrap clusters by `event_group_id` else (unit, season); `clustering_unit` exposed |
| EVAL-04 unbound power | RESOLVED — `power_design` binds to the declaration; diagnostic-only when absent |
| EVAL-05 direct construction | RESOLVED — typed `ForecastExperimentDeclaration` required for bound status; mappings convert strictly |
| FCST-01 caller-asserted groups | RESOLVED — `row_keys` ("unit_id|date") bind membership; uniqueness/alignment enforced at fit boundary |
| FCST-02 caller-supplied baselines | RESOLVED — `baseline_evidence` digests recompute over supplied vectors; unbound baselines marked `caller_supplied_fixture` |

SRC-01..07, DATA-01..07, RUN-01, REVIEW-01, GOV-01, ENV-01, PILOT-01
remain `GATED_ON_DATA`/`BLOCKED_EXTERNAL`/decision-pending —
unchanged by design; `NO_QUALIFYING_PILOT_SOURCE` stands.

## Honest residual risks

- **P3 design-review attestation is owner-directed and design-only**
  (see `STATUS_SCOPE_RECONCILIATION_NOTE_20260915.md`); it does not
  authorize acquisition — P5-C remains a separate bounded
  authorization. No code substitutes for owner sign-off.
- Nepal-coverage counts and per-record intake verification remain
  `GATED_ON_DATA`; NGDC 166966 license is `BLOCKED_EXTERNAL`;
  sources stay `CANDIDATE_ONLY` until intake sidecars verify.
- ECMWF MARS is procurement-gated (non-member, fee waiver) —
  `BLOCKED_EXTERNAL`.
- Publication-time ≠ issue-time is mitigated by preregistered latency
  margins + `archive_availability`/`local_retrieval` ordering, not
  eliminated; cycle completeness must be spot-checked at intake.
- Feature-field registry is shape-level defense; semantic verification
  of actual feature VALUES requires intake data (A20 — gated).
- Pilot outcome stands at `NO_QUALIFYING_PILOT_SOURCE`.

## Round-5 clarification — license metadata vs payload qualification (2026-09-16)

Earlier rounds use the word "license" for two distinct questions;
readers should not conflate them:

- **Metadata license resolution — done (metadata only).** The S-series
  addendum (`run_b/SOURCE_EVIDENCE_ADDENDUM_V0.md`) resolved license
  *tags* via public metadata APIs — see "License-evidence resolution
  (S-series partial)" above. Rows such as B27/C23 marked
  `BLOCKED_EXTERNAL` are not contradicted by that section: those rows
  track procurement, registration, and access-path blockers (e.g.,
  ECMWF MARS procurement, request-gated records), not tag lookup.
- **Payload qualification — not done.** Access authorization, Nepal
  subset counts, file schemas, coverage/extent splits, timing
  precision, byte-bound evidence sidecars, and independent review are
  intake artifacts. No source is `EVIDENCE_VERIFIED`; every source
  remains `CANDIDATE_ONLY`, and `NO_QUALIFYING_PILOT_SOURCE` stands.

## Round-5 closure — codeable enforcement residuals (2026-09-16)

All codeable gaps from the Round-5 conformance audit are closed in
this commit range; every closure carries a behavioral test in
`tests/test_round5_hardening.py` or the lane suite. Data-gated and
external items (SRC-*, DATA-*, GOV-*, FCST-EXT-*, REVIEW-01,
RUN-A-01 ratification, OPS-01) remain open by design.

| Gap | Closure |
|---|---|
| GOAL-01 | `docs/science/MISSION_SCOPE_OVERLAY_V0.md` — versioned scope overlay; frozen preregistration untouched. |
| PROV-01/02 | `REQUIRED_REGIME_GATE_NAMES` in `research_v0.gates` is the single gate universe enforced identically at producer emit, `freeze_regime_artifact`, the adapter, replay, and `audit_producer_payload` — missing, extra, false, and malformed gates all reject. |
| PROV-03 | Non-fixture `source_manifest` requires `evidence_root` in addition to source digests, units, allowlist, and lineage — producer preflight and auditor both enforce. |
| PROV-04 | `RegimeAssignmentArtifact.producer_payload_digest` binds the artifact to the frozen payload's `freeze_digest`; `problems()` rejects bare hand-built artifacts. |
| REG-01 | `run_regimes` rejects units appearing in more than one geographic group — the unit→basin binding is a partition. |
| REG-02 | `bootstrap_block_len` must be a positive preregistered int; the declared cadence must equal the finest observed within-unit spacing with all diffs integer multiples of it (1H-on-daily, 2D-on-daily, off-grid rows all fail pre-fit). |
| REG-03 | The missingness-selected surface (`fit_sel_full`) feeds LORO fold fits, the temporal bootstrap resample pool, season/elevation refits, the elevation ablation, and the season-matched null generator. |
| REG-04 | `effort_split` is validated (`median`/`tercile`/`first10`/`quantile:q`) and the resolved strata policy is executed and recorded. |
| REG-05 | `_null_envelope` serializes per-replicate `{i, gen_seed, fit_seed, k, stat, ok}` and the family digest covers it. |
| REG-06 | `run_b/REGIME_PROTOCOL_V0.md` status taxonomy aligned to the code's three terminal statuses + `RUN_ERROR`; waivers/N-A dispositions are bound and non-promoting. |
| ASSOC-01 | `SPATIAL_SHIFT_OFFSETS` = 24 deterministic nonzero day shifts (p-resolution ≈0.042 < 0.05); `MIN_SPATIAL_SHIFTS=20` enforced on caller overrides. |
| ASSOC-02 | `ALLOWED_LOOKBACK_DAYS` binds declared horizons to the policy horizon allowlist (`0d` identity + {2,3,7,14,30}d). |
| ASSOC-03 | `AssociationReport.problems()` recomputes every carried negative-control digest over its creation subset. |
| EVAL-01 | `evaluate()` populates `degradation` from declared `degradation_scenarios` or the deterministic default grid (fraction dropouts + per-region unit dropout) — never empty. |
| EVAL-02 | Uncertainty bootstrap resamples `event_group_id`-else-`unit+season` clusters — the dependence unit, not raw region+season. |
| EVAL-03 | `FORECAST_EXPERIMENT_ONLY` requires a complete bound declaration (vintage lineage, ablations, feature digest, threshold) plus byte-bound baseline evidence — fixture-only calls cap at `UNDERPOWERED_DESCRIPTIVE_ONLY`. |
| EVAL-04 | Slice denominators carry explicit registry attribution via `scope_rule` (`region_owned_basins`, `opportunity_window_midpoint_season`, `linked_opportunities_only`) with verified/censored id tuples. |
| DOC-01..04, GOAL-01 | Census reconciliation (1875/5/0/57 — historical, superseded by Round-7 census), Run-A wording, license clarification, mission overlay, legacy-path caveats. |
| CI-01 | Workflow triggers cover `nepal/science_v0/**`, `nepal/experiment_v0/**`, their tests and fixtures; focused contract-test step added. |
| ENV-01 | CI runs Python 3.14 with `requirements.txt` pins; the 5 rasterio skips are disclosed as excluded-gate skips (rasterio intentionally outside the supported lock). |

Unchanged boundaries: `NO_QUALIFYING_PILOT_SOURCE`;
`WARNING_PATH_AUTHORIZED: NO`; synthetic fixtures are contract-only;
clusters are candidate representations, never forecasts.

## Round-6 hardening (2026-09-16)

Coordinated hardening pass over the round-5 head (`content_head`
99ccec9; manifest commit `4e2ef91`). Code residuals closed in this
round's lane commits, each with behavioral tests:

| Gap | Closure |
|---|---|
| PROV-01 | Strict boolean gates — non-bool truthy/falsy gate values reject at emit, freeze, adapter, replay, and audit. |
| PROV-02 | Adapter parity with the producer gate universe; auditor findings now demote replay to `REPLAY_FAILED` instead of passing silently. |
| PROV-03 | Source evidence is byte-verified — sidecar/payload digests recomputed from real files under the bound evidence root. |
| PROV-04 | Verified producer binding required for `SUPPORTED` outcomes — an unbound or fabricated producer payload cannot carry support. |
| REG-02 | Calendar-valid dates enforced — impossible dates (e.g., June-31-class) reject. |
| REG-03 | Fit-surface intersection — the missingness-selected fit surface is intersected consistently across folds, bootstrap, strata, and nulls. |
| REG-04 | `first10` effort-split rejects non-numeric effort columns. |
| REG-05 | Null-replicate input digests bound per replicate; replay recomputes them. |
| ASSOC-03 | `input_digest` binding on the association report; revalidation runs for every status, not only terminal-success. |
| EVAL-03 | `forecast_regime_digest` binds the evaluated forecast output to its regime artifact. |
| EVAL-05 | Bound baselines and feature-row membership required — caller-supplied or unbound evidence cannot satisfy the declaration. |
| FCST-01 | Executable forecast-regime contract — the declared contract is enforced by an executed gate, not shape-only. |

Documentation/CI residuals closed in this round (docs lane):

- GOAL-01 — `MISSION_SCOPE_OVERLAY_V0.md` §2 now carries binding
  digests: frozen `preregistration.md` sha256 + P3-attested D1/D2
  digests, with an explicit additive-only statement.
- DOC-01 — `SOURCE_FEASIBILITY_RECORDS_V0.md` gains a dated
  (2026-09-16) supersession note: metadata-license tags resolved by
  `run_b/SOURCE_EVIDENCE_ADDENDUM_V0.md`; payload qualification and
  intake items remain gated.
- DOC-02 — stale live-state blocks refreshed to the round-5 rebind
  (HEAD `4e2ef91`, `content_head` 99ccec9, 71 manifest files,
  1875/5/0/57, 411 focused-lane green — historical, superseded by
  Round-7 census) in `P0_BASELINE_LEDGER.md`,
  `README.md`, and this register; the internal 56-vs-57 warnings
  inconsistency and the GOV-01/02 open-by-design wording reconciled.
- DOC-02b — `manifest_commit` field semantics reviewed; convention
  recommendation recorded for the integrator (the field equals the
  content commit this manifest revision was generated against; the
  carrying commit is recoverable from git history).
- GOV-02 — `GOVERNANCE_EXERCISE_RECORD_V0.md` added: bounded
  exercised-governance record (design-level, honestly bounded).
- PROV-DOC-01 — `run_a/RUN_A_EVIDENCE_SUMMARY_V0.md` §3 now binds the
  sha256 of `provenance_receipts.json` (verified against the run
  root's `rehash_report.json`).
- CI-01 — `.github/workflows/research-v0.yml` trigger paths extended
  (round-5/round-6/Run-A/framework-v1 test globs, hybrid fetcher,
  ledger repair, `requirements.txt`) and a step added running the
  previously-unrun manifest-governed test lanes.

The open-by-design table and `NO_QUALIFYING_PILOT_SOURCE` /
`WARNING_PATH_AUTHORIZED: NO` postures are unchanged.

## Round-7 closure — conformance hardening census (2026-09-17)

Verified at `content_head` cc2218b; HEAD `cab5897` is the manifest
rebind carrying this register (frozen baseline `5ef43c2`).
**Authoritative suite census: 2015 passed + 5 rasterio-disclosed
skips + 57 warnings = 2020 collected**; `ARTIFACT_MANIFEST_V0.json`
governs 77 files.  All earlier census figures in this register
(1282, 1300/1306/1311, 1641, 1687, 1875/5/0/57, and the 56-warning
count) are **(historical, superseded by Round-7 census)** — the rows
above are preserved verbatim as dated history, not edited.

Round-7 closed the following report-local gaps.  The `R7-` prefix is
a disambiguation prefix used only in this register — the report-local
IDs are C01–C17; they do not collide with the historical round-3
C01–C30 table above.  Behavioral tests live in
`tests/test_r7_hardening.py` unless noted.

| Gap | Closure evidence |
|---|---|
| R7-C02 | Strict fixture booleans — non-bool truthy/falsy `fixture` markers reject; `nepal/science_v0/regimes.py` + `TestC02StrictBoolFixture` (`tests/test_r7_hardening.py`) |
| R7-C03 | `unit_basin_map` bound into the producer payload and freeze-validated; `nepal/science_v0/regimes.py` + `TestC03UnitBasinMap` |
| R7-C04 | Semantic config revalidation at freeze via `_validate_config_semantics` (cadence, gap policy, block length, missingness, effort split, mode); `nepal/science_v0/regimes.py` + `TestC04ConfigSemantics` |
| R7-C06 | `family_digest` covers the complete null-family record fields; `nepal/science_v0/regimes.py` + `TestC06FamilyDigest` |
| R7-C07 | Per-event precision-class horizon admissibility — coarse-precision events cannot support fine lookback horizons; `nepal/experiment_v0/association.py` + `TestC07PrecisionHorizons` |
| R7-C08 | Mandatory report fields enforced for EVERY status, not only terminal success (`REQUIRED_NULLS` revalidation in `AssociationReport.problems`); `nepal/experiment_v0/association.py`; exercised by the `tests/test_r6_association.py` lane |
| R7-C11 | Explicitly empty `degradation_scenarios` rejected under a bound declaration (None = default grid only); `nepal/experiment_v0/evaluation.py` + `TestC11EmptyDegradation` |
| R7-C12 | `experiment_id` binds threshold, n_boot, seed, unit/region basin maps, baseline-evidence digests, and degradation specs; `nepal/experiment_v0/evaluation.py` + `TestC12ExperimentIdentity` |
| R7-C13 | Slice denominators derived exclusively from the bound opportunity registry (`missing_feed_degradation`); `nepal/experiment_v0/evaluation.py`; exercised by the `tests/test_r6_evaluation.py` lane |
| R7-C15 | Typed `RunManifestV0` with deterministic creation time; missing run manifest rejects at freeze; `nepal/science_v0/regimes.py` + `TestC15RunManifest` |
| R7-C16 | Finite-permutation +1 correction on spatial-shift p (`p = (ge+1)/(n+1)`); `nepal/experiment_v0/association.py` + `TestC16FinitePermutation` |

Round-7 residuals → Round-8 dispositions (closed by the Round-8
hardening content commit; census 2073 passed + 5 rasterio-disclosed
skips + 57 warnings, superseding the Round-7 census above as the
current authoritative figure — the Round-7 census rows are retained
verbatim as dated history):

| Report-local ID | Canonical register family | Round-8 disposition |
|---|---|---|
| R7-C01 shared producer validator | PROV family (producer payload validation) | RESOLVED — `nepal/research_v0/producer_validation.py` (`validate_producer_payload`) now runs at every producer boundary: `freeze_regime_artifact`, `regime_assignment_from_artifact`, `audit_producer_payload`, and the `run_association` producer-payload binding path; `tests/test_r8_provenance.py` (31 tests) |
| R7-C09 typed `fit_partition` | REG/FCST family (fit-surface binding) | RESOLVED — typed `fit_partition/v0` record bound into the payload, cross-checked against `fit_groups`/`heldout_groups_declared`/`n_train_rows`/`n_rows`/`feature_matrix_digest`/`feature_cols`, `fit_partition_digest` recomputed; `tests/test_r8_provenance.py` |
| R7-C10 forecast feature payload binding | FCST family (feature-matrix binding) | RESOLVED — `FORECAST_REGIME` payloads must carry a `forecast_feature_payload` consistent with mode/data-class/feature-matrix digest/row count/row-key digest; retrospective artifacts carrying forecast payloads reject; `tests/test_r8_provenance.py` |
| R7-C14 vintage `evidence_root` byte verification | E05/B15 family (vintage byte-binding) | RESOLVED — `ForecastVintageV0.evidence_root` + `verify_vintage_evidence()` (realpath containment, symlink/absolute/escape rejection, sha256 of real bytes); `build_vintage(require_bytes=True)`; `evaluate()` byte-verifies every claimed root and `FORECAST_EXPERIMENT_ONLY` now requires all admitted vintages byte-bound; `tests/test_r8_vintage.py` |
| R7-C17 rasterio-dependent skips | ENV-01 family (environment) | DOCUMENTED QUARANTINE — rasterio is intentionally outside the pinned environment by policy; the 5 skips are disclosed via `-rs`, not hidden; an optional pinned geospatial job is a future decision — rasterio is NOT added to CI or `requirements.txt` |
| NEW-FMX-01 label derivation | FMX family (feature-matrix labels) | RESOLVED — `fmx_audit` derives label columns from audited column metadata (`catalog_label` field class) even when `catalog_label_columns` is omitted; catalog labels in the predictor matrix or digest references reject; `tests/test_r8_fmx_assoc.py` |
| NEW-FMX-02 required FMX metadata | FMX family | RESOLVED — non-empty `unit`/`value_domain`/`missingness_policy` and a strict-UTC `temporal_window` (start ≤ end) are required; `tests/test_r8_fmx_assoc.py` |
| NEW-ASSOC-01 spatial-shift support accounting | ASSOC family (null family) | RESOLVED — shifted groups are checked against assignment coverage; eligible/censored support is recorded per offset, unsupported offsets are excluded from p denominators, and support below `MIN_USABLE_SPATIAL_SHIFTS` fails closed (no p-value) instead of counting as zero enrichment; finite-permutation `(ge+1)/(n+1)` retained; `tests/test_r8_fmx_assoc.py` |
| NEW-CI-01 round-N tests absent from CI | CI-01 family | RESOLVED (docs/CI lane) — workflow triggers now include `tests/test_r7_hardening.py`, `tests/test_r7_*.py`, `tests/test_r8_*.py`, and the hardening step runs the generalized `tests/test_r[678]_*.py` nullglob lane |

Crosswalk note: R7-C02/C03/C04/C06 extend the PROV/REG gate-universe
and config rows; R7-C07/C08/C16 extend the ASSOC null/report rows;
R7-C11/C12/C13 extend the EVAL denominator/identity rows; R7-C15
extends the PROV run-manifest row; R7-C17 maps to the ENV-01
environment quarantine row.  `NEW-*` IDs enter the register as new
rows under their own IDs above.

Still external/data-gated — never marked resolved:
S01–S04 source qualification, E01–E03 event package, M01 FMX on real
data, R01/R02 real regimes, A01 association, F01–F03 forecast,
G01/G02 human approvals, O01 operations.  Postures unchanged:
`DESIGN_DRAFT_COMPLETE`; P3 design-only; `NO_QUALIFYING_PILOT_SOURCE`;
`WARNING_PATH_AUTHORIZED: NO`.

## Round-9 promotion-closure census (2026-09-17)

An independent audit reopened thirteen items at the Round-8 head —
the `R9-` prefix is a report-local disambiguation prefix for the
reopened finding IDs; it does not collide with the earlier round
tables above.  **Every reopened Round-8 row below is marked
`REOPENED at Round-8 head → RESOLVED by Round-9`** — the Round-8 rows
themselves are preserved verbatim above as dated history, not edited.
Behavioral coverage lives in `tests/test_r9_promotion.py` — a
33-mutation matrix parameterized over `(mutation, boundary)` that
builds a complete NEW-floor producer payload, applies one mutation,
then *honestly rehashes every bound digest* (section digests,
`assignment_digest`, `regime_artifact_digest`, `freeze_digest` — the
B1 envelope rule) and asserts rejection at every applicable boundary:
`freeze_regime_artifact`, `regime_assignment_from_artifact`,
`audit_producer_payload`, and the `run_association`
`producer_payload` binding.

| Reopened row (Round-8 register family) | Status |
|---|---|
| R7-C01 / PROV shared producer validator (R9-P01, P04, P05, P07, P08, P09, P10, P12) | REOPENED at Round-8 head → RESOLVED by Round-9 |
| R7-C02 / PROV-01 strict fixture + gate booleans (R9-P01, P12) | REOPENED at Round-8 head → RESOLVED by Round-9 |
| Round-6 PROV-03 source byte-verification (R9-P02, P03) | REOPENED at Round-8 head → RESOLVED by Round-9 |
| R7-C09 typed fit_partition (R9-P05) | REOPENED at Round-8 head → RESOLVED by Round-9 |
| R7-C10 forecast feature payload (R9-P06) | REOPENED at Round-8 head → RESOLVED by Round-9 |
| R7-C15 typed RunManifestV0 (R9-P04) | REOPENED at Round-8 head → RESOLVED by Round-9 |
| R7-C04 semantic config revalidation (R9-P07) | REOPENED at Round-8 head → RESOLVED by Round-9 |
| R7-C03 / REG-01 unit→basin partition (R9-P10, P11) | REOPENED at Round-8 head → RESOLVED by Round-9 |
| ASSOC producer-payload binding / PROV-04 (R9-P11) | REOPENED at Round-8 head → RESOLVED by Round-9 |
| REG-05/FMX feature-matrix + null-family digests (R9-P08, P12) | REOPENED at Round-8 head → RESOLVED by Round-9 |
| DOC family — register/ledger/CI provenance (R9-D01) | REOPENED at Round-8 head → RESOLVED by Round-9 |

Closure evidence — validator functions, bound artifact fields, and
the test file carrying each:

| Finding | Closure evidence |
|---|---|
| R9-P01 truthy fixture flags | `producer_validation.fixture_flag` — strict `isinstance(..., bool)`; `{"fixture": "true"/1/"yes"}` is SCHEMA_MALFORMED, never a synthetic bypass; tested at all four boundaries (`fixture-*` cases). |
| R9-P02 fabricated source evidence | `_source_manifest_problems(..., verify_source_bytes=True)` → `_hashing.verify_source_evidence` runs at every boundary (freeze, adapter, audit, association binding); nonexistent roots, absent `source_files`, missing files, and digest-mismatched real bytes all reject (`nonexistent-root`, `missing-source_files`, `files-absent-on-disk`, `digest-mismatch-real-files`). |
| R9-P03 inside-root symlink | `verify_source_evidence` calls `is_symlink()` on the declared path BEFORE resolution — an inside-root symlink whose declared sha256 is *correct for the target's bytes* still rejects (`inside-root-symlink`). |
| R9-P04 run-manifest forgery | `_run_manifest_problems` — `record_type == "RunManifestV0"` enforced, `deserialize_record` exact field set (extra/missing fields reject), `canonical_json(rm) == canonical_json(rec.to_dict())`, `input_digests`/`output_digests` bind `input_bytes_digest`/`assignment_digest`, `environment_digest` and `seed == seeds_declared[0]` (7 mutation cases). |
| R9-P05 fit-partition train rows | `_row_universe_problems` — `fit_partition.train_row_keys_digest` recomputes over the assignment rows whose `unit_basin_map` group is in `train_groups`; honest rehash of `fit_partition_digest` + envelope does not rescue a forged digest (`train_row_keys-digest-forged`). |
| R9-P06 forecast payload rows | `_row_universe_problems` — `forecast_feature_payload.row_keys_digest` must equal the assignment row-universe digest and `row_count` the assignment row count (`ffp-row_keys-forged`, `ffp-row_count-off-by-one` on a FORECAST_REGIME payload). |
| R9-P07 config cross-binding | `_config_cross_binding_problems` — `config.mode`/`config.seeds`/`config.source_manifest`/`heldout_groups`/`train_groups`/`k_candidates`/`missingness_policy`/`data_class` must agree with the artifact's flat declared fields (`config-mode-flip`, `config-seeds-changed`, `config-source_manifest-differs`). |
| R9-P08 semantic feature matrix | `_input_values_problems` — `feature_matrix_digest` (artifact, `fit_partition`, forecast payload) recomputes over `input_values` under the 6-decimal `semantic_feature_matrix_digest` normalization; a consistently-forged digest still rejects (`feature_matrix_digest-forged`). |
| R9-P09 degenerate covariances | `_model_problems` — symmetric + finite + non-positive-diagonal + strict positive-definiteness (`eigvalsh` min > 1e-10) in the SHARED floor, not only the auditor (`negative-diagonal`, `zero-covariance`, `rank-deficient`). |
| R9-P10 unit→basin integrity | `_unit_basin_map_problems` — non-empty string pairs, unique units covering EXACTLY the assignment sidecar's units, groups ⊆ fit ∪ heldout, recomputed `unit_basin_map_digest` over `canonical_unit_basin_pairs` (4 cases incl. `unit-none`, coverage, duplication, stale digest). |
| R9-P11 association map binding | `RegimeAssignmentArtifact` carries `unit_basin_map` from the payload; `run_association` requires the caller's `unit_basins` to equal the artifact's bound map and the producer payload's map to equal the artifact's (`TestP11UnitBasinAssociationBinding`). |
| R9-P12 seed/gate/null/status | `_seed_stability_problems` (coverage keys = `seeds_declared`, declared-state vocabulary, converged-under-descriptive), `_gate_problems` (exact `REQUIRED_REGIME_GATE_NAMES` universe, boolean-only, status-consistent, `stability_report_digest` recompute), `_null_problems` (declared `shuffled`/`season_matched` families, `family_digest`/`null_model_digest` recompute) — 5 cases. |
| R9-D01 docs/CI surface | This census + round-9 snapshot blocks in `P0_BASELINE_LEDGER.md`/`README.md`; `tests/test_r9_*.py` added to push/pull-request triggers and the `test_r[6789]_*.py` nullglob lane in `.github/workflows/research-v0.yml`. |

### Round-9 independent adversary pass (V-residuals)

After the 33-case matrix landed, an independent read-only adversary
re-traced every boundary and reported twelve matrix-missed residuals
(`R9-V1..V12`). All are **closed** in the same round; regression
coverage lives in
`tests/test_r9_promotion.py::TestR9AdversarialResiduals` (30 tests):

| Residual | Closure |
|---|---|
| V1 — `run_association` verified-binding over payloads the adapter would reject | `_producer_payload_binding_problems` now enforces adapter admissibility on the bound payload: `status == DESCRIPTIVE_REGIME_ONLY`, `associable is True`, `data_class == "REANALYSIS"` — a `dataclasses.replace`'d artifact plus a self-consistent `CANDIDATE_ONLY`/`UNSTABLE`/non-associable payload cannot earn the `verified_producer_payload` claim. |
| V2 — `fit_groups ∩ heldout_groups_declared` straddle | Disjointness enforced in `_unit_basin_map_problems` (flat fields) and `_fit_partition_problems` (typed record). |
| V3 — duplicate `(unit, date)` assignment rows | `_row_universe_problems` requires one regime per unit-day. |
| V4 — `input_bytes_digest` self-consistent only | `_input_values_problems` decodes `input_values` to the float64 byte domain and recomputes the contiguous-byte sha256 at every boundary. |
| V5 — flat forecast fields under `RETROSPECTIVE_REGIME` | `_forecast_payload_problems` bans non-empty `forecast_vintage_digests`/`forecast_feature_set` under retrospective mode. |
| V6 — presence-only type floors | `_scalar_floor_problems`: positive non-bool-int `k`, non-empty int `seeds`, distinct int `seeds_declared`, non-negative numeric `occupancy`, non-empty unique string `feature_cols`, `fitted_on == "TRAIN_ONLY"`, boolean `associable`/`terminal`, declared `status` vocabulary, numeric `modal_k_frequency`. |
| V7 — assignment dates/units/labels unbounded | `_row_universe_problems` requires calendar-real ISO dates, non-empty unit ids, `regime_id ∈ [0, k)`. |
| V8 — `environment_digest`/`run_manifest_digest` not required | Both added to `PRODUCER_REQUIRED_FIELDS`. |
| V9 — model width unbound to `feature_cols` | `_model_problems` requires the fitted dimension to equal `len(feature_cols)`. |
| V10 — `missingness_applied` unvalidated | `_missingness_problems`: non-empty policy, non-negative int counts, `fitted + dropped == total`, `fitted == n_train_rows`. |
| V11 — secondary binding surfaces | `per_seed_best_k` values ⊆ `config.k_candidates`; `fit_partition`/`forecast_feature_payload` reject undeclared fields; `fit_partition.cutoff_iso` calendar-valid; `forecast_feature_set ⊆ feature_cols`; `forecast_vintage_digests` sha256-shaped; null `n_succeeded + n_failed ≤ n_replicates` (a skipped family legitimately reports zero executed — the bound is `≤`, not `=`). |
| V12 — unhashable `input_values` element raised `TypeError` | `_input_values_problems` type-guards the nonfinite-token membership test — malformed elements emit `SCHEMA_MALFORMED`, never an uncaught exception. |

The mutation matrix provides failure evidence (the rejection string
must name the finding's category — a stale-envelope rejection alone
is not counted) and a no-false-negative note per case; the unmutated
canonical payload is verified to pass freeze, adapter, audit, and the
association producer-payload binding first (positive controls), so a
mutation that still passes is reported as an unfixed finding, not
absorbed.

Still external/data-gated — never marked resolved, unchanged by this
census: **S01–S04** source qualification, **E01–E03** event package,
**M01** FMX on real data, **R01/R02** real regimes, **A01**
association, **F01–F03** forecast, **G01/G02** human approvals,
**O01** operations.  Postures unchanged:
`DESIGN_DRAFT_COMPLETE`; P3 design-only; `NO_QUALIFYING_PILOT_SOURCE`;
`WARNING_PATH_AUTHORIZED: NO`.

## Round-10 promotion-closure census (2026-09-17)

A Round-10 independent audit found the Round-9 floor green at its
tested layer but reopened its "complete shared floor" claim: the
crash-class surface (P01), serialized-config completeness (P02),
status state machine (P09), unknown-field tolerance (P11), typed
forecast-vintage binding (P12), and several boundary/test-surface
gaps were live. All sixteen findings are **closed in this round**;
the affected Round-9 rows are re-annotated below as
`RESOLVED by Round-9 → PARTIAL at Round-10 audit → RESOLVED by
Round-10`. Regression coverage lives in
`tests/test_r10_promotion.py` (520 tests: 89 payload mutations ×
floor/freeze/adapter/audit/association with forged-artifact
association evidence — no canonical-artifact fallback — plus
direct-construction, vintage-acceptance, run-level, and accounting
classes). Full suite at closure: **2757 passed, 5 skipped
(rasterio quarantine), 0 failed, 57 warnings**.

| Finding | Closure |
|---|---|
| R10-P01 numeric overflow crashes | `_finite_float` — one bounded numeric parser catching TypeError/ValueError/OverflowError, rejecting bools and non-finite values — applied at every numeric consumption site (input_values, weights, means, covariances, occupancy, modal_k_frequency, null stats/alpha/p_value, replicate stats, missingness, stability scores). No numeric probe raises through any boundary; audit's own richer model checks were wrapped identically. |
| R10-P02 incomplete config semantics | `_config_semantic_problems` validates every serialized `RegimeRunConfig` field: cadence/gap_policy/fold_seed_policy/effort_split vocabularies, `bootstrap_block_len`/`n_bootstrap`/`n_null_replicates` floors, `null_alpha`/`max_missingness`/`elev_ablation_ari_max`/`era_drift_max` ranges, `label_blinding is True`, `fitted_on == "TRAIN_ONLY"`, distinct K candidates ⊆ {1..5} containing 1, non-empty disjoint train/heldout groups (run parity — an empty holdout can never be emitted), column-name fields as non-empty strings (frame membership remains run-preflight — the payload carries no frame; covered by `TestRunLevelColumnMembership`), forecast field presence/absence by mode. |
| R10-P03 duplicate ordered values | Duplicates reject in `k_candidates`, `train_groups`, `heldout_groups`, `forecast_feature_set`, `forecast_vintage_digests`, `seeds`/`seeds_declared`/`fit_groups`/`feature_cols`/`per_seed_best_k`/`seed_coverage` keys — canonical order preserved where semantic. |
| R10-P04 malformed unit/group values | `run_regimes` preflight rejects missing/blank/non-string values in unit/group/season (and declared era) carrier columns BEFORE any `astype(str)` — a frame producing `"None"`/`"nan"`/empty identifiers returns RUN_ERROR and never reaches emission. |
| R10-P05 weak digest/count types | `_preprocessing_problems`: exact field set; `row_keys_digest` 64-hex AND recomputed over the assignment row universe; `train_mask_membership_digest` 64-hex; `feature_order == feature_cols` ordered; scaler/imputer lists finite with exact lengths. `forecast_feature_payload.row_count` strict positive int equal to the assignment count. |
| R10-P06 direct artifact coercion | `RegimeAssignmentArtifact.__post_init__` no longer `str()`-coerces map entries — non-string/blank values flag `_unit_basin_map_malformed` and are excluded; `problems()` additionally requires the map's unit set to equal the assignment universe exactly (missing/extra both reject). `_strict_artifact_payload_problems` applies the same non-coercion at deserialization. `local_artifact_unverified` remains descriptive-only (controlled residual — never SUPPORTED). |
| R10-P07 source-manifest schema | Exact schema for non-fixture manifests: `source_id`/`lineage`/`evidence_root` non-empty strings, `units`/`feature_allowlist` non-empty sequences of non-empty strings, `source_digests` 64-hex sequence, `source_files` `{relpath, sha256}` records with no extra fields, duplicate relpaths rejected; run-level parity check added in `run_regimes` preflight. |
| R10-P08 symlink/TOCTOU policy | `_hashing` walks every path component under the evidence root — any symlink at any depth (including a symlinked root or intermediate directory) rejects; leaf pinned by pre/post `lstat` identity (documented: fd-level O_NOFOLLOW is isolation-forbidden; component-walk + identity-pin is the enforceable equivalent). Applies to source evidence and vintage evidence identically. |
| R10-P09 status state machine | Exact machine in the shared floor: `DESCRIPTIVE_REGIME_ONLY → terminal∧associable`; `UNSUPERVISED_STRUCTURE_NOT_STABLE → terminal∧¬associable`; `CANDIDATE_ONLY → ¬terminal∧¬associable`; `RUN_ERROR` and undeclared statuses reject — enforced identically at all four boundaries. |
| R10-P10 null-family validation | `nulls` section and each family record carry exact field sets; family `status` restricted to the emitted vocabulary; `p_value`/`alpha` in [0,1]; `statistic == "silhouette"`; `null_stat_min ≤ max`; `null_k_distribution` keys ⊆ `k_candidates` with non-negative int counts ≤ `n_replicates`; `reason` required non-empty iff status != PASS; `replicates` (optional) validated per-record (index range/uniqueness, `fit_seed ⊆ seeds_declared`, `k ⊆ k_candidates`, `ok` bool, `stat` finite-or-null, 64-hex-or-null `input_digest`, ok-count == n_succeeded); family digests recompute over the full record. |
| R10-P11 unknown-field tolerance | `PRODUCER_ALLOWED_FIELDS` — the exact emitted envelope plus declared optionals (`frozen`, `freeze_digest`, `forecast_feature_payload`, `forecast_vintages`, `ambiguous_fraction`, `mean_max_posterior`, `missingness`) — any undeclared top-level field rejects; exact field sets also enforced for config/model/input_schema/preprocessing/stability/fit_partition/nulls+families/missingness_applied/source_manifest/forecast_feature_payload/vintage records. |
| R10-P12 typed vintage binding | `forecast_vintages` — optional artifact-level section of serialized `ForecastVintageV0` records. `FORECAST_REGIME + associable` requires non-empty records that deserialize exactly (record_type tag, exact fields, `problems()==[]`), cover the declared digests bijectively (`sha256_canonical(rec.to_dict())`), and carry non-empty `evidence_root` passing `verify_vintage_evidence` byte checks — no metadata-only vintage qualifies a forecast-ready artifact. Non-associable forecast artifacts may carry metadata-only candidates; `RETROSPECTIVE_REGIME` forbids the section. `RegimeRunConfig.forecast_vintages` emits the records (artifact-level, never in serialized config). |
| R10-T01 weak association evidence | Every R10 mutation binds a forged artifact (`dataclasses.replace` with the mutation's honest digests + map) — no canonical-artifact fallback; association rejection must name the finding's category. |
| R10-T02 speculative fallbacks | Import fallbacks and "lanes not landed" wording removed from `tests/test_r9_promotion.py`; the shared helpers are mandatory. |
| R10-CI-01 test census | Root `conftest.py` pins `collect_ignore = ["data"]` — repository-wide collection equals `tests/` exactly (2762 nodes at the R10 head; 2876 at the R11 thin-PoC head, zero from the ignored external symlink). CI adds a canonical-collection guard step (tests/ == repo-wide counts) and a `reconciled-geospatial-optional` job (`continue-on-error`, rasterio stays quarantined, no data/ writes). |
| R10-D01 doc accuracy | This addendum; R9 rows below re-annotated; ledger/README counts refreshed at the final head. |

### Round-9 rows re-annotated

| R9 row | Re-annotation |
|---|---|
| R9-P01 fixture flags, R9-P02 source evidence, R9-P04 run manifest, R9-P05/P06 row universes, R9-P08 semantic matrix, R9-P09 PSD | RESOLVED by Round-9 — upheld at Round-10 (no reopen). |
| R9-P03 symlink | RESOLVED by Round-9 → **PARTIAL** at Round-10 (leaf-only check missed intermediate components) → RESOLVED by Round-10 (component-walk + identity pin). |
| R9-P07 config cross-binding | RESOLVED by Round-9 → **PARTIAL** (cross-binding yes, per-field semantics no) → RESOLVED by Round-10 (`_config_semantic_problems`). |
| R9-P10/P11 unit→basin + association map | RESOLVED by Round-9 → **PARTIAL** (coercion at direct construction, no coverage check, malformed producer-side values) → RESOLVED by Round-10 (non-coercion + exact coverage + run preflight). |
| R9-P12 seed/gate/null/status | RESOLVED by Round-9 → **PARTIAL** (state machine and null-family semantics incomplete) → RESOLVED by Round-10. |
| R9-V6 scalar floors | RESOLVED by Round-9 → **PARTIAL** (crash-class — coercions raised instead of rejecting) → RESOLVED by Round-10 (`_finite_float` everywhere incl. audit). |
| R9-V11 secondary surfaces | RESOLVED by Round-9 → **PARTIAL** (envelope/config/preprocessing unknown fields tolerated; `cutoff_iso` unbound) → RESOLVED by Round-10 (exact schemas + `cutoff_iso` recomputed as max train date). |
| R9-V12 unhashable input guard | RESOLVED by Round-9 — upheld (extended to the full numeric surface by R10-P01). |

### Round-10 coordinator adversarial pass (post-merge, executable)

~60 executable probes beyond the matrix — crash-class (nan/inf/10**400/dicts/generators/sets in every numeric surface), exactness (extra+missing fields in all 11 sections, wrong-type same-name), consistency (config↔payload divergence in mode/seeds/k_candidates, seed_coverage/per_seed/run_manifest key drift, feature_cols disagreements, n_train_rows accounting, regime_id≥k, map coverage, row drops), temporal (impossible dates, vintage ordering), IO-class (symlinked root/intermediate, traversal, directory-as-file), fixture-class (marker+field combos, divergent config↔payload manifests). One residual was found and fixed in-round: `fit_partition.cutoff_iso` was bound only as a calendar-valid string — the producer emits `max(train-row date)`, so the floor now recomputes it (`1999`/`2099` forged cutoffs reject). No other probe escaped structured rejection at any boundary.

Still external/data-gated — never marked resolved, unchanged by this
census: **S01–S04** source qualification, **E01–E03** event package,
**M01** FMX on real data, **R01/R02** real regimes, **A01**
association, **F01–F03** forecast, **G01/G02** human approvals,
**O01** operations.  Postures unchanged:
`DESIGN_DRAFT_COMPLETE`; P3 design-only; `NO_QUALIFYING_PILOT_SOURCE`;
`WARNING_PATH_AUTHORIZED: NO`.

## Round-10.1 release repair + residual backlog (2026-09-18)

A post-R10 independent verification reproduced one release regression
(stale `cutoff_iso` fixture) and twelve residual malformed-value
findings.  Per the contract-freeze directive, the repair is minimal:
the stale fixture, the documented test count, and the two cheap
critical/high residuals (A crash-class, B fixture-smuggling) are
closed; the rest are registered as a deferred maintenance backlog
with explicit stage gates — NOT silently dropped.

| Item | Disposition |
|---|---|
| R10-P0 stale cutoff fixture | RESOLVED — `test_r6_regimes` fixture `cutoff_iso` set to the recomputed max train date `2020-01-02`; the recomputation binding stays strict. |
| R10-D0 doc count + overclaim | RESOLVED — 503→505→520 count corrected in register/ledger/README; "no malformed value crashes" restated to the exact residual threat model (JSON-shape preflight + str-guarded vocab lookups + exception boundary). |
| **R10.1-A** unhashable values crash (Critical) | RESOLVED — `_reject_nonjson` preflight at the floor head covers non-JSON-native values (sets/generators/nan); str-type guards added before every frozenset vocab lookup (`status`, `config.mode`, `missingness_policy`, `fold_seed_policy`, `seed_coverage` values, null `status`); `validate_producer_payload` is now an exception-safe wrapper — an unenumerated TypeError/ValueError/OverflowError becomes one named SCHEMA problem. `[]`/`{}`/dict-valued scalars reject at all five boundaries (15 focused regressions in `TestR10Point1Residuals`). |
| **R10.1-B** fixture-manifest extras (High) | RESOLVED — a fixture manifest is exactly `{"fixture": true}`; any extra field (evil/source_files/evidence_root/source_id/lineage) is a SCHEMA problem even after honest rehash. |
| **R10.1-C** era duplicate/reversed/mixed | DEFERRED — gate: era-drift ablation at real-fit stage. Producer preflight already rejects some forms; floor accepts duplicate/reversed era strings today. Action: extract one era-boundary helper shared by run + floor. |
| **R10.1-D** `RunManifestV0.status="PLANNED"` / future `created_at` | DEFERRED — gate: terminal promotion on real artifacts. Action: bind manifest status to the artifact's terminal state + order `created_at` vs input dates. |
| **R10.1-E** run-preflight unguarded float/set/int ops | DEFERRED — gate: multi-group real fit. Action: one bounded preflight wrapper; `RUN_ERROR` instead of raw exceptions. |
| **R10.1-F** `feature_allowlist`/`units` not bound to emitted surface | DEFERRED — gate: real-source intake. Action: allowlist ⊆ feature_cols, units ⊆ assignment universe at the floor. |
| **R10.1-G** extra configured train group | VERIFIED-CLOSED — the `fit_groups == config.train_groups` cross-binding already rejects an extra configured group; `fit_partition.train_groups` tampering rejects via row-digest. Codex's subset-only observation was on the run-level preflight (deferred to R10.1-E's stage). |
| **R10.1-H** non-string dtype values | VERIFIED-CLOSED — rejects at the floor via the dtypes-keys check. |
| **R10.1-I** association `n_boot`/`seed` coercion + duplicate family declarations | DEFERRED — gate: association PoC (blocked until real labels). Action: bounded-int validation + reject duplicates before canonicalization. |
| **R10.1-J** component-walk residual race | DEFERRED — documented threat model; fd-level `O_NOFOLLOW` unavailable under the isolation policy. Gate: decide before real byte intake whether an approved lower-level helper is required. |
| **R10.1-K** `conftest.py` broad `data/**` ignore | DEFERRED — informational geospatial job remains `continue-on-error`; replace with an explicit canonical-collection allowlist or accept the quarantine status. |
| **R10.1-L** matrix coverage of malformed families | RESOLVED for the reproduced families (15 regressions); further families live in the deferred items above. |

Postures unchanged: `DESIGN_DRAFT_COMPLETE`; P3 design-only;
`NO_QUALIFYING_PILOT_SOURCE`; `WARNING_PATH_AUTHORIZED: NO`.
The Round-10-PoC lane is the next workstream — source card for
HMAGLOFDB v1.3.0 prepared (CANDIDATE_WITH_GAPS: no native
opportunity frame, heterogeneous timing); P5 authorization is the
next human gate.

## Round-11 thin-PoC acceleration (2026-09-18)

The Round-11 audit froze the contract and directed a thin
byte-bound descriptive slice rather than further hardening.  New
surface (all audit-pinned interfaces):

- `nepal/research_v0/source_intake.py` —
  `build_source_manifest` (existing non-fixture manifest shape;
  `source_version=…;` bound in lineage) and
  `load_hmaglofdb_rows` (explicit column map; byte-bound;
  fail-closed on missing columns, dup keys, invalid intervals,
  unknown basin/mechanism/precision).
- `nepal/science_v0/glof_poc.py` —
  `build_hmaglofdb_event_package` (typed labels/opportunities/
  controls/holdout + canonical digests; rejected holdout demotes,
  never weakens) and `run_glof_descriptive_poc` (non-promotable
  `GLOF_POC_RECEIPT_V0`: RUN_ERROR → CANDIDATE_ONLY →
  UNDERPOWERED_DESCRIPTIVE_ONLY → DESCRIPTIVE_REGIME_ONLY).
- `docs/science/run_b/GLOF_POC_CONTRACT_V0.md` — the frozen
  contract incl. the P5 authorization + opportunity-frame policy
  owner gates.
- `tests/test_glof_poc_contract.py` — contract regressions.

Synthetic path verified end-to-end (temporary-byte fixture →
manifest → loader → package → receipt; 16s to a frozen artifact).
Postures unchanged — no source bytes, no P5, no association, no
forecast, no operational path.

## Round-11.1 provenance repair (2026-09-18)

The live R11.1 audit's serial repair — one content commit then one
manifest-only rebind.  Codeable findings closed:

- **R11.1-1 (P0)** loader read bytes before the shared verifier —
  now `verify_source_evidence` runs first, then the parser consumes
  identity-pinned bytes from the new shared
  `_hashing.read_evidence_file` helper (component walk + inode/mtime
  pin); the caller's own path may not alias through a symlink
  (lexical component walk on the intake path).
- **R11.1-2 (P1)** malformed `column_map` values and duplicate
  mapped/CSV headers are bounded `ValueError`s, never `TypeError`
  or silent `DictReader` collapse.
- **R11.1-3 (P0)** `source_manifest_digest` now digests the
  `source_manifest` (required builder keyword), not the record.
- **R11.1-4 (P0)** the runner recomputes all carried section
  digests and re-deserializes every record before fitting —
  stale/tampered sections are `RUN_ERROR`.
- **R11.1-5 (P1)** row↔record↔manifest ID/version/units
  cross-binding plus config↔package manifest binding.
- **R11.1-6 (P1)** malformed package inputs are bounded
  `ValueError`s (no `AttributeError`).
- **R11.1-8 (P1)** `tests/test_glof_poc_contract.py` added to CI
  push/PR triggers plus a dedicated step.
- **R11.1-9/10** content-head/`manifest_commit` repaired via the
  one-content-commit + one-manifest-rebind convention; full suite
  rerun bound at this head.

Owner-gated (unchanged): P5 authorization, opportunity-frame
Option A/B, independent review, operational approvals — posture
stays `NO_QUALIFYING_PILOT_SOURCE`.  Deferred per the audit's own
directive: R10.1-C/D/E/I/J/K/L stage gates.

## Round-11.2 provenance micro-round (2026-09-18)

The live R11.2 audit's serial repair — findings 1-7 codeable, the
rest owner-gated or deferred.  Closed:

- **R11.2-1 (P0)** the event package now requires the exact
  seven-key non-fixture manifest, byte-verifies it via
  `verify_source_evidence`, and the runner requires a present,
  digest-equal `regime_config.source_manifest` — a rehashed or
  nonexistent manifest can never reach the engine.
- **R11.2-2 (P0)** `EVIDENCE_VERIFIED` posture now verifies real
  sidecar bytes through the shared pinned reader —
  `gates.source_evidence_problems` walks lexical components, so
  leaf AND intermediate sidecar symlinks reject, and the digest
  and parsed JSON are the same bytes.
- **R11.2-3 (P0)** source posture+sidecar gating moved before
  `run_regimes` — unverified sources return `CANDIDATE_ONLY`
  without ever invoking the engine (spy-tested).
- **R11.2-4 (P1)** malformed `source_files` shapes are bounded
  `ValueError`s; the intake path must lie LEXICALLY inside the
  evidence root — an outside-root symlink alias resolving inside
  is rejected outright.
- **R11.2-5 (P1)** opportunities cross-bind `source_id`, declared
  units, unique `opportunity_id`, unique (unit,window) identity,
  and non-shared `frame_ids`.
- **R11.2-6 (P1)** malformed engine/freeze outputs are
  `RUN_ERROR`; holdout rejection `problems` is shape-bounded.
- **R11.2-7 (P2)** opportunities canonically sort by
  `opportunity_id` — digests are frame-permutation invariant.

Owner-gated (unchanged): P5 authorization, opportunity-frame
Option A/B, independent review, operational approvals.  Deferred
per the audit's directive: R10.1-C/D/E/F/I/J stage gates, rasterio
quarantine, `data/**` collection quarantine.

## Round-11.3 provenance micro-round (2026-09-18)

The live R11.3 audit's serial repair — findings P01-P04 + D01 +
SRC-01 codeable/docable; the rest owner-gated.  Closed:

- **R11.3-P01 (P0)** the runner now independently re-verifies the
  config manifest — exact seven-key non-fixture shape AND
  `verify_source_evidence` — so a forged package carrying a
  self-consistent forged digest can never reach the engine
  (spy-tested: zero `run_regimes` calls).
- **R11.3-P02 (P1)** section digest recomputation is inside a
  bounded boundary — sets, generators, NaN, bytes, and malformed
  nested values are `RUN_ERROR`, never uncaught `TypeError`.
- **R11.3-P03 (P1)** package construction requires exact typed
  records (`SourceRecordV0`, `ObservationOpportunityV0` or their
  serialized mappings deserializing to those classes) — duck-typed
  stand-ins reject.
- **R11.3-P04 (P1)** sidecar `reviewer_ids` must be a unique
  non-empty string sequence naming >=2 independent reviewers —
  single-lane review cannot carry `EVIDENCE_VERIFIED`.
- **R11.3-D01** verification commands standardized on `tests/` +
  `.venv`; no `backend/tests` reference exists in governed files.
- **SRC-01** source-card claims reconciled with the independent
  GLM3 review: lake joins are `GL_ID`/`LakeDB_ID`/`G_ID` payload
  columns (not `GF_ID`); timing percentages are paper/v1.0-era —
  v1.3 distribution marked `PAYLOAD-GATED`; `_Z` suffix semantics
  marked UNVERIFIED for v1.3.

Owner-gated (unchanged): P5 authorization, opportunity-frame
Option A/B, independent reviews, real bytes, governance.

## Round-11.4 documentation/source-matrix repair (2026-09-18)

The live R11.4 audit found the R11.3 code findings closed but
external-claim drift across the governed documents.  Serial
reconciliation only — no code, no bytes, no posture change:

- **#2 ds084001** — span corrected to `2015-01-15 → 2026-10-02`
  per the RDA record with the early-2026 freeze/AWS-migration
  caveat; tail availability marked PAYLOAD-GATED in all five
  documents that carried the stale `2025-05-28` end.
- **#3 NODD** — NODD rolling buckets (incl. post-2020 material)
  classified `CURRENT_FEED` and explicitly excluded from
  `ARCHIVED_OPERATIONAL` evidence; GEFSv12 reforecast row notes
  that only the fixed product qualifies.
- **#4 timing** — published v1.0 statistics recorded as
  39% day / 47% month / 26% year-uncertain across all cards;
  v1.3 distribution remains PAYLOAD-GATED.
- **#5 semantics** — day = last-day-or-peak-flood for multi-day
  events; `GF_ID` = integer event key; `Repeat` = recurrence
  field; `_Z` suffix convention UNVERIFIED until payload
  inspection.
- **#6 licence** — all four surfaces recorded (RDS CC BY 4.0,
  Zenodo CC0 tag, GitHub LICENSE, GitHub README — the latter two
  metadata-only; fetched 2026-09-18 by the GLM3 metadata lane, see
  R11.4.1 below); conservative CC BY 4.0 governing
  read retained without implying access authorization.
- **#7 payload identity** — Zenodo 107,879-byte payload + published
  MD5 recorded as metadata only; independent SHA-256 required
  after acquisition.
- **#9 target date** — the 2026-08-26 ice-rock avalanche recorded
  as a separate vertical: not a HMAGLOFDB GLOF label and outside
  the frozen GLOF source span.

Owner-gated (unchanged): P5 authorization, opportunity-frame
Option A/B, real bytes, independent intake review, governance.

## Round-11.4.1 residual documentation repair (2026-09-18)

The GLM3 independent re-verification against the manifest-bound
content head (`3c38443`) closed the stale-baseline finding but
found four R11.4.1 residuals. GLM-5.3 (documentation-only lane)
applied the repairs — no code, no bytes, no posture change:

- **R11.4.1-1 GitHub provenance** — `SOURCE_EVIDENCE_ADDENDUM_V0.md`
  recorded GitHub LICENSE/README as "not re-fetched" although the
  GLM3 lane fetched them 2026-09-18; now recorded as LICENSE
  `CC0-1.0` / README `CC BY 4.0` with retrieval date and lane,
  metadata-only/no-access wording retained.
- **R11.4.1-2 TIGGE vocabulary** — `tigge_ecds_cma` carried
  `decision: BLOCKED` and sat in the consolidated `BLOCKED` row
  while other documents said `CANDIDATE_ONLY`; normalized to
  `CANDIDATE` with the registration/per-provider-licence blocker
  preserved — `BLOCKED` is reserved for intrinsic disqualifiers
  and procurement barriers.
- **R11.4.1-3 `_Z`/join-key spec** — `EVENT_PACKAGE_SPEC_V0.md`
  still prescribed unconditional `_Z`-suffix grouping and a
  `GF_ID` lake-inventory join; now integer `GF_ID` + `Repeat`
  govern recurrence, `_Z` is UNVERIFIED/PAYLOAD-GATED with no
  deduplication on suffix alone, and lake joins go through
  `GL_ID`/`LakeDB_ID`/`G_ID`.
- **R11.4.1-4 NCEI precision** — official GEFS archive end
  recorded as `2020-09-23` with the NODD `CURRENT_FEED`
  distinction preserved in the records, feasibility table, and
  archive matrix.

Verification: stale-claim greps clean (`not re-fetched`,
unconditional `_Z` grouping, `GF_ID` lake joins, TIGGE
`BLOCKED`), `git diff --check` clean, protected-path diff empty,
manifest verified post-rebind.

Owner-gated (unchanged): P5 authorization, opportunity-frame
Option A/B, real bytes, independent intake review, governance.

## Round-11.5 real-path test lanes (2026-09-18)

SWE2 content commit `a0bc8ff` added four focused lanes exercising
the real (non-mocked) boundaries with synthetic bytes, all
P5-blocked by construction:

- `tests/test_p5_glof_intake.py` — byte-bound source intake contract.
- `tests/test_real_fmx_audit.py` — real `audit_matrix` leak/leakage
  checks (exposure column named to the governed vocabulary so
  NAME-EXPOSURE and CLASS-EXPOSURE both fire).
- `tests/test_glof_poc_real_path.py` — end-to-end runner on real
  bytes; fixture-manifest runner path asserts `RUN_ERROR` plus zero
  engine calls (R11.3-P01 fail-closed semantics).
- `tests/test_regime_real_path.py` — real `run_regimes`/freeze/
  producer audit against the FROZEN artifact.

CI path triggers and the contract step include all four files.
No production code changed; no bytes acquired; no posture change.

GLM3 verifier/rebinder results at this head:

- Collection: **3061** (was 2967; +94 = the four lanes).
- Focused lanes: **93 passed / 1 skipped** — the skip is
  documented (`cached artifact is 'CANDIDATE_ONLY'`; the
  gate/status consistency rejection is exercised under a
  descriptive artifact).
- Full canonical suite (`tests/`, `.venv`, `-B`):
  **3055 passed / 6 skipped / 0 failed / 57 warnings** in 1518 s —
  5 disclosed rasterio quarantine skips + 1 lane skip.
- Manifest rebound: four test files added (90 → 94 files);
  `content_head` bound to the verification-record commit;
  claim-scan clean; protected-path diff empty.

Owner-gated (unchanged): P5 authorization, opportunity-frame
Option A/B, real bytes, independent intake review, governance.

## Round-11.5 release closure (2026-09-18)

The R11.5 audit found the manifest's `manifest_commit` stale at
`bb22ca2` while `content_head` was `5d34210` — the convention
requires both fields to name the final content commit. Closed
serially (SWE2 content commit `285e933`; GLM3 manifest-only
rebind `8c788a8`):

- **#1 manifest identity** — `manifest_commit == content_head`
  now names the final content commit; enforced by a CI
  identity-guard step and `tests/test_r11_5_release_closure.py`
  (3 tests: identity, lane governance, collection census).
- **#2 canonical test root** — `tests/` everywhere; zero
  `backend/tests/` references in governed files.
- **Residual (queued)** — `verify-manifest` (stdlib verifier)
  does not itself check the invariant; enforcement is pytest +
  CI. Queued hardening, not a blocker.
- **Rebind results** — manifest 95 files; collection 3064;
  fresh suite **3058 passed / 6 skipped / 0 failed /
  57 warnings** (5 rasterio + 1 documented lane skip);
  claim-scan clean; protected-path diff empty.
- **Head-claims commit** — README, this register, and the P0
  ledger now report the same counts and posture.

Owner-gated (unchanged): P5 authorization, opportunity-frame
Option A/B, real bytes, independent intake review, governance.

## Seismic event-detection sidecar addendum (2026-09-18)

The customer-requested seismic direction is recorded as a separate
research-only sidecar rather than a change to the frozen Nepal
weather/GLOF thin-PoC. `run_b/SEISMIC_EVENT_DETECTION_ADDENDUM_V0.md`
records the boundary:

- the Nepal feature frame has no seismic predictor or waveform loader;
- `landslide_coseismic` and the USGS Gorkha source are trigger-label
  surfaces only;
- T2A's USGS path is earthquake-catalog context and its geophone FFT/PSD
  path is disabled/unwired, so neither is treated as Nepal waveform
  evidence;
- a future sidecar may evaluate post-initiation abnormal detection using
  station-level FDSN/StationXML evidence, with `UNOBSERVABLE` when coverage
  or SNR is inadequate;
- P5 authorization, byte-bound waveforms, independent review and the
  observability gate remain open.

No source status, pilot outcome, preregistration, frozen feature contract,
warning authority or production status changes in this addendum.

## Round-11.8 seismic contract and provenance completion (2026-09-19)

The R11.8 repair leaves the hardened GLOF path unchanged while completing
the seismic contract surface. Config flags
and role sequences are strict and unique, malformed values return bounded
problems, feature columns cannot be coerced or duplicated, and window dates
and durations are bound to canonical UTC identities before semantic digests.
The added waveform/StationXML reader, event/opportunity/control package,
and feature-generation provenance chain are covered by **208** focused
tests and remain retrospective-only and non-associable.

The merged repository collects **3273** tests. The fresh canonical suite is
**3267 passed / 6 skipped / 0 failed / 57 warnings**. HMAGLOFDB bytes,
seismic real bytes, P5 authorization, independent intake review, FMX, and
real regime fitting remain owner/data-gated. Posture remains
`DESIGN_DRAFT_COMPLETE`,
`NO_QUALIFYING_PILOT_SOURCE`, and `WARNING_PATH_AUTHORIZED: NO`.

## Post-acquisition reconciliation — 2026-09-19 (Codex audit findings)

After the P5 acquisition + P3 intake + Phase-4 wiring commits
(`419356e` content, `480f48e` manifest rebind), the Codex audit
surfaced 24 findings. Disposition below — code-affecting items are
closed with committed evidence; owner-side gates remain open by
design.

| Finding | Disposition | Evidence |
|---|---|---|
| PROV-01 | **RESOLVED** | 5 ledger mismatches rebound to live bytes; 4 CDS interim `.nc` blocks marked `superseded` (prior digests preserved in notes), `snow_ledger_koshi.jsonl` reclassified `operational_log`; 0 mismatches remain |
| PROV-02 | **RESOLVED** | `retrieval/retrieval_record_era5_hma_operative.json` binds 78 operative files at frozen HMA anchors; stale PDGL-anchor record retained as superseded history |
| INT-01 | **RESOLVED** | `nepal/research_v0/p3_package_adapter.py` — P3 package → typed records → frozen ten-key runner package; digests recomputed from decoded content; `glof-events/p3_runner_package_v0.json` emitted |
| INT-02 | **RESOLVED** | 4 distinct role manifests (`retrieval/role_manifests_v0.json`); `RunEvidenceManifestV0` instantiated with all roles — `problems()==[]`, `verify_problems()==[]` |
| OP-01 | **RESOLVED** | `glof-lakes/lake_to_basin_linkage_v0.json` — explicit digest-bound pdgl→basin map (47 lakes, 0 conflicts) from `basin_coverage`; lake-level opportunities preserved verbatim |
| SCOPE-01/02 | **RESOLVED (record)** | `P5_SCOPE_RECONCILIATION_V0.md` — 3-basin final; hydrological (not national) event scope declared; 5-basin contract text reconciled |
| HOLD-01 | **RESOLVED** | `retrieval/holdout_feature_gate_report.json` — axes decoupled; regime groups train={koshi,gandaki}/heldout={karnali} verified PASS; event holdout preserved |
| FMX-01 | **RESOLVED** | Feature-role manifest binds both channels + derived frames + provenance; semantic matrix digest + cutoff `2025-10-01T00:00:00Z` declared |
| FMX-02 | **RESOLVED** | `verify_inputs_against_manifest` floor in `era5_anchor_intake.py`; tamper/undeclared/stale-sidecar probes fail closed |
| FMX-03 | **RESOLVED** | Real audit over 6900×19 frame → **FMX_PASS**, 0 rejects; `era5-multibasin/features/fmx_audit_report_v0.json` digested |
| REG-01 | **EXECUTED → CANDIDATE_ONLY** | Real `run_glof_descriptive_poc` ran; honest demotion — source posture `CANDIDATE_ONLY`/`UNREVIEWED` (reviews pending); authority flags all false; `retrieval/p5_glof_descriptive_receipt_v0.json` |
| REPLAY-01 | **RESOLVED** | `scripts/replay_p5.py` → **REPLAY_OK**: 131 ledger bytes + 4 role manifests + package digests + sidecars + authority flags all verify (caught + fixed a CWD-dependent `evidence_root` defect) |
| REL-01 | **RESOLVED** | stale `rebind_manifest_p5.py` quarantined to /tmp; `.write-leases/` gitignored |
| LIC-01 | **RESOLVED** | licence snapshots for all 4 roles (RDS7952 agreement extracted byte-bound; ERA5-CDS/GEE/HMA reference snapshots) |
| MULTI-01 | **RESOLVED** | CDS/GEE kept as distinct channel identities in feature-manifest lineage + `retrieval_record_era5_hma_operative.json` |
| GOV-01 / REV-01 | **OPEN — owner-side** | `retrieval/p3_review_packet_v0.json` binds 13 artifacts + 5 review questions for 2 reviewers + adjudicator; labels stay `UNADJUDICATED`, source `CANDIDATE_ONLY` until real review evidence exists — cannot be delegated to an agent |
| CTRL-01 | **BY DESIGN** | All controls `CENSORED_OR_AMBIGUOUS` (UNKNOWN opportunities) — no NEGATIVEs manufacturable this cycle; association stays stage-gated |
| DOC-01 | **RESOLVED** | This reconciliation + `P5_SCOPE_RECONCILIATION_V0.md` |
| STORAGE-01 / SEIS-01 / SEIS-02 | **PARTIAL** | obspy 1.5.1 cp314 **QUALIFIED** in isolated env (`seismic-4w/obspy_qualification_probe.json`: STEIM1/2 BE+LE decode, malformed-input rejection); waveform acquisition blocked on storage (11 GiB free vs 10 GiB cap + 8 GiB reserve) — separate volume or smaller-scope amendment needed |
| DEFER-01 | **DEFERRED** | association/forecast/warning/operations out of cycle |

Posture after this round: `DESIGN_DRAFT_COMPLETE`,
`NO_QUALIFYING_PILOT_SOURCE` (intake exists; review does not),
`WARNING_PATH_AUTHORIZED: NO`.


## R11.9 semantic-binding closure — 2026-09-19 (SWE2)

Post-acquisition audit (20 findings) closed the remaining structural
proof gaps; the posture gate's `CANDIDATE_ONLY` hold is intact and
owner-side gates remain open by design.

| Finding | Disposition | Evidence |
|---|---|---|
| R11.9-01 verifier/rebind | CLOSED | GLM3 lane landed `0113677` + `49d46e8`; suite 3324/6/1 then module-move fix verified by focused reruns |
| R11.9-02/13 two-reviewer intake | CLOSED — reviews adjudicated | `reviewer_1`=sanjayb (owner, agent evidence adopted), `reviewer_2`=RAVI (independent, in-thread signature), `adjudicator`=sanjayb (owner, dual role disclosed); `evidence_sidecar_v0.json` binds source/version/license/coverage/timing/reviewers/decision; source promoted `EVIDENCE_VERIFIED` + `INDEPENDENTLY_VERIFIED` |
| R11.9-03/10 real regime + holdout | BLOCKED — structural | reviews closed + source `EVIDENCE_VERIFIED`; engine invoked; honest `RUN_ERROR`: >=3 fit groups vs 2 after LORO holdout — governed run needs >=4 frame groups (4th-basin acquisition or owner amendment); gate must NOT be weakened |
| R11.9-04 FMX role binding | CLOSED | `run_real_fmx` now requires the verified feature-role manifest; frame bytes re-checked before parse; report binds `feature_role_digest`, `frame_sha256`, `row_universe_digest`, `semantic_matrix_digest` |
| R11.9-05 asserted preprocessing | CLOSED | `PREPROCESSING_PROVENANCE_V0` record persisted (`features/preprocessing_provenance_v0.json`); `fitted_row_count`=4600 train rows + `train_row_digest` recomputed from live bytes — mismatches emit `FMX_BLOCKED_PROVENANCE` |
| R11.9-06 hard-coded cutoff | CLOSED | `CUTOFF_RECORD_V0` persisted (`features/cutoff_record_v0.json`) bound to `retrieval_record_era5_multibasin.json` sha + `pull_utc_end`; availability margin proven; failures emit `FMX_BLOCKED_CUTOFF` |
| R11.9-07 real-evidence FMX test | CLOSED | `tests/test_r11_9_bindings.py` — 22 tests: 13 synthetic tamper probes + 9 real-evidence gates (skipif when root absent) |
| R11.9-08 semantic role binding | CLOSED | `semantic_binding_problems` in `run_evidence.py` — package/event-role digest, frame membership + allowlist, FMX role/frame digests, sidecar control-doc coverage all enforced |
| R11.9-09 integrity-only replay | CLOSED | `replay_p5.py` now rebuilds the FMX report, ledger fields, package digests, semantic bindings, and receipt `report_digest`; `REPLAY_OK` requires digest equality, `replay_scope: full_recomputation` |
| R11.9-11 censored controls | BY DESIGN | unchanged — no manufacturable negatives; association stage-gated |
| R11.9-12 scope scan | CLOSED | remaining five-basin strings are historical authorization/template records superseded by `P5_SCOPE_RECONCILIATION_V0.md`; operative code/manifests carry only the three-basin universe |
| R11.9-14/16/17 seismic bytes/storage/execution | OPEN — owner/physics | storage ~11 GiB < 10 GiB cap + 8 GiB reserve; acquisition still gated |
| R11.9-15 STEIM admission | PARTIAL→GATED | `allow_steim_decoding` flag now WIRED in `io.py` (False fails closed with real decoder reason; True adopts qualified obspy traces) — governed-env qualification still required before flag use |
| R11.9-18 release metadata | CLOSED | this block + manifest rebind + README/ledger updates |
| R11.9-19 unbound control docs | CLOSED | sidecar role now binds review packet, holdout gate report, anchor record, coverage ledger, era5_multibasin retrieval record, FMX report, cutoff + preprocessing records (16→23 files); run products stay self-sidecarred to avoid a self-referential manifest |
| R11.9-20 deferred surfaces | DEFERRED | association/forecast/warning/operations unchanged |

Probes fixed during implementation: annotation-field ledger compare,
eager-fixture evaluation, circular-import ordering in replay.
Posture: `DESIGN_DRAFT_COMPLETE`; source `EVIDENCE_VERIFIED` (reviews adjudicated 2026-09-19); regime `RUN_ERROR` — structural (>=4 frame groups required); `WARNING_PATH_AUTHORIZED: NO`.

## R11.9 closure round — 2026-09-19 (second Codex audit)

| Finding | Disposition |
|---|---|
| R11.9-21 | **RESOLVED** — cutoff record rebound to canonical `retrieval_record_era5_hma_operative.json`; `cutoff_record_problems` now requires `canonical:true`, operative anchor-set equality, and rejects non-HMA file lists |
| R11.9-22 | **RESOLVED** — canonical record carries evidence-derived `pull_utc_start/end` (CDS start → EE completion 07:48:22Z; never mtime) + per-channel completions; validator rejects missing/stale/pre-completion times |
| R11.9-23 | **RESOLVED** — full chain regenerated: retrieval→cutoff→preprocessing→FMX→manifests→package→receipt→replay, all digests recomputed |
| R11.9-24/25 | **ESCALATED** — regime honest `RUN_ERROR` (2 fit groups < MIN_GEO_GROUPS=3); `scope_amendment_fourth_group_v0.json` prepared (koshi L2 sub-basin split, ~26 requests) — owner signature required; no invented coords, gate untouched |
| R11.9-26 | **RESOLVED** — holdout report split: `axis_preflight=PASS` / `engine_admissibility=BLOCKED` |
| R11.9-27 | **RESOLVED** — carrier group domains derived from frame bytes at audit time, not hardcoded |
| R11.9-28 | **RESOLVED** — sidecar + scope doc distinguish source-level verification from label adjudication (labels stay UNADJUDICATED) |
| R11.9-29 | **RESOLVED (disclosed)** — dual role recorded in adjudication + sidecar; conservative posture retained; stricter reading needs a third non-owner review |
| R11.9-30 | **RESOLVED** — replay report carries `regime_replay_state=regime_execution_blocked` |
| R11.9-31/32 | **RESOLVED** — this addendum; one content commit + manifest rebind |
| R11.9-33/34 | **PARTIAL** — obspy qualified; acquisition storage-gated (separate track) |
| R11.9-35/36 | **BY DESIGN** — censored controls, deferred surfaces |

EE pull-geometry deviation (grid-snapped boxes vs declared ±0.1°
anchor boxes) is now disclosed in the canonical retrieval record.
