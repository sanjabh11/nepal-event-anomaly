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
  mission elements in §3 are outside its single-event frame — and
  never silently: this document is the explicit, dated record of that
  supersedure boundary.
- Any future change to declared mission scope requires a **new
  versioned overlay** (e.g., `MISSION_SCOPE_OVERLAY_V1.md`), not an
  edit to this file and not an edit to `preregistration.md`.

## 2. Binding digests (recorded 2026-09-16)

This overlay is **additive**: it records scope relative to the frozen
and attested artifacts below and does not modify, replace, or weaken
their bytes. Digests pin the exact versions this overlay speaks
about. The `preregistration.md` digest was recomputed from the live
file on 2026-09-16; the D1/D2 digests are copied verbatim from the
P3 attestation record (`P3_ATTESTATION_RECORD_V0.md`, fields
`matrix_sha256` / `policy_sha256`).

- Frozen preregistration — `preregistration.md` (repository root,
  frozen 2026-09-10, byte-immutable):
  `sha256 = 0e7ce3c2e347a955f7495d719bb9232465ac9c25a5266656865800dbc963da7c`
- D1 — `docs/science/HAZARD_EVENT_INVENTORY_DECISION_MATRIX_V0.md`
  (P3-attested `matrix_sha256`):
  `sha256 = 5848668ea01bdab4d2e29554b6cb7bc666a30db46aca2b9f78e40adec76f6d04`
- D2 — `docs/science/INFORMATION_CUTOFF_TARGET_POLICY_V0.md`
  (P3-attested `policy_sha256`):
  `sha256 = df856eb1e4e2e264e725d662dfbe9862660e965a8ac8c459f99c5d910dc1861e`

What this overlay binds (scope coverage only — see §3; it never
touches the frozen bytes):

- the four hazard verticals: `snow_avalanche`, `glof`, landslide
  (`landslide_rainfall`, `landslide_coseismic`), and
  `ice_rock_avalanche` — with `dam_breach_engineered` remaining
  `DEFERRED_NO_OPEN_TIMED_SOURCE`;
- multi-region regime discovery (descriptive regime modes) under
  `run_b/REGIME_PROTOCOL_V0.md` — gated; not run on real data;
- held-out event–regime association under
  `run_c/ASSOCIATION_PROTOCOL_V0.md` — gated; not run on real data;
- archived forecast/reforecast evaluation scope under
  `run_c/FORECAST_EVAL_SCAFFOLD_V0.md` and
  `run_c/FORECAST_ARCHIVE_MATRIX_V0.md` — gated; not run on real
  data.

## 3. Declared mission scope (v0 design surface)

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

## 4. Current posture (restated)

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

## 5. Evidence class of synthetic fixtures

All synthetic fixtures under `tests/fixtures/` and all synthetic
payloads named in the run_b/run_c protocols are **contract-only
evidence**: they exercise schema, gate, adapter, and evaluation
behavior. They are not intake, not observations, and no scientific
finding may be drawn from them.

## 6. Non-authorization clause

No part of this overlay may be read as:

- pilot selection or pilot-gate passage for any vertical;
- forecast, prediction, or forecast-skill authorization;
- warning-path or alerting authorization of any kind;
- production or deployment authorization;
- competent-authority approval.
