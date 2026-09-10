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

```bash
source .venv/bin/activate
python nepal/feature_contract.py  # verify contract
python nepal/era5_download.py     # download ERA5-Land (Phase 1)
python nepal/run_nepal_test.py    # end-to-end runner (Phase 5)
```

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
