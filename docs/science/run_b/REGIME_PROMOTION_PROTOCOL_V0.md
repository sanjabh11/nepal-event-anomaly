# Regime Promotion Protocol — v0 (Run B)

**Status:** `PREREGISTERED_DRAFT` — binding criteria declared **before**
any new data lane (Arm C pressure-level, multi-season expansion, or any
successor frame) is executed. **Recorded:** 2026-09-22.
**Lane:** Run B successor planning (audit-4 → Phase C).

This document preregisters the evidence bar for promoting a regime
verdict. It modifies no gate, no estimand, no threshold, and no frozen
record. It binds the existing engine vocabulary in
`nepal/science_v0/regimes.py` and `run_b/REGIME_PROTOCOL_V0.md` so that
a future positive result is evaluated against criteria written before
the data that could produce it existed.

## 1. Why this exists

Both executed lanes returned `UNSUPERVISED_STRUCTURE_NOT_STABLE` —
honest negatives under the fail-closed gate system. Any future lane
that adds features (Arm C) or rows (multi-season expansion) creates a
new opportunity for structure, and therefore a new opportunity for
motivated reasoning: thresholds tuned after seeing results, waivers
stacked to clear gates, or a `CANDIDATE_ONLY` quietly treated as
success. This protocol forecloses that by fixing the promotion ladder
in advance.

## 2. The promotion ladder

```
RUN_ERROR                          (preflight rejection — not a status)
    │
    ▼
UNSUPERVISED_STRUCTURE_NOT_STABLE  (any structural gate failed)
    │   requires ALL structural gates PASS:
    │     modal_k_unanimous, seed_ari (>0.6), seed_coverage, loro
    ▼
CANDIDATE_ONLY                     (structure reproduces; an evidence
    │                                gate failed — demotion, never
    │                                terminal-associable)
    │   requires ALL 13 required_gates PASS
    │   (or policy-admissible NOT_APPLICABLE):
    │     seed_policy, temporal_bootstrap, season_refits,
    │     elevation, missingness, effort, era_drift,
    │     shuffled_null, season_matched_null
    ▼
DESCRIPTIVE_REGIME_ONLY            (every gate closed; may freeze)
    │
    ▼  ─── separate Run-C authorization; NEVER automatic ───
association eligibility            (ASSOCIATION_PROTOCOL_V0.md)
```

## 3. Binding promotion criteria

### 3.1 Exit from `UNSUPERVISED_STRUCTURE_NOT_STABLE`

All four structural gates must observe `PASS`:

- `modal_k_unanimous` — identical modal K across **every** declared
  seed (`k_freq == 1.0`). The v1 seasonal failure (3/3/4 across seeds)
  is the canonical counterexample.
- `seed_ari` — minimum pairwise label-invariant ARI across seed folds
  **strictly > 0.6**.
- `seed_coverage` — every declared seed accounted for; an unaccounted
  seed is instability, not a gap.
- `loro` — per declared `loro_policy`; a `diagnostic` policy emits
  `SKIPPED`/non-binding observations and does not count as evidence
  either way.

### 3.2 Promotion to `DESCRIPTIVE_REGIME_ONLY`

All 13 `required_gates` must be `True`, with the following
non-negotiable readings:

- **Null families** (`shuffled_null`, `season_matched_null`): the
  envelope must be **complete** — every declared replicate converged
  (`n_failed == 0`; the v1 seasonal lane failed closed at 7/50
  shuffled-replicate failures) — **and** the observed predeclared
  statistic (silhouette) must exceed the envelope at `null_alpha`
  (the v1 season-matched p = 0.66 is the canonical failure). A partial
  envelope fails closed; it is never interpolated.
- **Axis gates** (`season_refits`, `elevation`, `missingness`,
  `effort`, `era_drift`): each must observe `PASS`. A bound waiver
  (`*_waiver_reason`) makes the gate non-blocking but is explicitly
  **non-promoting** — a waived axis can never stand in for evidence,
  and a lane whose result rests on waivers discloses them.
- `temporal_bootstrap`: declared block length, cadence, and gap
  policy bound in config; `PASS` required.
- `seed_policy`: `fold_seed_policy == "all"`; one-seed folds are
  diagnostic-only forever.

### 3.3 Beyond `DESCRIPTIVE_REGIME_ONLY`

`DESCRIPTIVE_REGIME_ONLY` is the **ceiling** of unsupervised
discovery. It authorizes freeze — nothing more. Association testing
(Run C), forecast evaluation, and any operational vocabulary require
separate, later, explicitly authorized protocols. A frozen regime is
a descriptive statement about structure in a bounded retrospective
frame, never a detector, predictor, or warning.

## 4. Legitimacy requirements for any new run

A new lane's verdict counts toward promotion only if:

1. **Amendment before execution** — a versioned amendment record
   declares the estimand, frame version, feature set, config, and
   output paths *before* the run; the config digest is bound before
   any fit.
2. **Declared config, never inferred** — `RegimeRunConfig` fields are
   declared in the amendment; post-hoc reconstruction of "what we
   probably ran" is inadmissible.
3. **Negative-control refusal mandatory** — a declared
   `negative_control` input must be refused before fitting AND
   rejected statistically; a lane without a refusing control is
   incomplete evidence.
4. **Same gate vocabulary** — no new gates may be silently added, no
   existing gate weakened, no threshold changed relative to this
   document and `REGIME_PROTOCOL_V0.md`. Changing the bar requires a
   V1 of this protocol, dated and justified before the run it governs.
5. **K restriction holds** — `k_candidates` fixed at declaration;
   post-hoc expansion to fit a desired K is prohibited.
6. **Exclusive-create outputs** — new evidence lands at new paths;
   no historical artifact is overwritten.

## 5. Falsifiability and honest negatives

A promotion protocol that cannot produce a negative is not a
protocol. If an expanded or enriched frame still yields
`UNSUPERVISED_STRUCTURE_NOT_STABLE` or `CANDIDATE_ONLY`, that verdict
is a complete, publishable-quality scientific result: Nepal's
declared feature space, at the declared grain, does not support
stable unsupervised regime structure under these gates. `BLOCKED`,
`NOT_OPERATIONAL`, and waived axes are never reclassified as
scientific negatives or positives.

## 6. Explicit prohibitions

- No retroactive threshold tuning after observing a lane's output.
- No waiver stacking to clear an axis that data could clear.
- No treating `CANDIDATE_ONLY` as a soft pass for downstream
  association.
- No label access before freeze (`label_blinding` is mandatory).
- No forecast-vintage material in a `RETROSPECTIVE_REGIME` run.
- No claim language above `DESCRIPTIVE_REGIME_ONLY` anywhere —
  association, detection, forecast, warning, and operational terms
  remain outside the claim ceiling.

## 7. Binding references

- `run_b/REGIME_PROTOCOL_V0.md` — gate semantics this protocol binds.
- `run_c/ASSOCIATION_PROTOCOL_V0.md` — the separate downstream gate.
- `nepal/science_v0/regimes.py` — `REQUIRED_REGIME_GATE_NAMES` (13
  gates), `LORO_ARI_MIN = 0.6`, `K_CANDIDATES`, null-family contracts.
- `docs/science/P5_EXTENSION_STATUS_V0.md` — the two executed honest
  negatives this protocol stands against.
