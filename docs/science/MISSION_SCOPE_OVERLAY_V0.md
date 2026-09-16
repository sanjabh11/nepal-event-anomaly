# Mission Scope Overlay — v0 (mission vs frozen preregistration)

**Recorded:** 2026-09-16. **Lane:** Round-5 documentation residuals
(GOAL-01). This overlay records the declared program scope relative to
the frozen single-event preregistration. It changes no approval, no
record, and no code.

## 1. Binding to the frozen preregistration

- Bound file: `preregistration.md` (repository root), frozen
  2026-09-10 — a single-event retrospective thermal hindcast for the
  26 August 2026 Langtang Lirung ice-rock avalanche.
- This overlay does not modify, replace, or weaken that file. The
  frozen preregistration remains the **Run-A baseline**: the scope
  under which the P5-C bounded acquisition and the single-cell
  descriptive GMM diagnostic were authorized and executed.
- The frozen file is superseded **only for scope coverage** — the
  mission elements in §2 are outside its single-event frame — and
  never silently: this document is the explicit, dated record of that
  supersedure boundary.
- Any future change to declared mission scope requires a **new
  versioned overlay** (e.g., `MISSION_SCOPE_OVERLAY_V1.md`), not an
  edit to this file and not an edit to `preregistration.md`.

## 2. Declared mission scope (v0 design surface)

The design artifacts in `docs/science/` (decision matrix, cutoff and
target policy, source feasibility records, gap register, and the
run_b/run_c protocol documents) declare a multi-hazard, multi-region
research program broader than the frozen hindcast:

- **Hazard verticals:** snow/avalanche (`snow_avalanche`), GLOF
  (`glof`), landslide (`landslide_rainfall`,
  `landslide_coseismic`), and ice-rock (`ice_rock_avalanche`). The
  engineered dam-breach vertical (`dam_breach_engineered`) is
  explicitly `DEFERRED_NO_OPEN_TIMED_SOURCE` — no open structured
  Nepal inventory located; owner-accepted deferral.
- **Multi-region regime discovery** (Run B): descriptive regime
  fitting across declared regions under
  `run_b/REGIME_PROTOCOL_V0.md` — gated; it has not run on real data.
- **Held-out event–regime association** (Run B/C): association of
  held-out events with discovered regimes under
  `run_c/ASSOCIATION_PROTOCOL_V0.md` — gated; it has not run on real
  data.
- **Archived forecast/reforecast evaluation** (Run C): evaluation
  against archived forecast vintages under
  `run_c/FORECAST_EVAL_SCAFFOLD_V0.md` and
  `run_c/FORECAST_ARCHIVE_MATRIX_V0.md` — gated; it has not run on
  real data.

## 3. Current posture (restated)

- Program design state: `DESIGN_DRAFT_COMPLETE` — protocols and
  contract-layer code exist; execution is not implied by design
  completeness.
- P3 attestation: executed 2026-09-14 with scope
  `design_review_only` (see `P3_ATTESTATION_RECORD_V0.md`); it
  authorizes no acquisition, no intake, and no execution. P3 remains
  design-only.
- Pilot outcome: `NO_QUALIFYING_PILOT_SOURCE` — every candidate
  source remains `CANDIDATE_ONLY`; nothing in this overlay selects or
  qualifies a pilot.
- `warning_path_authorized=false` (WARNING_PATH_AUTHORIZED: NO),
  `production_authorized=false`, `promotion_eligible=false`
  everywhere.
- Run A: completed bounded single-cell descriptive GMM
  implementation confirmation (`EXPLORATORY_DESCRIPTIVE_SINGLE_CELL`,
  method-only). Runs B and C: `SPECIFICATION_COMPLETE` with real data
  pending.

## 4. Evidence class of synthetic fixtures

All synthetic fixtures under `tests/fixtures/` and all synthetic
payloads named in the run_b/run_c protocols are **contract-only
evidence**: they exercise schema, gate, adapter, and evaluation
behavior. They are not intake, not observations, and no scientific
finding may be drawn from them.

## 5. Non-authorization clause

No part of this overlay may be read as:

- pilot selection or pilot-gate passage for any vertical;
- forecast, prediction, or forecast-skill authorization;
- warning-path or alerting authorization of any kind;
- production or deployment authorization;
- competent-authority approval.
