# Nepal Event Anomaly Assessment

Retrospective hindcast of whether ERA5-Land meteorological data showed
unusual pre-event thermal conditions before the 26 August 2026
Langtang Lirung ice-rock avalanche.

## Status: RESEARCH ONLY

- No use in any warning context
- No prediction claim
- No production deployment
- No causal attribution

## Pre-Registration

See `preregistration.md` (FROZEN — do not modify after data inspection)

## Quick Start

> **Legacy / non-pilot paths.** The commands below are the frozen
> single-event hindcast surface. They are not pilot paths for any future
> forecast experiment: multi-hazard science work is governed by the v0
> design artifacts in `docs/science/` and the import-isolated
> `nepal/research_v0` namespace, which performs no downloads and runs no
> pipeline before the P3 design-approval gate. `era5_download.py` and
> `run_nepal_test.py` remain quarantined legacy surfaces — do not treat
> their outputs as forecast or operational evidence.

```bash
# Historical reference only — these legacy commands are intentionally
# commented out and must not be run as part of any v0 research path:
#   source .venv/bin/activate
#   python nepal/feature_contract.py   # contract check (frozen surface)
#   python nepal/era5_download.py      # ERA5-Land fetch — LEGACY, non-pilot
#   python nepal/run_nepal_test.py     # Phase-5 runner — LEGACY, non-pilot
```

### P5 confirmation-only diagnostic path

The three scripts below form one bounded, research-only diagnostic
chain — they are NOT legacy quarantined surfaces, but they are NOT a
pilot, forecast, or operational path either. They produce a
single-cell descriptive regime check on retrospective ERA5-Land data
only. Execution required explicit network authorization (P5-C gate);
**Run A executed under it on 2026-09-15** as a hybrid-route
acquisition (ARCO timeseries + EarthDataHub DestinE mirror +
CDS/MARS — see `docs/science/run_a/HYBRID_ROUTE_RECONCILIATION_V0.md`
for the authorized-vs-actual record and open disposition) followed by
feature extraction and the descriptive GMM. The completed result is
`EXPLORATORY_DESCRIPTIVE_SINGLE_CELL` — implementation-confirmation
scope only; see `docs/science/run_a/RUN_A_EVIDENCE_SUMMARY_V0.md`.

```bash
RUN_ROOT=research_runs/gmm_confirmation_$(date +%Y%m%d)

# 1. Bounded acquisition — 78 monthly ERA5-Land JJA requests,
#    2026-08 capped at day 25, fails closed on any incomplete or
#    post-cutoff payload. Requires cdsapi credentials.
#    (Run A instead used nepal/era5_hybrid_fetch.py — arco / sd-sf /
#    assemble subcommands plus an EarthDataHub mirror file; the
#    canonical pure-route command is kept here for reference.)
.venv/bin/python nepal/era5_download.py --run-root "$RUN_ROOT" \
    --year-range 2001-2026

# 2. Feature extraction — reads merged/, writes features/ under the
#    same run root. No post-event rows; edge-censored PDD rows flagged.
.venv/bin/python nepal/feature_extraction.py --run-root "$RUN_ROOT" \
    --era5-file "$RUN_ROOT/merged/era5_land_nepal_jja_2001_2026.nc"

# 3. Descriptive GMM — reads features/, writes gmm/ under the same
#    run root. Status: EXPLORATORY_DESCRIPTIVE_SINGLE_CELL. K=1 null
#    included; K=1..5; 3 seeds; converged-only BIC; no event labels.
.venv/bin/python nepal/gmm_descriptive.py --run-root "$RUN_ROOT"
```

All outputs land under `research_runs/` (gitignored) — never under
`data/`, `pinned/`, `framework_v1/`, or `preregistration.md`.

## Science design (v0)

See `docs/science/` for the hazard/event-inventory decision matrix, the
information-cutoff and target policy, source feasibility records, and
the gap register. Research-only code lives in `nepal/research_v0/`
(validation CLI: `.venv/bin/python -m nepal.research_v0.cli --help`).
No hazard vertical is pre-selected; the current pilot outcome is
`NO_QUALIFYING_PILOT_SOURCE` pending the evidence items in
`docs/science/SOURCE_FEASIBILITY_RECORDS_V0.md`.

Run status:

- **Run A** — completed bounded diagnostic (single cell, hybrid
  route): an authorized single-cell descriptive GMM implementation
  confirmation, method-only. `docs/science/run_a/` holds the evidence
  summary, route reconciliation, and scope overlay. Result status
  `EXPLORATORY_DESCRIPTIVE_SINGLE_CELL`; descriptive reanalysis only.
  Multi-region regime fitting remains gated under Run B and has not
  run on real data.
- **Runs B and C** — `SPECIFICATION_COMPLETE` with real data pending:
  `docs/science/run_b/` and `docs/science/run_c/` contain protocol
  documents and synthetic fixtures only — no intake, fitting, or
  evaluation on real data has run under them.

Test-suite honesty: the full suite under the pinned `.venv` recorded
1282 passed / 5 skipped / 0 failed at the time of
`P0_BASELINE_LEDGER.md` (historical snapshot). Later dated snapshots:
1306 at the round-2 reconciliation head, 1641 after the first swarm
integration, 1828 at the post-audit residual-repair head. Last
fully-verified baseline (round-11.7 seismic-sidecar content
commit; the manifest's `content_head` names the commit whose
tree the manifest hashes — the manifest commit itself follows
the content commit): **3203 passed / 6 skipped / 0 failed /
57 warnings** across **107** manifest-governed files (3,209
collected) — bound to the round-11.7 head, which landed the
hardened seismic sidecar (derived observability gates,
byte-pinned waveform reads, canonical window identities,
explicit station holdout, the SEISMIC_WAVEFORM_RETROSPECTIVE
class bound to the retrospective lane only) on top of the
round-11.5.1 `manifest_commit == content_head` CLI invariant.
Round-11 added the audit-pinned GLOF intake and descriptive-runner
surface (`nepal/research_v0/source_intake.py`,
`nepal/science_v0/glof_poc.py`,
`docs/science/run_b/GLOF_POC_CONTRACT_V0.md`, and the
`tests/test_glof_poc_contract.py` contract suite) on top of the
frozen round-10 floor — the prior verified baseline (round-10
hardening):
**2757 passed / 5 skipped / 0 failed / 57 warnings** across
84 files. The round-10 surface closes the sixteen findings a
Round-10 independent audit raised against the round-9 floor:
bounded numeric parsing and an exception-safe floor (no malformed value crashes a boundary — verified by focused regressions),
complete serialized-config semantics, exact field schemas across
the whole envelope, the status×terminal×associable state machine,
complete null-family validation, non-coercing artifact
construction with exact unit→basin coverage, component-walk
symlink policy, and typed byte-bound `forecast_vintages` for
associable forecast artifacts; `tests/test_r10_promotion.py`
carries the 520-test mutation matrix (forged-artifact association
evidence at every boundary), and the root `conftest.py` pins
canonical collection to `tests/` so the ignored external data
symlink can never be collected. The prior verified baseline
(round-9 hardening): **2252 passed / 5 skipped / 0 failed** across
82 manifest files; the round-8 baseline:
**2073 passed / 5 skipped / 0 failed / 57 warnings**;
before that, round-7 content commit `cc2218b` (77 manifest files):
**2015 passed / 5 skipped / 0 failed / 57 warnings**, with
562 focused-lane tests green — retained as dated history, as is
round-6 (content `aceedff`, 76 files, 1993/5/0/57, 540 focused).
The 5 skips are `rasterio`-dependent
optional-dependency gates in frozen-package tests — disclosed, not
waived — and warnings are not treated as failures.

## Structure

```
pinned/          # Reusable code from avalanche-insight-hub-t2a
nepal/           # Nepal-specific code
config/          # AOI and configuration
data/            # Downloaded data (gitignored)
plots/           # Output plots
preregistration.md  # Frozen pre-registration
```

## Key Limitations

- ERA5-Land model elevation (4,322 m) is ~899 m below source (5,221 m)
- Grid resolution is ~11.1 × 9.8 km, not slope-scale
- ERA5-Land preliminary product has ~5-day latency
- Single event cannot support prediction claims
- GMM is descriptive only, not a detector

R11.8 seismic contract and provenance completion (2026-09-19)

The R11.8 changes close the remaining seismic contract-validation seams and
bind the separate byte/event/provenance path without changing the hardened
GLOF path. Strict configuration booleans and role/column uniqueness, bounded
malformed-input handling, UTC calendar/date binding, and declared
window-duration checks are enforced before digests or fitting. The added
waveform I/O, event/opportunity package, and feature-generation provenance
modules remain retrospective, research-only, and non-associable.

The merged tree collects **3273** tests. The fresh canonical suite is
**3267 passed / 6 skipped / 0 failed / 57 warnings**. The seismic/addendum
lane is **208 passed**, and the shared GLOF/regime regression lane is
**904 passed / 1 warning**. Five rasterio quarantines and one documented
lane skip remain disclosed. No seismic payload bytes were acquired, and
warning, production, and promotion authority remain false.

### Post-acquisition status (2026-09-19, R11.9 historical snapshot)

P5 owner-authorized acquisition landed: HMAGLOFDB v1.3.0 events, ICIMOD
PDGL 2015 opportunity frame, RDS7952 basin boundaries, and dual-channel
ERA5-Land (CDS + GEE) over the three operative basins — all byte-bound
under `retrieval/role_manifests_v0.json` (event / opportunity / feature /
sidecar, distinct source IDs). The R11.9 text below is retained as the
historical pre-temporal-amendment snapshot; the current P5-A2 state is
recorded immediately after it.

The FMX audit now runs only through the verified feature-role manifest
(`nepal/real_fmx.py`): frame bytes are re-checked before parsing, the
report binds the role digest, frame digest, row-universe digest, and
semantic-matrix digest; cutoff is a persisted `CUTOFF_RECORD_V0` bound
to the retrieval record's completion time; preprocessing provenance is a
persisted `PREPROCESSING_PROVENANCE_V0` record recomputed from live bytes
(4,600 train rows proven, not asserted). Replay is full recomputation —
`scripts/replay_p5.py` rebuilds the FMX report, package digests, ledger
fields, semantic role bindings, and receipt digest; `REPLAY_OK` requires
reproduction, not just sidecar integrity. The seismic STEIM admission
gate (`allow_steim_decoding`) is a wired strict-bool flag — closed by
default pending governed-env decoder qualification.

Remaining gates in that historical snapshot were owner-side or physical:
two-reviewer intake + adjudication, post-review regime execution, seismic storage
(~11 GiB free vs 10 GiB cap + 8 GiB reserve) and waveform acquisition.
`DESIGN_DRAFT_COMPLETE`; seismic remains retrospective-only and
non-associable; seismic is **not** a Nepal predictor.

The merged tree now collects **3355** tests; the fresh canonical suite
is **3349 passed / 6 skipped / 0 failed / 57 warnings**.

### P5-A2 temporal amendment (current state, 2026-09-20)

The owner-authenticated amendment separates source geography from hydrology:
Melamchi remains the raw river value, Sindhupalchok and Bagmati Province
remain administrative fields, and the derived hydrological mapping is
`hydro_subbasin=Indrawati`, `basin_group=koshi`, `basin_id=koshi`.
The regime axis is temporal: JJA 2001–2017 train, JJA 2018–2019 embargo,
and JJA 2020–2025 holdout, with all three feature basins in the fit and no
geographic-transfer claim. Event labels use the explicit
`evaluation_only` waiver and never enter regime fitting.

The real run is complete and honest: 4,692 train rows, 552 embargo rows,
and 1,656 holdout rows were evaluated; the receipt is `CANDIDATE_ONLY`
because the artifact verdict is `UNSUPERVISED_STRUCTURE_NOT_STABLE`.
Replay is `REPLAY_OK` with `artifact_integrity_replay` — the persisted v1
artifact's envelope/freeze digests recompute and the v1 receipt binds
those bytes (the v0 receipt is preserved as historical: it bound a
digest whose artifact bytes were never persisted; amendment v5 records
the lineage repair). Promotion, warning, and production authority remain
false. A fourth basin is dormant and is not required for this temporal path.

The current canonical suite records the post-seasonal-v1 totals
recorded in `docs/science/ARTIFACT_MANIFEST_V0.json` (historical
snapshots: 3387 passed / 3393 collected at P5-A2; 3417+1 collection-
drift failure / 3424 collected at the v0 seasonal rebind). Five
rasterio skips and one documented descriptive-artifact skip remain
disclosed, plus ~56 upstream xarray/netCDF4 deprecation warnings.
Seismic waveform bytes and STEIM admission remain a separate
storage-gated track.

### P5 seasonal lane (amendment v3, executed 2026-09-20)

A different estimand from the daily result — never a retry or rescue.
Basin-year JJA seasonal types over 75 rows (3 basins × 25 seasons,
51 train / 6 embargo / 18 holdout) on the locked six-feature contract
(`t2m_mean, d2m_mean, pdd_sum, tp_q95, wet_spell_max_days, sd_delta`),
tied covariance, K≤4, a seasonal-only parameter-count guard, and
diagnostic (non-binding) LORO declared in config because 3-basin folds
are structurally infeasible at n=51. Verdict:
`UNSUPERVISED_STRUCTURE_NOT_STABLE` — modal K non-unanimous across
seeds (3/3/4), season-matched null p=0.66, shuffled-null envelope
incomplete. A second honest negative. The declared negative-control arm
was refused before any model fitting. Independent replay:
`scripts/replay_seasonal_p5.py` → `REPLAY_OK` (deterministic frame
rebuild, envelope + freeze digests, producer floor, receipt bindings).

The current evidence root is `p5-seasonal-v1-2026-09-20` (the v0 root
`p5-seasonal-jja-2026-09-20` is immutable and superseded *semantics
only* — identical scientific outcome). Post-audit v1 semantics:
`stability.gate_observations` separates each gate's observed status
(`PASS`/`FAIL`/`SKIPPED`/`NOT_APPLICABLE`) from whether it bound the
terminal status — diagnostic LORO serializes `SKIPPED`/non-binding
rather than a bare `true`, and `NOT_APPLICABLE` axes are no longer
indistinguishable from executed passes. `year_block` is a
metadata/resampling carrier — no year-block stability predicate is
executed. The seasonal adapter refuses contaminated inputs closed
(non-JJA labels, out-of-window dates, undeclared units/years,
duplicate or non-consecutive coverage) while honest missingness stays
ledgered. Arm A verifies the full daily reference chain (receipt
bytes, **live artifact bytes** — envelope + freeze digests recompute,
producer floor clean — terminal status, all-false authority) before
binding; the lane receipt carries `terminal_reason`,
`failed_gates`, and an explicit all-false authority block.

**P5-A2 artifact lineage repair (amendment v5).** The original daily
run bound only the artifact *digest* into its v0 receipt — the
artifact bytes were never persisted. The v0 receipt is preserved as
historical; `scripts/run_daily_p5.py` re-executed the frozen protocol
under a declared config to serialize
`retrieval/p5_glof_regime_artifact_v1.json` and issue a lineage-bound
`p5_glof_descriptive_receipt_v1.json`. The verdict reproduced:
`UNSUPERVISED_STRUCTURE_NOT_STABLE`. Daily replay emits
`artifact_integrity_replay` only after validating the persisted artifact —
a receipt-bound digest alone is never an independent model replay.

Option 3 (seismic sidecar) completed desk preflight and is **BLOCKED**:
no cataloged event inside the authorized 2023-04-01→05-09 waveform
window, no waveform bytes, storage marginal — recorded in
`retrieval/p5_d_preflight_reconciliation_v0.json` with formal
disposition in `retrieval/p5_d_owner_disposition_v1.json`; no retrieval
was performed. A versioned one-station receipt contract
(`nepal/seismic_sidecar/one_station_contract.py`) was drafted under the
preflight amendment as a synthetic design surface only — it authorizes
nothing and performs no I/O. Post-audit hardening: ≤24h window bound,
`BLOCKED`/`RUN_ERROR`/`NOT_OPERATIONAL` bind no byte evidence, strict
event anchors (source_id, event_utc, timing tolerance, relation,
source digest), required execution-digest chain for scientific
statuses, bounded malformed-input errors. Seismic remains
retrospective-only and is **not** a Nepal predictor.

Cross-root provenance: `retrieval/p5_evidence_index_v4.json` is the
exhaustive release index: it binds every non-sidecar payload in the daily,
seismic logical, seasonal-v0, and seasonal-v1 surfaces, requires a sidecar
for each payload, and records explicit exclusions — including typed
`planned` slots for outputs published after index generation
(`CLOSURE_PENDING` pre-publication semantics). The detached release
closure (`retrieval/p5_release_closure_v4.json`) is published into its
declared slot by `scripts/build_p5_release_closure_v4.py` and verified
post-publication by `scripts/validate_release_closure.py`; suite counts
are bound to a machine-generated `p5_suite_receipt_v1.json`, never
free-form. Prior indexes (v1 listed-set, v2/v3 exhaustive) remain as
superseded historical records. Both replay reports are
`artifact_integrity_replay` unless an independent model refit
is actually performed. Canonical writers refuse existing evidence paths
and replay defaults to stdout, so reruns cannot replace history.

Historical cross-root provenance: `retrieval/p5_evidence_index_v1.json` binds the
three physical roots (daily, seasonal v0 immutable, seasonal v1) plus
the seismic preflight as a logical surface under the daily root —
logical `root_id`s, relative paths, per-file digests + sidecars, and
provenance fields, machine-verified fail-closed by
`scripts/validate_evidence_index.py`. The superseded v0/v2/v3 indexes are
preserved immutably. The current P5 state is indexed in
`docs/science/P5_EXTENSION_STATUS_V0.md`; post-seasonal audit
dispositions are recorded in `docs/science/GAP_REGISTER_V0.md`.
