# DISPATCH PACKET — C1 (Framework/Code Lane)
**RUN_ID:** `run-20260912-2a8757b` | **Status:** READY TO DISPATCH
**Model label:** Codex 5.3 (record actual runtime model ID, tools, execution mode; never silently substitute)

## Anti-hallucination preamble (paste verbatim at top)
You are one isolated worker in a gated, evidence-first Phase B-to-F campaign.
Do not infer completion from narrative, prior reports, green structural tests, filenames, or another agent's prose. Inspect the live filesystem and execute bounded checks.
Every claim must be backed by: exact path, file size, SHA-256, schema/semantic validation, source+processing lineage, contract binding, and command+exit-code evidence.
Allowed states: READY, BLOCKED, INCOMPLETE, FAILED, UNAVAILABLE, NEEDS_INPUT. READY is forbidden unless all required evidence is present.
Never: invent hashes/values/URLs/files/credentials/model IDs; use synthetic data as real evidence; flip booleans; weaken thresholds or stability rules; delete/reset/clean/broadly-stage user files; overwrite the existing reconciled data root; write outside the declared allowlist; accept arbitrary caller paths or gate booleans; claim production/warning/authority/field validation.
If root, contract, credentials, model identity, disk guard, or input semantics are ambiguous, stop and emit NEEDS_INPUT or UNAVAILABLE. Atomic writes and checkpoints. Redact secrets. Bounded timeouts; report incomplete runs honestly.

## Identity & bindings
- Authoritative root: `/Users/sanjayb/nepal-event-anomaly`
- BASE_HEAD: `8eeeccc60231a3aa2e55c14ce790fda3fea32bcd` (2026-09-12T14:23+05:30 — includes the verified INTEGRITY_POC_V1: `integrity_poc.py`, tamper benchmark, cli subcommand. Build on it; do not rewrite it. If it blocks a gate fix, document why.)
- RUN_CONTEXT: `/Users/sanjayb/nepal-event-anomaly/.phase-loop/parallel_runs/run-20260912-2a8757b/RUN_CONTEXT.json` — read first
- Framework contract runtime sha256 (current): `7748b089b8bf29a705090e0ba7751a5f8ecd15ad51272bfd090c54ce0e1b6c69`
  - STALE BINDING ALERT: `data/framework_inputs_v1_reconciled/handoff_gate_result.json` records `ecda408e…` (pre-commit). Reconcile: confirm whether the contract change in HEAD is intended; report the new runtime hash; any data packet bound to ecda408e is invalid until rebound.
- Data contract (feature_contract.py) sha256: `240a3615f89cdd9c49b6833623084d4dbdd5b5f85c5b50e1fe4ea7c84513ec0a`

## Worktree (create, then work ONLY inside it)
```
cd /Users/sanjayb/nepal-event-anomaly
git worktree add /Users/sanjayb/nepal-event-anomaly-worktrees/run-20260912-2a8757b-code -b c1-run-20260912-2a8757b 8eeeccc60231a3aa2e55c14ce790fda3fea32bcd
```
Packet output: `/Users/sanjayb/nepal-event-anomaly/.phase-loop/parallel_runs/run-20260912-2a8757b/packets/C1/`

## Write allowlist (inside worktree only)
`nepal/framework_v1/`, `tests/test_framework_v1_*.py`, approved framework CLI/schema/doc files.
Never: data roots, preregistration.md, legacy results, frozen artifacts, the dirty main checkout.

## Read first (prove, don't rewrite)
- `FRAMEWORK_V1_CURRENT_STATUS.md` — authorization path + proof boundaries
- `nepal/framework_v1/` modules: contract, preflight, input_manifest, catalog, controls, adapters, validation, verification, screen, provenance, briefing, extensions, cli
- `tests/test_framework_v1_*.py` (10 files) + `data/framework_inputs_v1_reconciled/tests_reconciled/` (76-test matrix)
- `git status`/`git diff` of main checkout (context only — never modify it)
- G01–G42 register from the plan document

## Gate IDs owned
G03, G04, G06, G07, G12–G19, G21–G25, G31–G37, G41, G42

## Required engineering
- Failing tests before fixes; prove already-implemented behavior with tests first
- Canonical JSON as sole authorization hash; fail-closed when expected hashes absent
- Exact artifact-role mappings; typed raster validation (units, dtype, finite, ranges, nodata, resampling, CRS, affine, bounds, shape)
- Population loaded-but-inactive unless versioned control activates; aspect excluded from leave-one-layer-out stability; preserve active-component registry
- Deterministic O(n log n) percentile behavior and tie equivalence
- Reject wrapper-shaped observability input, caller-supplied A/control booleans, arbitrary component paths
- Authenticate complete A/B/E/F envelopes
- CLI status/exit codes distinguish input-blocked vs ranked-but-gate-blocked vs success
- Timeout, checkpoint, progress, resumability, atomic output
- Package-inventory and duplicate-contract drift tests
- Update stale test to prove both default blocking and explicit-hash readiness
- Keep C and D intentionally blocked
- No threshold/weight/contract/semantic changes merely to obtain PASS

## Verification commands (capture output + exit codes)
```
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q tests/test_framework_v1_*.py
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m pytest -q data/framework_inputs_v1_reconciled/tests_reconciled/
pyright nepal/framework_v1
PYTHONDONTWRITEBYTECODE=1 .venv/bin/python -B -m compileall -q nepal tests
```
(Note: in the worktree, .venv is not copied — symlink or use the main .venv interpreter path.)

## Return
One commit hash, changed-file list, command evidence, evidence packet in packets/C1/. State: READY / BLOCKED / INCOMPLETE / FAILED / NEEDS_INPUT.

## Recommended skills for the C1 session
`conformance-gate` (prove contract conformance), `python-testing` or `ecc-tdd` (failing-tests-first), `agent-phase-ratchet` (phase ledger), `fuzz-regression` (forged/tamper input cases), `ecc-ml-engineering` (avalanche-model context).
