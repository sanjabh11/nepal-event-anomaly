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
fully-verified baseline (round-11.1 provenance-repair content
commit; the manifest's `content_head` names the commit whose tree
the manifest hashes — the manifest commit itself follows the
content commit): **2904 passed / 5 skipped / 0 failed /
57 warnings** across **88** manifest-governed files — bound to the
round-11.1 head, which hardens the thin-PoC provenance chain
(verify-then-read byte intake, manifest-bound digests, runner
section revalidation, cross-object provenance) per the R11.1 audit.
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
