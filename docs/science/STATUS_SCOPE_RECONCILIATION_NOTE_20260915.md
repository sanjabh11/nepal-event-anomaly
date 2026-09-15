# Status & Scope Reconciliation Note — 2026-09-15

**Status:** `DOCUMENTATION` — dated reconciliation note; changes no
record, no code, and no frozen bytes.
**Lane:** docs wording reconciliation (worker C4).
**Date:** 2026-09-15.

## 1. P3 status overlay

`P3_ATTESTATION_RECORD_V0.md` records `ATTESTED —
design_review_only`, `human_approved=true`, `approved_at=2026-09-14`,
owner-directed. That attestation is **design-only**: it covers review
of the two hash-bound design documents (D1
`HAZARD_EVENT_INVENTORY_DECISION_MATRIX_V0.md`, D2
`INFORMATION_CUTOFF_TARGET_POLICY_V0.md`) and authorizes no data
intake, no forecast execution, no warnings, and no production or
authority action.

Documents whose text still carries a pre-attestation `P3_PENDING` /
"pending human approval" claim:

| Document | Disposition |
|---|---|
| `P3_ATTESTATION_TEMPLATE.md` | `P3_PENDING` is the blank-template state; annotated in place 2026-09-15 as superseded-by-design-attestation for the bound D1/D2 pair |
| D1 (`HAZARD_EVENT_INVENTORY_DECISION_MATRIX_V0.md`) status line | frozen bytes — NOT edited; superseded for the design-review scope by the 2026-09-14 record |
| D2 (`INFORMATION_CUTOFF_TARGET_POLICY_V0.md`) status line | frozen bytes — NOT edited; superseded for the design-review scope by the 2026-09-14 record |
| `P0_BASELINE_LEDGER.md` §"Owner designations" | pre-attestation designation-as-intent text; integrator-owned — flagged for post-merge annotation |
| `GAP_REGISTER_V0.md` rows citing still-required human P3 approval (e.g., G02, "P3 human approval is still required") | superseded for the design-review scope as of 2026-09-14; integrator-owned — flagged for post-merge annotation |

Run-level design documents not bound by the P3 record
(`run_c/FORECAST_EVAL_SCAFFOLD_V0.md`,
`run_c/ASSOCIATION_PROTOCOL_V0.md`) keep their own
`DESIGN_DRAFT_COMPLETE — pending human approval` status: the
2026-09-14 attestation binds only the D1/D2 digests, so that wording
remains accurate for those documents.

## 2. P3 vs P5-C scope separation

`P3_ATTESTATION_RECORD_V0.md` holds two distinct authorizations
co-located in one file, both owner-directed on 2026-09-14:

- **P3 — design-only attestation** (`design_review_only`): review of
  D1/D2 by sha256; authorizes no data intake, no forecast execution,
  no warnings, and no production or authority action.
- **P5-C — bounded acquisition authorization**: exactly 78 monthly
  ERA5-Land JJA requests (2001–2025 full JJA + 2026 through
  August 25 only), the seven pre-registered ERA5-Land variables, a
  unique timestamped `research_runs/` run root, ≥8 GiB disk floor,
  purpose `EXPLORATORY_DESCRIPTIVE_SINGLE_CELL`.

The co-location is editorial. P3 does not grant acquisition; P5-C
does not confer design approval. The record file is hash-bound
(`ARTIFACT_MANIFEST_V0.json`), so this separation is documented here
rather than by rewriting record bytes. See also
`run_a/SCOPE_OVERLAY_V0.md` §§1–2.

## 3. Run A posture cross-reference

- Route: hybrid ARCO (`reanalysis-era5-land-timeseries`) +
  EarthDataHub DestinE mirror + CDS-MARS (`reanalysis-era5-land`),
  assembled into the 78-month JJA set.
- Standing: method-only implementation-confirmation pending owner
  ratification (`run_a/HYBRID_ROUTE_RECONCILIATION_V0.md` §2, §5).
- Equivalence evidence: bounded-overlap only — 5 of 78 months; the
  remaining months are UNVERIFIED and no route-equivalence claim
  stands.
- `tp`/`sf` precipitation semantics differ across routes (verified
  finding, `run_a/HYBRID_ROUTE_RECONCILIATION_V0.md` §4).
- No transfer of K/JS/occupancy to multi-region science
  (`run_b/REGIME_PROTOCOL_V0.md`;
  `run_a/RUN_A_EVIDENCE_SUMMARY_V0.md` §6).
