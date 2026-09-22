# P5 Seasonal Regime Study — Statistical Power and Information Note v0

**Status: RESEARCH NOTE — not an amendment and not an authorization**

This note records what can and cannot be inferred from the executed seasonal
lane. It does not change the estimand, covariance policy, gates, nulls,
embargo, holdout, or claim ceiling. It does not authorize new data
acquisition, Arm C, association, forecasting, warning, production, or
operational use.

## 1. Executive conclusion

The executed seasonal frame contains 75 basin-year observations: three basins
over 25 JJA seasons. The declared temporal split places 51 rows in training,
6 rows in the embargo, and 18 rows in the sealed holdout. The locked Arm B
contract has six features:

    t2m_mean, d2m_mean, pdd_sum, tp_q95, wet_spell_max_days, sd_delta

The result is an honest negative for the declared seasonal estimand:

- modal K was non-unanimous across seeds, 3/3/4;
- the season-matched-null p-value was 0.66, not below the declared 0.05
  threshold;
- the shuffled-null envelope was incomplete because 7 of 50 replicates
  failed.

These facts do not establish that seasonal types cannot exist. They establish
that this frame did not clear the preregistered evidence bar. The n=75 total
and, more importantly, n=51 training surface create a small-sample,
parameter-to-information constraint for mixture selection. That constraint is
a design diagnostic, not a universal theorem that supplies a minimum sample
size.

## 2. What “power” means for this unsupervised question

There is no single conventional power calculation for the question “does a
low-complexity unsupervised structure reproduce?” The result depends jointly
on:

- the number of observations and their basin/season balance;
- the number of features and their dependence;
- covariance parameterization and regularization;
- component separation, overlap, and mixture weights;
- seed sensitivity and K-selection behavior;
- the temporal and basin-aware resampling policy;
- the completeness and calibration of both null families.

Cluster stability is normally assessed through resampling or related
perturbations, and stability is data- and separation-dependent rather than a
universal fixed cutoff. The methodological literature therefore supports a
prospective simulation or sensitivity analysis for this design, not a
retrospective declaration of a minimum n from one negative run.

## 3. Parameter accounting for the executed frame

For a Gaussian mixture with K components and d features, the free parameter
count, excluding the data, is:

| Covariance contract | Free parameters |
|---|---|
| tied | (K - 1) mixture weights + Kd means + d(d + 1)/2 covariance terms |
| diagonal | (K - 1) mixture weights + Kd means + Kd variance terms |
| full | (K - 1) mixture weights + Kd means + Kd(d + 1)/2 covariance terms |

For the locked six-feature contract, the resulting counts are:

| K | tied | diagonal | full |
|---:|---:|---:|---:|
| 2 | 34 | 25 | 55 |
| 3 | 41 | 38 | 83 |
| 4 | 48 | 51 | 111 |

The seasonal lane uses tied covariance and restricts K to 2–4. At the
training surface n=51, the tied counts are 34, 41, and 48. This leaves only
17, 10, and 3 observations respectively after a simple parameter-minus-row
comparison. That subtraction is not a valid degrees-of-freedom test for
mixture models, but it makes the information constraint visible. A diagonal
K=4 fit would reach 51 formal parameters before accounting for estimation
instability; a full K=2 fit would already exceed the training-row count.
Those alternatives are therefore not evidence that the executed tied model
was wrong; they explain why the seasonal parameter guard and covariance
restriction are necessary.

The scikit-learn GaussianMixture contract confirms that full, tied, diagonal,
and spherical covariance types represent different covariance surfaces and
that covariance regularization is a separate fitting parameter. The
repository’s seasonal contract must continue to bind the covariance type and
regularization explicitly rather than infer them from a future fit.

## 4. Interpretation of the three observed failures

### 4.1 Modal K non-unanimity

The seed results 3/3/4 fail the structural modal-K-unanimity gate. This is
direct evidence that the selected complexity is sensitive to initialization or
the finite sample. It is stronger than merely observing that one seed had a
different label permutation: K itself changed.

### 4.2 Season-matched null

The p-value 0.66 is far above the declared alpha 0.05. Under the locked
season-matched null, the observed statistic did not distinguish itself from
the predeclared seasonal/era marginal structure. The correct interpretation
is failure to demonstrate structure against that null, not evidence that the
null is physically true for every future frame.

### 4.3 Incomplete shuffled-null envelope

Seven of 50 shuffled replicates failed. The envelope is therefore incomplete
and fails closed. Increasing the number of nominal replicates after seeing
this result, dropping failed replicates, or interpolating missing statistics
would not be a valid power improvement; it would change the evidence rule.

## 5. What a future expansion must deliver

A future expansion should be planned as a new amendment if it changes years,
basin coverage, feature definitions, covariance, K, or acquisition scope.
It should preserve the following controls:

1. the same seasonal estimand unless a new estimand is explicitly declared;
2. a complete, byte-bound frame and feature manifest;
3. training-only preprocessing and a sealed embargo/holdout;
4. the same seed, ARI, modal-K, null, bootstrap, drift, missingness, and
   effort gates;
5. a complete shuffled-null and season-matched-null envelope;
6. negative-control refusal before fitting and statistical rejection;
7. an explicit covariance and regularization contract;
8. the descriptive claim ceiling even if every gate passes.

The following are planning scenarios, not minimum sample-size claims:

| Scenario | Basins | Seasons | Total rows | Training rows if the same 2-year embargo and 6-season holdout are retained |
|---|---:|---:|---:|---:|
| Executed baseline | 3 | 25 | 75 | 51 |
| Expansion A | 3 | 40 | 120 | 96 |
| Expansion B | 3 | 50 | 150 | 126 |
| Geographic expansion | TBD_OWNER | TBD_OWNER | TBD_OWNER | TBD_OWNER |

The row count alone is insufficient. Expansion A or B would still be
uninformative if one basin or era dominates, if the feature covariance is
near-singular, if null replicates do not converge, or if K remains
non-unanimous. Adding basins changes the geographic estimand and requires a
separate declaration; it is not a free substitute for adding seasons.

## 6. Recommended prospective sensitivity analysis

Before choosing an acquisition scope, an owner-approved design amendment
should specify a simulation grid. The minimum useful sequence is:

1. Freeze the feature count, scaling, covariance type, regularization, K
   candidates, seeds, temporal split, resampling blocks, and null rules.
2. Use only the training surface to calibrate nuisance ranges. Do not use
   sealed holdout values to tune a simulated alternative.
3. Simulate balanced and deliberately imbalanced basin-year frames over
   candidate row counts, including the executed 75-row design and proposed
   expansions.
4. Vary component separation, covariance dependence, missingness, and
   effective basin/season imbalance over a preregistered grid.
5. Run the complete governed pipeline, including K selection, every seed,
   both null families, negative control, and all binding gates.
6. Report, for each scenario, the proportion clearing structural gates, the
   proportion with complete null envelopes, false-promotion frequency under a
   no-structure null, and the probability of a fully descriptive result
   under each simulated alternative.
7. Select an acquisition scope only from that sensitivity table and an
   explicit owner decision. Do not convert the resulting planning probability
   into a claim about the observed Nepal frame.

This analysis would answer “what designs could reliably detect a declared
separation under declared assumptions?” It would not answer “what is the
probability that the observed Nepal seasons truly have regimes?”

## 7. Decision rule for a future positive

A larger frame is interpretable only if the complete preregistered pipeline
clears the structural and evidence gates. In particular:

- stable labels at one K across all declared seeds are required;
- the ARI and basin-aware holdout behavior must meet the locked thresholds;
- both null envelopes must be complete and the observed statistic must beat
  the declared null criterion;
- the negative control must be refused before fitting and rejected
  statistically;
- no waiver may be used as positive evidence;
- the result remains DESCRIPTIVE_REGIME_ONLY.

If a future expansion again returns UNSUPERVISED_STRUCTURE_NOT_STABLE or
CANDIDATE_ONLY, that remains a valid fail-closed result for its declared
frame. If the expanded frame is blocked, incomplete, or underpowered, it
must not be relabeled as a scientific negative.

## 8. References and scope of use

The following sources support the methodological framing only; none is
external validation of the Nepal result:

1. scikit-learn, GaussianMixture covariance types and parameterization:
   https://scikit-learn.org/stable/modules/generated/sklearn.mixture.GaussianMixture.html
2. Hennig, C. (2007), Cluster-wise assessment of cluster stability,
   DOI: https://doi.org/10.1016/j.csda.2006.11.025
3. Yu et al. (2019), Bootstrapping estimates of stability for clusters,
   observations and model selection, DOI:
   https://doi.org/10.1007/s00180-018-0830-y
4. Liu (2022), Out-of-bag stability estimation for k-means clustering,
   DOI: https://doi.org/10.1002/sam.11593

The authoritative Nepal facts remain the repository-bound seasonal frame,
receipt, artifact, replay, and gate observations. This note does not replace
those records.
