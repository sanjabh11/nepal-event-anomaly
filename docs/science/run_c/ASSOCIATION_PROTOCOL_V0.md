# Event–Regime Association Protocol — v0

**Status:** `DESIGN_DRAFT_COMPLETE` — pending human approval.
**Scope:** research only. This protocol specifies how a *frozen*
unsupervised regime artifact may be tested for association with held-out
hazard events. It authorizes no fitting, no label access, no intake, and
no claim. All language in association outputs is associational — causal
or predictive wording is rejected by the claim scan.

**Binding contracts:** `nepal/research_v0/records.py`
(`RegimeArtifactV0`, `HoldoutPlanV0`, `EventLabelV0`,
`ObservationOpportunityV0`, `ControlWindowV0`, `EvidenceArtifactV0`),
`nepal/research_v0/policy.py` (`RegimeMode`, `EventTimeClass`,
`TargetState`, `OPPORTUNITY_STATES`, embargo and horizon rules), and
`docs/science/INFORMATION_CUTOFF_TARGET_POLICY_V0.md` §§4–6, 9–10.

## 1. What this protocol is and is not

- It is a **predeclared testing protocol** for the question: *does a
  label-blind, frozen regime partition carry non-random association with
  independently adjudicated events on held-out basins?*
- It is **not** a forecast experiment. Regime–event association, even
  when supported, is evidence about the unsupervised path, not forecast
  skill. Forecast evaluation lives in
  `FORECAST_EVAL_SCAFFOLD_V0.md` and requires
  `REFORECAST`/`ARCHIVED_OPERATIONAL` vintages — regimes fit on
  `REANALYSIS` never cross that boundary.
- It is **not** authorization. `PILOT_GATE_PASSED`, warning, production,
  and authority statuses are structurally absent from the neutral
  status vocabulary and remain so here.

## 2. Precondition gates (fail-closed, in order)

Every gate must pass before any association statistic is computed. A
failed gate terminates the protocol with the shown status and no
association numbers may be reported.

| # | Gate | Contract | Fail status |
|---|---|---|---|
| P0 | Regime artifact exists and `problems()` is empty | `RegimeArtifactV0` | protocol cannot start |
| P1 | `data_class == "REANALYSIS"` and `mode == "RETROSPECTIVE_REGIME"` | `RegimeArtifactV0` fields | restart in correct lane |
| P2 | `fitted_on == "TRAIN_ONLY"`; `label_blinding == True`; ≥3 distinct seeds; `k >= 1` with bound `k_selection_digest`, `preprocessing_digest`, `stability_report_digest`, `null_model_digest`, non-empty `source_digests` | `RegimeArtifactV0` | protocol cannot start |
| P3 | Stability evidence adequate across seeds, temporal blocks, and basin/region subsets (stability report verdict) | stability artifact | `UNSUPERVISED_STRUCTURE_NOT_STABLE` |
| P4 | Holdout plan valid: basin/catchment assignment made before eligibility filtering; complete event→group mapping; non-empty train/validation/test groups; ≥2 named evaluation regions drawn from locked test groups; finite `embargo_seconds` | `HoldoutPlanV0` | protocol cannot start |
| P5 | Labels bound and validated: unique `event_id`, ordered explicit-UTC interval, `uncertainty_seconds` covering the whole bracket, declared `event_time_basis`, dedup/`parent_event_id`/`cascade_group_id` lineage resolvable | `EventLabelV0` | protocol cannot start |
| P6 | **Freeze order:** the regime artifact, all digests, and the holdout plan are committed *before* any event label is opened for association analysis. Any label exposure before freeze → restart. | this protocol | restart |
| P7 | Label-open scope: enrichment/transition tests run **only on locked test (held-out) basins**. Train-basin label associations are descriptive-only diagnostics, never reported as evidence. | this protocol | descriptive downgrade |

## 3. Association universe construction

1. **Units and windows.** The evaluation frame is built from
   `ObservationOpportunityV0` windows on held-out basins only.
   `NEGATIVE` requires `OBSERVED_FULL`; `OBSERVED_PARTIAL`,
   `UNOBSERVED`, `UNKNOWN` windows are `CENSORED_OR_AMBIGUOUS`, never
   negatives.
2. **Events.** Only labels whose `adjudication_state ∈
   {TWO_REVIEW_AGREE, THIRD_PARTY_ADJUDICATED}` may enter positive
   cells; `UNADJUDICATED`/`DISAGREEMENT_RETAINED` labels censor the
   overlapping window (dominant-ambiguity rule, policy B13).
3. **Atomicity.** `cascade_group_id`, `parent_event_id`, and
   `duplicate_of` clusters are atomic units: they count once and are
   never split across statistical resamples.
4. **Matching.** Controls are matched on basin/forecast unit, season,
   platform, data availability, and coverage quality — constructed
   before outcome inspection. Future event labels may not select the
   control frame.
5. **Horizon binding.** Pre-event regime windows may look back only
   within horizons admissible under the event-time class allowlist
   (`EXACT_DAY` → 48h+; `INTERVAL_LE_7D` → 7d+; `INTERVAL_8_30D` → 30d;
   `COARSE_OR_UNRESOLVED` → descriptive only).

## 4. Enrichment tests (predeclared)

For each regime `r` and each admissible horizon `h`:

- **Statistic:** enrichment ratio `E(r,h) = P(event-window regime = r)
  / P(control-window regime = r)` on held-out basins, with a two-sided
  test against 1.0.
- **Nulls:** (a) season-matched shuffled labels (permutation preserving
  month-of-year distribution); (b) geography-preserving spatial shift
  null (regime assignment sampled under shifted event centroids within
  the same basin group).
- **Uncertainty:** event-group block bootstrap — resample
  cascade/event-season groups (not individual windows); report
  percentile intervals.
- **Multiplicity:** the tested regime×horizon set is predeclared;
  Holm or Benjamini–Hochberg correction across the family is recorded
  in the evaluation report. Any post-hoc regime selection is labelled
  exploratory and excluded from the supported verdict.

## 5. Transition tests (predeclared)

- For `EXACT_DAY`/`INTERVAL_LE_7D` events, compare the pre-event regime
  **transition path** (regime sequence over the admissible look-back
  window) against matched controls: transition-count enrichment and
  dwell-time distributions, same event-group bootstrap.
- Transitions are descriptive trajectory statistics — a regime that
  precedes events more often than controls is *associated*, not a
  predictor.

## 6. Sensitivity analyses (mandatory, predeclared)

| Sensitivity | Procedure | Reporting |
|---|---|---|
| Interval uncertainty | Recompute enrichment under (a) uniform placement of event time inside its uncertainty bracket, (b) adversarial worst-case placement, (c) midpoint. Report the range. | range per regime×horizon |
| Label precision class | Recompute restricted to `EXACT_DAY` and better labels. | subset result |
| Observation effort | Recompute under downweighted low-coverage periods and effort-covariate adjustment. | sensitivity result |
| Era drift | Split at preregistered era boundary; recompute per era (B33). | per-era result |
| Feature subset | Recompute on preregistered feature-subset regime refits *within the frozen seed/K protocol* — no new K search. | robustness result |
| Missingness | Recompute under declared missingness policy variants. | sensitivity result |

Any sensitivity that reverses the sign or collapses the interval of the
primary enrichment is disclosed in the verdict — cherry-picked subsets
are prohibited.

## 7. Negative controls

- **Placebo horizons:** admissible-horizon look-backs applied at
  windows where no event could have occurred (non-event, fully
  observed); enrichment there must be ~1.0.
- **Impossible-regime control:** association of a frozen regime with a
  structurally different hazard vertical's labels must be ~1.0.
- **Time-reversed null:** post-event windows analyzed as if pre-event;
  any symmetric enrichment flags look-ahead contamination.

## 8. Language discipline

Required: "is associated with", "enriched under", "co-occurs with",
"non-random correspondence on held-out basins".
Prohibited: "causes", "triggers", "predicts", "forecasts",
"preconditions", "enables warning", "operational signal", and all
claim-scan operational phrases. Association outputs carry
`claim_scope=research_only_no_operational_authorization`.

## 9. Decision rule

```
P3 fails                       → UNSUPERVISED_STRUCTURE_NOT_STABLE
P0–P2, P4–P7 fail              → protocol restart (no numbers)
association computed,
 effective sample underpowered → UNDERPOWERED_DESCRIPTIVE_ONLY
no corrected regime×horizon
 cell survives on held-out
 basins vs both nulls          → UNSUPERVISED_PATH_NOT_SUPPORTED
≥1 cell survives all nulls,
 sensitivities, and negative
 controls                      → REGIME_ASSOCIATION_SUPPORTED
 otherwise (stable but
 untested or descriptive
 scope)                        → DESCRIPTIVE_REGIME_ONLY
```

`REGIME_ASSOCIATION_SUPPORTED` means only: *a frozen label-blind
regime partition showed non-random association with adjudicated events
on held-out basins under predeclared tests.* It licenses no forecast,
warning, or operational inference.

## 10. Record and artifact bindings

| Record / artifact | Role |
|---|---|
| `RegimeArtifactV0` | frozen regime; all digests bound |
| `HoldoutPlanV0` | basin groups, embargo, evaluation regions |
| `EventLabelV0` | adjudicated event intervals + lineage |
| `ObservationOpportunityV0` | observation opportunity per window |
| `ControlWindowV0` | derived-state controls (never caller-asserted) |
| `EvidenceArtifactV0` | `regime_model`, `stability_report`, `k_selection`, `preprocessing`, `null_model`, `evaluation_report` |
| Envelope status | `STATUS_REQUIRED_RECORDS` graph: `REGIME_ASSOCIATION_SUPPORTED` requires regime + holdout + labels + evidence artifacts |

## 11. Testing surface

Protocol conformance is exercised only on **synthetic fixtures** —
generated labels, opportunities, and regime assignments with known
planted association and planted leakage — validating gate ordering,
dominant-ambiguity censoring, bootstrap atomicity, and decision-rule
transitions. No real data is touched; fixture results are contract-layer
evidence, not scientific results.
