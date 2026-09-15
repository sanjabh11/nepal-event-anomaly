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
| P0-01 count discrepancy | RESOLVED — authoritative census: `--collect-only` = 1311 nodes = 1306 passed + 5 rasterio skips; manifest's 1306 verified correct. External 1300 was a stale/env-differentiated observation. |
| DOC-01 historical vs current counts | RESOLVED — README and P0 label 1282 as ledger-time history, 1306 as current; historical evidence retained |
| DOC-02 stale extractor comments | RESOLVED — both comments now state route-dependent semantics (ARCO increments vs MARS/EDH running accumulation) |
| RUN-01 derived-run provenance | RESOLVED — derivation ledger carries `run_kind=derived`, `input_run_bundle_sha256`, `acquisition_disk_check=not_applicable_derived_run` |
| ENV-01 warnings/skips | RESOLVED — 56 warnings classified: all xarray/netCDF4 deprecations in `test_p5_io_contract.py`; skips disclosed |
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
| DOC-01 count drift | RESOLVED — README/P0 record 1282/1306/1641 as dated history; 1687 current at post-audit head |
| DOC-02 metadata-only scope | RESOLVED — SOURCE_FEASIBILITY_RECORDS_V0 labeled dated metadata-only snapshot |
| GOV-01/02 | RESOLVED — P3 design-only vs P5-C acquisition separated; see STATUS_SCOPE_RECONCILIATION_NOTE_20260915 |
| H01/H03 | RESOLVED (wording) — Run A docs state hybrid route, method-only, bounded 5-month overlap, no K/JS transfer |

SRC-*/DATA-*/FMX-*/REG-*/ASSOC-*/FCST-*/OPEN-01/GOV-03/OPS-01 remain
`GATED_ON_DATA` / `BLOCKED_EXTERNAL` / deferred — unchanged by design.

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
