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
  route): `docs/science/run_a/` holds the evidence summary, route
  reconciliation, and scope overlay. Result status
  `EXPLORATORY_DESCRIPTIVE_SINGLE_CELL`; descriptive reanalysis only.
- **Runs B and C** — `SPECIFICATION_COMPLETE` with real data pending:
  `docs/science/run_b/` and `docs/science/run_c/` contain protocol
  documents and synthetic fixtures only — no intake, fitting, or
  evaluation on real data has run under them.

Test-suite honesty: the full suite under the pinned `.venv` last
recorded 1282 passed / 5 skipped / 0 failed
(`P0_BASELINE_LEDGER.md`). The 5 skips are `rasterio`-dependent
optional-dependency gates in frozen-package tests — disclosed, not
waived — and the suite emits warnings that are not treated as
failures.

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
