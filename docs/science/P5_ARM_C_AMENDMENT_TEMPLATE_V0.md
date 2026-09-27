# P5 Arm C Amendment Template — v0

**Status: TEMPLATE_ONLY_NOT_AUTHORIZED**

This blank form is a planning scaffold. It is not an amendment, not an
approval, not a data-access request, and not evidence of an executed Arm C
lane. No CDS request, pressure-level download, frame build, model fit, or
release publication may begin from the existence of this file. The owner
must complete and separately approve the structured record before any
acquisition or execution.

## 1. Record identity

| Field | Owner completion |
|---|---|
| Record type | P5_ARM_C_AMENDMENT_V0 |
| Amendment id | TBD_OWNER |
| Supersedes | TBD_OWNER |
| Created UTC | TBD_OWNER |
| Scientific owner | TBD_OWNER |
| Execution coordinator | TBD_OWNER |
| Approval status | TBD_OWNER |
| Approved by | TBD_OWNER |
| Approval UTC | TBD_OWNER |
| Approval evidence reference | TBD_OWNER |

The approval fields remain literal TBD_OWNER values until the owner supplies
an authenticated decision. A completed form must be written as a new,
exclusive-create record; it must not overwrite a prior P5 amendment.

## 2. Scientific purpose and fixed boundaries

| Field | Owner completion |
|---|---|
| Estimand | Basin-year JJA seasons exhibit reproducible low-complexity types |
| Grain | 3 basins x 25 seasons = 75 seasonal rows, unless a new amendment states otherwise |
| Arm | Arm C: Arm B plus compact, prespecified pressure-level diagnostics |
| Role | RETROSPECTIVE_REGIME |
| Event labels | Evaluation-only; no label access before the declared freeze |
| Claim ceiling | DESCRIPTIVE_REGIME_ONLY |
| Authority flags | warning_path_authorized=false; production_authorized=false; promotion_eligible=false; operational_claim=false |
| Seismic relationship | None; seismic data and contracts remain isolated from this arm |

The daily and seasonal negative results remain immutable references. Arm C
may test whether the compact pressure-level feature contract adds descriptive
information; it may not be described as a rescue, forecast, warning,
detector, or operational capability.

## 3. Prespecified pressure-level feature contract

Every row below must be completed before acquisition. “TBD_OWNER” is not a
valid execution value.

| # | Diagnostic | Level(s) | Formula / units | Source variable(s) | Missingness rule |
|---:|---|---|---|---|---|
| 1 | TBD_OWNER | TBD_OWNER | TBD_OWNER | TBD_OWNER | TBD_OWNER |
| 2 | TBD_OWNER | TBD_OWNER | TBD_OWNER | TBD_OWNER | TBD_OWNER |
| 3 | TBD_OWNER | TBD_OWNER | TBD_OWNER | TBD_OWNER | TBD_OWNER |
| 4 | TBD_OWNER | TBD_OWNER | TBD_OWNER | TBD_OWNER | TBD_OWNER |
| 5 | TBD_OWNER | TBD_OWNER | TBD_OWNER | TBD_OWNER | TBD_OWNER |
| 6 | TBD_OWNER | TBD_OWNER | TBD_OWNER | TBD_OWNER | TBD_OWNER |

Required declarations:

- exact CDS product, dataset version, variables, levels, spatial subset, and
  time interval: TBD_OWNER;
- pressure-level diagnostics are compact and prespecified; raw-channel
  expansion is not permitted: TBD_OWNER;
- anomaly baseline, temporal aggregation, units, calendar, and timezone:
  TBD_OWNER;
- feature ordering, standardization, imputation, and finite-value policy:
  TBD_OWNER;
- correlation or condition-number threshold for collinearity:
  TBD_OWNER;
- action if the threshold is exceeded: TBD_OWNER;
- exact source and feature-manifest digests before fitting: TBD_OWNER.

## 4. Gate and null contract

The following values must be copied into the machine amendment record before
execution. They may not be inferred from a later run:

| Contract item | Declared value |
|---|---|
| covariance_type | tied or diagonal only; exact choice TBD_OWNER |
| k_candidates | 2, 3, 4 only; exact tuple TBD_OWNER |
| seeds | TBD_OWNER |
| temporal split | training / embargo / holdout values TBD_OWNER |
| bootstrap block length and gap policy | TBD_OWNER |
| shuffled-null replicates and completeness rule | TBD_OWNER |
| season-matched-null definition and alpha | TBD_OWNER |
| basin-year-aware resampling rule | TBD_OWNER |
| LORO policy | diagnostic unless a separately justified policy is approved |
| negative-control input and refusal expectation | TBD_OWNER |
| parameter-count guard at n=75 | enforced; exact bound TBD_OWNER |
| gate vocabulary | existing 13 required regime gates only |
| threshold changes | none; a change requires a new protocol version |

The negative-control arm must be refused before fitting and must also be
rejected by the statistical checks. A non-convergent null envelope fails
closed; it is not completed by interpolation or selective omission.

## 5. Acquisition and execution limits

| Field | Owner completion |
|---|---|
| CDS provider and access mode | TBD_OWNER |
| Dataset and version | TBD_OWNER |
| Exact request specification | TBD_OWNER |
| Authorized spatial and temporal bounds | TBD_OWNER |
| Storage budget and reserve | TBD_OWNER |
| Evidence root and exclusive-create path | TBD_OWNER |
| Dependency amendment, if any | TBD_OWNER |
| Reproducible environment digest | TBD_OWNER |
| Stop rules | TBD_OWNER |

No external request is authorized until Sections 1, 3, 4, and 5 are
complete and the owner has recorded approval. A failed or incomplete request
must produce a blocked or run-error record, not a partial scientific result.

## 6. Required outputs

The owner must bind each output path and schema before execution:

- source and retrieval receipt: TBD_OWNER;
- pressure-level feature manifest and frame digest: TBD_OWNER;
- Arm B reference verification: TBD_OWNER;
- Arm C artifact, configuration, and environment digests: TBD_OWNER;
- gate observations and terminal receipt: TBD_OWNER;
- negative-control refusal and statistical rejection evidence: TBD_OWNER;
- replay mode and replay report: TBD_OWNER;
- evidence-index entry and sidecars: TBD_OWNER.

Allowed scientific terminal statuses are limited to the existing governed
vocabulary. Even a DESCRIPTIVE_REGIME_ONLY result remains descriptive and
does not authorize association, forecasting, warning, or operational use.

## 7. Owner completion and release gate

- [ ] I completed every TBD_OWNER field with byte-bound values.
- [ ] I confirmed that no daily or seasonal gate was weakened.
- [ ] I confirmed that no new estimand or post-hoc feature was introduced.
- [ ] I confirmed the exact CDS request and storage reserve.
- [ ] I confirmed that the negative-control refusal is mandatory.
- [ ] I recorded approval identity, decision time, and evidence reference.
- [ ] I understand that this template itself authorizes nothing.

**Owner decision:** TBD_OWNER  
**Decision UTC:** TBD_OWNER  
**Decision record digest:** TBD_OWNER
