# Scope Overlay — v0 (P3 / P5-C / source intake)

**Status:** `SCOPE_SEPARATION_RECORDED` — clarifies which gate covers
what. It changes no approval, no record, and no code.
**Lane:** Run A (`run-a/reconciliation`).

Three distinct approvals exist or are contemplated in this repository.
They are disjoint; none implies another.

## 1. P3 — design-review attestation (executed)

- Record: `docs/science/P3_ATTESTATION_RECORD_V0.md`,
  `design_review_only`, attested 2026-09-14, hash-bound to
  `HAZARD_EVENT_INVENTORY_DECISION_MATRIX_V0.md` (D1) and
  `INFORMATION_CUTOFF_TARGET_POLICY_V0.md` (D2).
- Covers: review of those two design documents only.
- Does not cover: any data intake, any forecast execution, any
  warnings, any production or authority action — the attestation
  string says so verbatim.
- Not modified by this lane.

## 2. P5-C — bounded retrospective diagnostic authorization (executed)

- Recorded in the same P3 record file (§"P5-C bounded-acquisition
  authorization") — a **distinct** bounded acquisition authorization
  co-located with, but not part of, the P3 `design_review_only`
  attestation scope. The co-location is editorial; the P3 design
  approval does not itself grant acquisition, and the P5-C
  authorization confers no design approval: exactly 78 monthly
  ERA5-Land JJA requests,
  2001–2025 full JJA + 2026 through August 25; the seven
  pre-registered variables; a timestamped `research_runs/` run root;
  ≥8 GiB disk floor; purpose `EXPLORATORY_DESCRIPTIVE_SINGLE_CELL`.
- Covers: one bounded acquisition of `REANALYSIS`-class data and one
  descriptive regime diagnostic on it — Run A
  (`gmm_confirmation_20260915T052240Z`), summarized in
  `RUN_A_EVIDENCE_SUMMARY_V0.md`.
- Executed via a hybrid retrieval route that deviated from the literal
  request plan; disposition options are recorded in
  `HYBRID_ROUTE_RECONCILIATION_V0.md` §2 and remain an owner decision.
- Does not cover: label intake, event-source qualification, forecast
  archives, regime–event association, multi-basin fitting, or any
  claim beyond a single-cell descriptive description.

## 3. Source qualification / intake — a separate, future gate

- Status: **not executed**. All sources in
  `SOURCE_FEASIBILITY_RECORDS_V0.md` remain `CANDIDATE_ONLY`;
  pilot outcome stands at `NO_QUALIFYING_PILOT_SOURCE`; the license
  and procurement items listed in `GAP_REGISTER_V0.md` remain
  `BLOCKED_EXTERNAL`.
- Would require: its own gate covering license terms, version/DOI
  binding, coverage, field semantics, timing precision, and per-source
  evidence sidecars (`EVIDENCE_VERIFIED` requires byte-bound sidecars
  — B07/C22). Run A's ERA5-Land intake does not qualify any event
  catalog, forecast archive, or other source.
- Runs B and C are `SPECIFICATION_COMPLETE` with real data pending:
  their governed outputs are protocol documents and synthetic
  fixtures, not executed science. Neither run has performed intake.

## 4. Explicit non-authorization clause

No approval, record, artifact, or commit in this repository may be
read as:

- pilot selection or pilot-gate passage for any vertical;
- forecast, prediction, or forecast-skill authorization;
- warning-path or alerting authorization of any kind;
- production or deployment authorization;
- competent-authority approval.

Every envelope keeps `warning_path_authorized=false`,
`production_authorized=false`, `promotion_eligible=false`
(D2 §11). A future warning pathway is a separate program (WMO
four-pillar framework + competent-authority approval) and is not
implied by anything here.
