# Nepal Event Anomaly Assessment

Retrospective hindcast of whether ERA5-Land meteorological data showed
unusual pre-event thermal conditions before the 26 August 2026
Langtang Lirung ice-rock avalanche.

## Status: RESEARCH ONLY

- No operational warning
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

## Science design (v0)

See `docs/science/` for the hazard/event-inventory decision matrix, the
information-cutoff and target policy, source feasibility records, and
the gap register. Research-only code lives in `nepal/research_v0/`
(validation CLI: `python -m nepal.research_v0.cli --help`). No hazard
vertical is pre-selected; the current pilot outcome is
`NO_QUALIFYING_PILOT_SOURCE` pending the evidence items in
`docs/science/SOURCE_FEASIBILITY_RECORDS_V0.md`.

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
