"""nepal.science_v0 — research-only scientific execution engines.

Synthetic-fixture machinery for the pre-intake phase: event/identity
normalization, observation opportunities, non-event controls, holdout
assignment (events.py), value-level feature-matrix audit (fmx_audit.py),
and the multi-region retrospective regime runner (regimes.py).

Nothing in this package downloads data, emits event labels, freezes a
feature matrix, or produces a regime result on real data. All outputs
are descriptive/research-only. Statuses emitted are limited to the
vocabulary declared in docs/science/run_b/ (DESCRIPTIVE_REGIME_ONLY,
UNSUPERVISED_STRUCTURE_NOT_STABLE, CANDIDATE_ONLY).
"""
