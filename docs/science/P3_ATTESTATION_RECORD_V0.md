# P3 Attestation Record — v0

**Status: ATTESTED — design_review_only**

| Field | Value |
|---|---|
| Approver | repository owner (designated approver, recorded 2026-09-14) |
| Attestation date | 2026-09-14 |
| Matrix path | `docs/science/HAZARD_EVENT_INVENTORY_DECISION_MATRIX_V0.md` |
| `matrix_sha256` | `5848668ea01bdab4d2e29554b6cb7bc666a30db46aca2b9f78e40adec76f6d04` |
| Policy path | `docs/science/INFORMATION_CUTOFF_TARGET_POLICY_V0.md` |
| `policy_sha256` | `df856eb1e4e2e264e725d662dfbe9862660e965a8ac8c459f99c5d910dc1861e` |
| Scope | `design_review_only` |
| Pilot rule | `FIRST_PASSING_ALL_GATES_ELSE_NO_QUALIFYING` |
| `human_approved` | `true` (owner-directed; see channel below) |
| `approved_at` | `2026-09-14` |

## Hash verification

Both digests were recomputed from the live files on 2026-09-14 and
match the values bound in `P3_ATTESTATION_TEMPLATE.md` exactly.

## Authentication channel

Owner instruction delivered in-session ("proceed on my behalf and
continue"), acting on the previously recorded P3 designation in
`P0_BASELINE_LEDGER.md`. This is owner-directed attestation; no
independent third-party signature is claimed.

## Attestation string (verbatim `EXPECTED_ATTESTATION`)

> I reviewed only the design documents identified by their sha256
> digests; this approval authorizes no data intake, no forecast
> execution, no warnings, and no production or authority action.

## Scope limits (unchanged)

- This approval covers `design_review_only` — it authorizes the
  bounded P5 ERA5-Land diagnostic described below and nothing else.
- It does not select a pilot, qualify a source, or authorize forecast
  execution, warnings, production, or authority action.
- `NO_QUALIFYING_PILOT_SOURCE` stands until a vertical passes all
  matrix gates.

## P5-C bounded-acquisition authorization (owner-directed, same date)

- Scope: exactly 78 monthly ERA5-Land JJA requests, 2001–2025 full
  JJA + 2026 through August 25 only.
- Variables: the seven pre-registered ERA5-Land variables, with
  `snow_depth_water_equivalent` (sd), never `snow_depth`/`sde`.
- Run root: a unique timestamped directory under `research_runs/`
  (gitignored, non-frozen).
- Disk floor: abort below 8 GiB free.
- Purpose: retrospective descriptive regime check only —
  `EXPLORATORY_DESCRIPTIVE_SINGLE_CELL`. No event labels enter the
  fit; no forecast/warning/production/authority claim is emitted.
