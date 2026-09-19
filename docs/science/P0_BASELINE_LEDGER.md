# P0 Baseline Ledger — v0

**Recorded:** 2026-09-13. Engineering-proof baseline only; no scientific
claim is implied by anything here.

| Field | Value |
|---|---|
| Canonical integration root | `/Users/sanjayb/nepal-event-anomaly-worktrees/full-framework-v1-20260912-144802/integration` |
| HEAD at baseline | `5ef43c292ce41a93c9adec55185973caa7045285` |
| Working tree at baseline | clean (`git status --porcelain` empty) |
| Runtime | pinned `.venv` at worktree root (`.venv/bin/python`, interpreter ends in `/python`); `ruptures==1.1.9` installed from `requirements.txt` |
| Test invocation | `PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=. .venv/bin/python -B -m pytest tests/ -q` |
| Disk | ~8.2 GiB free at baseline; dipped to ~7.1 GiB mid-cycle (guard correctly refused writes); **~15 GiB at re-check 2026-09-14 (round-7)**. Pre/post-write checks required on every future write; serial acquisition aborts below 8 GiB |
| Frozen preregistration | `preregistration.md` untouched; frozen 2026-09-10; single-event retrospective Langtang hindcast |
| N5 / gates | B strict path hash-bound fail-closed; C/D intentionally blocked; E needs verified A+B; F strict needs A+B+E; FMX blocked (no verified external freeze token) |
| Authorization flags | `warning_path_authorized=false`, `production_authorized=false`, `promotion_eligible=false` everywhere |
| Empty Git shell (DO NOT USE) | `/Users/sanjayb/Documents/ChatGPT/nepal-event-anomaly` — no commits; not the implementation root |

## Path/HEAD guard (G01)

Every future command against this project must first resolve the
canonical integration root and exact HEAD above. Abort on any mismatch.
The empty Git shell is never the implementation root.

## Test results (exact, as run)

- `pytest tests/test_research_v0_*.py`: **116 passed** (2026-09-14,
  hardened contracts incl. adversarial A/B/C/D/E-series probes).
- `pytest tests/test_p5_io_contract.py`: **34 passed** (2026-09-14,
  P5 I/O contract — payload normalization, cell tie, year-range,
  JJA universe, edge censoring, marker/area/cell gates,
  full-chain layout).
- Full suite under pinned `.venv`: **1282 passed, 5 skipped,
  0 failed** — both prior environmental failures resolved
  (interpreter ends in `/python`; `ruptures` installed). The 5 skips
  are `rasterio`-dependent frozen-package tests — optional-dependency
  gates, not waived coverage; they are disclosed, not hidden:
  `test_framework_v1_adapters.py:80`, `:92`, `:109`, `:128`,
  `test_framework_v1_screen.py:123`.
- Content commit: bound via `content_head` in the manifest
  (bound head is the manifest's own field — see
  `ARTIFACT_MANIFEST_V0.json` for the current value).

## Current head (round-11 thin-PoC acceleration)

- The Round-11 audit froze the R10 contract and directed a thin
  byte-bound descriptive slice — no further validator hardening
  unless a live intake or PoC failure exposes a defect.
- New surface (audit-pinned interfaces only):
  `nepal/research_v0/source_intake.py` (`build_source_manifest`,
  `load_hmaglofdb_rows`), `nepal/science_v0/glof_poc.py`
  (`build_hmaglofdb_event_package`, `run_glof_descriptive_poc`
  emitting the non-promotable `GLOF_POC_RECEIPT_V0`),
  `docs/science/run_b/GLOF_POC_CONTRACT_V0.md`, and
  `tests/test_glof_poc_contract.py`.
- Documentation reconciled to one status vocabulary (CC BY 4.0
  governs HMAGLOFDB metadata; payload/Nepal-v1.3-count/
  opportunity-frame remain PENDING); P5 authorization section
  added to `P3_ATTESTATION_TEMPLATE.md`.
- Suite counts bound via the manifest at the final head; see
  `ARTIFACT_MANIFEST_V0.json` for the verified totals.

## Prior verified baseline (round-10 hardening)

- HEAD: Round-10 hardening content commit (bound via `content_head`
  in `ARTIFACT_MANIFEST_V0.json`; the manifest rebind lands as its
  direct child, same relationship as previous binds).
- Full suite under pinned `.venv`: **2757 passed,
  5 skipped (rasterio), 0 failed, 57 warnings** — verified at the
  Round-10 hardened content commit.
- Manifest files governed: **84** (81 prior + root `conftest.py`
  collection guard + `tests/test_r10_promotion.py` + ledger
  updates).
- A Round-10 independent audit reopened the Round-9 "complete
  shared floor" claim — sixteen findings closed this round
  (see `GAP_REGISTER_V0.md` Round-10 census): bounded numeric
  parsing (`_finite_float` — no numeric probe raises through any
  boundary, including the auditor's richer checks), complete
  serialized-config semantics (`_config_semantic_problems`),
  ordered-sequence duplicate rejection, null/blank partition
  carrier preflight in `run_regimes`, exact digest/count typing,
  non-coercing `RegimeAssignmentArtifact` construction with exact
  map coverage, exact non-fixture source-manifest schema,
  component-walk symlink policy with identity pinning, the exact
  status×terminal×associable state machine, complete null-family
  record validation, exact field sets across the whole envelope,
  typed byte-bound `forecast_vintages` binding for associable
  forecast artifacts, forged-artifact association evidence in the
  mutation matrix, the canonical-collection guard
  (repo-wide == `tests/`, the ignored external symlink never
  collected), and this addendum.  `tests/test_r10_promotion.py`
  carries 520 tests — 89 mutations × floor/freeze/adapter/audit/
  association — plus the coordinator's ~60 executable adversarial
  probes (one residual found and fixed in-round: `cutoff_iso`
  rebound to the recomputed max train date).

## Prior verified baseline (round-9 hardening)

- HEAD: Round-9 hardening content commit (bound via `content_head`
  in `ARTIFACT_MANIFEST_V0.json`; the manifest rebind lands as its
  direct child, same relationship as previous binds).
- Full suite under pinned `.venv`: **2252 passed,
  5 skipped (rasterio), 0 failed** — verified at the Round-9
  hardened content commit `ae1b03e`.
- Manifest files governed: **82**.
- New surface: `nepal/research_v0/producer_validation.py` hardened
  into a complete producer-validation boundary —
  `validate_producer_payload(payload, *, verify_source_bytes=True)`
  now runs byte-verified source-evidence binding at every boundary
  (freeze, adapter, audit, association producer-payload binding);
  strict `RunManifestV0` deserialization; recomputed row universes
  and the 6-decimal semantic feature-matrix digest over
  `input_values`; config↔artifact cross-binding; a strict
  positive-definite covariance floor; exact seed/gate/null/status
  semantics; and `unit_basin_map` + `unit_basin_map_digest` bound
  from the payload through the adapted artifact into
  `run_association`'s `unit_basins` equality check.
  An independent adversary pass then closed twelve residual
  holes the matrix missed (`R9-V1..V12`: association-binding
  admissibility, fit∩heldout disjointness, row uniqueness,
  calendar/label bounds, material-bound `input_bytes_digest`,
  scalar type floors, forecast-field mode bans, required bound
  digests, model/feature-width binding, missingness accounting,
  typed-section field exactness, null-replicate bound, and a
  crash-not-finding decode path).
  `tests/test_r9_promotion.py` carries the 33-mutation ×
  4-boundary promotion-closure matrix plus the 30-test
  `TestR9AdversarialResiduals` regression class.
- Prior baseline retained as history: round-8 hardening,
  **2073 passed, 5 skipped (rasterio), 0 failed, 57 warnings** —
  retained verbatim as the prior verified baseline below; round-7
  content `cc2218b`, 77 manifest files, 2015/5/0/57, 562 focused;
  round-6 content `aceedff`, 76 files, 1993/5/0/57, 540 focused.

## Prior verified baseline (round-8 hardening)

- HEAD: Round-8 hardening content commit (bound via `content_head`
  in `ARTIFACT_MANIFEST_V0.json`; the manifest rebind lands as its
  direct child, same relationship as previous binds).
- Full suite under pinned `.venv`: **2073 passed, 5 skipped
  (rasterio), 0 failed, 57 warnings**.
- New surface: `nepal/research_v0/producer_validation.py` (shared
  producer validator), `tests/test_r8_provenance.py` (31),
  `tests/test_r8_vintage.py`, `tests/test_r8_fmx_assoc.py`;
  `verify_vintage_evidence` in `nepal/research_v0/_hashing.py`;
  `ForecastVintageV0.evidence_root`; byte-bound
  `FORECAST_EXPERIMENT_ONLY` gate in `evaluate()`; FMX label
  derivation + required metadata in `science_v0/fmx_audit.py`;
  spatial-shift support accounting in `experiment_v0/association.py`.
- Prior baseline retained as history: round-7 content `cc2218b`,
  77 manifest files, 2015/5/0/57, 562 focused; round-6 content
  `aceedff`, 76 files, 1993/5/0/57, 540 focused.

## Prior baseline (2026-09-16, round-7 hardening)

- HEAD: `cc2218b` — "Round-7 enforcement hardening" (content
  commit; the manifest rebind lands as its direct child, same
  relationship as previous binds).
- Manifest/HEAD relationship: `ARTIFACT_MANIFEST_V0.json` carries
  `content_head=cc2218b` — the **commit whose tree the manifest
  hashes**; the manifest commit itself follows the content commit.
  77 manifest files (76 + test_r7_hardening.py).
- Full suite under pinned `.venv`: **2015 passed, 5 skipped
  (rasterio), 0 failed, 57 warnings**.
- Focused lanes: **562 green** (round7-hardening 22 + round6
  hardening 118 + round5 hardening 18 + regimes 93 + association 63
  + evaluation 118 + audit 61 + adapters/replay/e2e).

## Verification snapshot history (2026-09-15, round 2 and later)

- HEAD at round-2 snapshot: `d3d9238`-series.
- Full suite under pinned `.venv` (round-2 snapshot): **1306 passed,
  5 skipped (rasterio), 0 failed** — `pytest --collect-only` reported
  1311 nodes (1306 + 5). An external audit observed 1300 in an earlier
  environment; the manifest's 1306 was verified correct at that HEAD.
- Post-swarm snapshots: **1641** after the first science_v0 +
  experiment_v0 integration; **1828 passed, 5 skipped (rasterio), 0
  failed** at the post-audit residual-repair head (round-4; superseded
  by the verified baseline above). Earlier counts are retained as
  dated history. 24 Run-A-reconciliation tests (calendar validity,
  claim-scan recursion, ledger repair, canonical JSON, accumulation
  semantics).
- Warnings: 57 total at the verified baseline (this block earlier
  said 56 — corrected 2026-09-16 to match the verified census and
  the count already recorded above): 56 are xarray/netCDF4
  `DeprecationWarning`s in `test_p5_io_contract.py` and 1 is a
  sklearn `ConvergenceWarning` in `test_science_v0_regimes.py` —
  all library-level, none in the research namespace. 5 skips are
  `rasterio` optional-dependency gates — disclosed, not waived.
- Run A corrected derivative: `research_runs/
  gmm_hybrid_corrected_20260915/` — sf running-accumulation defect
  (RA-01) corrected via closing-value aggregation; modal K=5
  recomputed (not inherited); JS 0.2702 [0.2505, 0.2944];
  status `EXPLORATORY_DESCRIPTIVE_SINGLE_CELL`, method-only pending
  owner ratification of the hybrid route.
- Independent rehash: `rehash_report.json` in each run root; all
  recorded digests match recomputed bytes (183 + 16 files).
- Run ledger: `size_accounting` measured from bytes; timestamps carry
  `*_utc` normalization with explicit `timestamp_classification`.
- Run A reconciliation (2026-09-15, `run-a/reconciliation` lane):
  valid-day request chunking (no June-31-class cross products),
  UTC `Z` ledger timestamps, byte-measured size accounting
  (run ledger now reports ~40.19 MB across raw/monthly/merged),
  request-vs-used window fields + JJA-only merged assertion,
  recursive `claim-scan` (CI glob defect closed), canonical-JSON
  result persistence with scaler/GMM parameters, and a
  `provenance_receipts.json` binding recoverable route evidence
  (unrecoverable fields recorded as UNVERIFIED, never fabricated).
- CLI smoke: `validate-envelope` re-verifies flag/status/digest shape,
  self-hash, real matrix/policy bytes, and record payloads; execution
  statuses refuse no-bundle validation; `claim-scan` clean on all docs;
  `horizons`/`embargo` fail closed on invalid input.

## What was added this cycle (additive only)

- `docs/science/` — this ledger, decision matrix, cutoff/target policy,
  source records, gap register.
- `nepal/research_v0/` — import-isolated research-only record types,
  policy primitives, gates, and a validation-only CLI. **Zero imports of
  `nepal.framework_v1`** (negative import tests enforce this).
- `tests/test_research_v0_*.py` — contract-layer tests.
- `README.md` — legacy quick-start paths marked non-pilot.

**Not modified:** `nepal/framework_v1/`, `preregistration.md`, `pinned/`,
`data/framework_inputs_v1_reconciled/`, all existing tests.

## Run A result — P5 descriptive GMM confirmation (2026-09-15)

The bounded ERA5-Land acquisition executed and completed. Run root:
`research_runs/gmm_hybrid_20260915` (gitignored). Retrieval was hybrid
because the MARS queue serializes per-account requests (1 running +
3 queued; verified via CDS jobs API QoS metadata):

- `t2m, d2m, u10, v10, tp` — CDS `reanalysis-era5-land-timeseries`
  (ARCO copy of the same archive), one request, cell (28.3, 85.5).
- `sd, sf` 2001–2025 — Earth Data Hub DestinE Zarr mirror of
  `reanalysis-era5-land` (coverage ends 2026-05-31, last closed month).
- `sd, sf` 2026 (Jun–Jul + Aug 1–25) — `reanalysis-era5-land` MARS,
  2 requests.

All 78 months passed the unchanged `normalize_payload` gate (exact
vars, exact hourly timestamp sets, finite values, no post-cutoff data,
`sde` absent). Merged: 57,264 hours; `merged/complete.json` digest
matches. Features: 2,386 JJA daily rows, 156 edge-censored, 2,230
usable; selected cell (28.3, 85.5), model elevation 4322 m. GMM:
modal K=5 (3/3 seeds, all converged), JS 0.2618 (95% CI
[0.2391, 0.2856], null p95 0.0456, exceeds null), pre-event JS 0.5084.
Cross-reference vs reported literature values: 7-day mean T 9.42 °C
(reported 9.43 °C), PDD 65.94 °C·d (reported 65.94 °C·d).

Status: `EXPLORATORY_DESCRIPTIVE_SINGLE_CELL` — descriptive regime
structure on one grid cell only. This is NOT a forecast, precursor,
warning, or pilot result. `nepal/era5_hybrid_fetch.py` added (assembly
+ provenance-recorded retrieval paths).

## Owner designations

- **P3 approver (designated):** repository owner (user), 2026-09-14.
  Designation is recorded here as intent; the attestation itself
  remains an external, human-verified act — `human_approved=True`
  fields stay structural only and no code path authenticates identity.
- **Engineered dam-breach vertical:** deferred
  (`DEFERRED_NO_OPEN_TIMED_SOURCE`) — no open structured Nepal
  inventory located; owner-accepted.
- **Pilot selection:** owner pre-commits to
  `FIRST_PASSING_ALL_GATES_ELSE_NO_QUALIFYING` — if no vertical clears
  its gates, `NO_QUALIFYING_PILOT_SOURCE` stands.
- **License order:** GLOF + snow-avalanche sources first
  (HiAVAL, HMAGLOFDB), then Sentinel-1, then forecast archives; MARS
  procurement deferred until a vertical actually needs it.

## Standing prohibitions (unchanged)

No downloads before P3 design approval + P5 intake gates. No FMX freeze
without a verified external freeze token. No clustering/regime fitting
on real data beyond Run A's authorized single-cell bounded descriptive
GMM implementation confirmation (method-only,
`EXPLORATORY_DESCRIPTIVE_SINGLE_CELL`); multi-region regime discovery
and event–regime association remain gated under Run B and have not run
on real data. No claims about operational use, warnings, production,
prediction, or scientific validation. Existing green tests are
contract-layer evidence, not real-data science.

## Round-11.2 (2026-09-18) — provenance micro-round

Content head: `586c08e` (plus the README/ledger head-update commit).
Basis: 2944 collected, **2939 passed / 5 skipped / 0 failed /
57 warnings**; focused lanes 204 + 803 green; manifest verified
(89 files — `tests/test_r11_2_provenance.py` added).  The R11.2
audit live-probed the R11.1 boundary and found nine residual
acceptance paths; all seven codeable findings are closed
(findings 8-22 remain owner-gated or stage-gated exactly as the
audit deferred them).  Provenance convention maintained: content
commit(s), then one manifest-only rebind; `content_head` and
`manifest_commit` name the final content commit.

## Round-11.3 (2026-09-18) — provenance micro-round

Content head: `9b7169f` (plus the README/ledger head-update commit).
Basis: 2967 collected, **2962 passed / 5 skipped / 0 failed /
57 warnings**; focused lanes 227 + 699 green; manifest verified
(90 files — `tests/test_r11_3_provenance.py` added).  The R11.3
audit live-probed the R11.2 boundary and found four residual
acceptance paths plus the source-card discrepancies; all codeable
findings are closed (runner-side manifest re-verification, bounded
digests, exact typed records, >=2-reviewer sidecar binding,
command consistency, SRC-01 reconciliation).  Owner-gated items
remain unchanged.

## Round-11.4 (2026-09-18) — documentation/source-matrix repair

Content head: `9bd856b`.  Basis: 2967 collected, **2962 passed /
5 skipped / 0 failed / 57 warnings** (fresh canonical rerun at
this head); focused lane 190 green.  Serial doc-only
reconciliation of external claims — ds084001 span, NODD
classification, v1.0 timing statistics, GF_ID/Repeat/_Z/day
semantics, four licence surfaces, Zenodo payload identity,
target-date scope.  No code or posture changes.

## Round-11.4.1 (2026-09-18) — residual documentation repair

Content head: this content commit (GLM-5.3 lane).  Basis: the
R11.4 verified counts carry unchanged — **2962 passed /
5 skipped / 0 failed / 57 warnings**, 2967 collected — because
this round is documentation-only and no code or tests were
touched; the canonical suite was not re-run per the dispatch
rule.  Repairs: GitHub licence provenance (fetched 2026-09-18,
GLM3 lane), TIGGE normalized to `CANDIDATE`/`CANDIDATE_ONLY`,
`EVENT_PACKAGE_SPEC_V0.md` `_Z`/join-key correction, NCEI GEFS
official end `2020-09-23`.  Verified by stale-claim greps,
`git diff --check`, protected-path diff, and manifest rebind.
No code or posture changes.

## Round-11.5 (2026-09-18) — real-path test lanes

Content head: `a0bc8ff` (SWE2 test lanes) + this verification
record commit (GLM3 verifier/rebinder).  Basis: **3061 collected,
3055 passed / 6 skipped / 0 failed / 57 warnings** — fresh
canonical `tests/` run at this head (1518 s).  Focused lanes:
`test_p5_glof_intake` + `test_real_fmx_audit` +
`test_glof_poc_real_path` + `test_regime_real_path` =
**93 passed / 1 skipped** (documented skip: cached-artifact
`CANDIDATE_ONLY` gate-consistency rejection is exercised under a
descriptive artifact).  Skips: 5 disclosed rasterio quarantine +
1 lane skip.  Four new test files added to the manifest and to
CI triggers + contract step; no production code changed.
Owner-gated items unchanged.

## Round-11.5 release closure (2026-09-18) — provenance invariant

Content head: `285e933` (SWE2 release-closure) + this
head-claims commit (GLM-5.3 lane).  Basis: **3064 collected,
3058 passed / 6 skipped / 0 failed / 57 warnings** — fresh
canonical `tests/` run at the rebound state (1077 s); the +3
collection delta is the closure regression file
`tests/test_r11_5_release_closure.py` (manifest-commit identity,
lane governance, collection census — 3 passed).  Skips: 5
disclosed rasterio quarantine + 1 documented lane skip.
The release-provenance defect (`manifest_commit` stale at
`bb22ca2`) is closed: `manifest_commit == content_head` names
the final content commit, enforced by a CI identity-guard step
and the regression file.  Manifest rebound to 95 files
(`8c788a8`).  No production code changed; no posture change.
Residual `verify-manifest` gap closed at R11.5.1 (`106b3d2`):
the CLI now independently rejects a stale or manifest-only
`manifest_commit`, verified by an accept/reject regression pair.
Fresh canonical suite at `73fd1a8`: **3059 passed / 6 skipped /
0 failed / 57 warnings** (3,065 collected).  Owner-gated items
unchanged.

R11.7 seismic sidecar (`8e17cfd` + reb `a43a078`): the
research-only seismic vertical landed — derived observability
gates (forged OBSERVABLE rejects), byte-pinned waveform reads,
15-key exact receipt with config_digest, canonical window
identities, explicit station holdout, and
SEISMIC_WAVEFORM_RETROSPECTIVE bound to the retrospective lane
only (non-associable, rejected on forecast/adapter/association).
Fresh canonical suite: **3203 passed / 6 skipped / 0 failed /
57 warnings** (3,209 collected, 107 governed files).  Seismic
posture unchanged: design-only, no station/waveform bytes, no
EVIDENCE_VERIFIED source, UNOBSERVABLE terminal.  Owner-gated
items unchanged.

## Seismic event-detection sidecar addendum (2026-09-18)

The customer seismic recommendation is captured in
`run_b/SEISMIC_EVENT_DETECTION_ADDENDUM_V0.md` as a separate,
post-initiation research track. This documentation-only change records
that the current Nepal weather/GLOF thin-PoC has no seismic predictor,
separates T2A earthquake-catalog context from disabled geophone spectral
code, and defines station observability, P5 and byte-binding gates for any
future waveform sidecar. No payload bytes were retrieved; no source,
pilot, warning or production posture changed. The focused documentation
regression is `tests/test_research_v0_seismic_addendum.py`.

## Round-11.8 seismic contract and provenance completion (2026-09-19)

The R11.8 content change closed the direct-probe validation seams: strict
boolean flags, exception-safe unhashable-value handling, unique and
disjoint waveform/response roles, unique holdout/catalog sequences, strict
feature-column declarations, bounded non-DataFrame errors, real calendar
dates bound to UTC `window_start`, and exact `window_seconds` duration
binding.

The parallel seismic content lane is preserved alongside it: verified
waveform/StationXML I/O, derived observability, typed
event/opportunity/control packaging, and feature-generation provenance.
tests pass **208/208**; the R11.8 validation/shared lanes pass **161** and
**904/1 warning**, respectively.

The merged tree collects **3273** tests. The fresh canonical suite records
**3267 passed / 6 skipped / 0 failed / 57 warnings**. No bytes, P5
authorization, protected paths, or authority flags changed.


## R11.9 semantic-binding closure (2026-09-19)

P5 owner-authorized acquisition landed earlier this round (HMAGLOFDB
v1.3.0, PDGL 2015, RDS7952, dual-channel ERA5-Land; 131 ledger payloads).
This content change closes the semantic-binding findings: the real FMX
audit now enters only through the byte-verified feature-role manifest
(`nepal/real_fmx.py`), the report binds role/frame/row-universe/semantic
digests, the cutoff is a persisted `CUTOFF_RECORD_V0` bound to retrieval
completion, and preprocessing provenance is a persisted
`PREPROCESSING_PROVENANCE_V0` record whose 4,600-row train partition is
recomputed from live bytes rather than asserted.  `semantic_binding_problems`
(R11.9-08) binds package/frame/report to the declared roles, and
`scripts/replay_p5.py` is now full recomputation — REPLAY_OK requires
rebuilt digests to match.  The sidecar role binds all consumed control
documents (16→23 files); run products stay self-sidecarred to avoid a
self-referential manifest.

Evidence chain regenerated in order: provenance/cutoff records -> bound
FMX report (FMX_PASS, 6,900x19, 0 rejects) -> role manifests -> runner
package + wrapper -> descriptive receipt (`CANDIDATE_ONLY` — posture
gate intact) -> replay report (REPLAY_OK, `full_recomputation`).

The merged tree collects **3355** tests; the fresh canonical suite
records **3349 passed / 6 skipped / 0 failed / 57 warnings**.

Owner decisions 2026-09-19: seismic waveform acquisition **deferred**
(11 GiB free vs 10 GB cap + 8 GiB reserve; external-volume and
reduced-slice paths both remain open for a later amendment); governed-env
obspy admission **deferred** until bytes exist (isolated 1.5.1
qualification retained; `allow_steim_decoding` stays False).

Seismic: the `allow_steim_decoding` flag is now wired (G3-F1) — closed
by default with the honest decoder reason; governed-env qualification
still required before admission.  Owner-side gates unchanged:
two-reviewer review, adjudication, post-review regime execution,
seismic storage/bytes.  Protected paths untouched; all authority flags
false; seismic stays non-associable and is not a Nepal predictor.
