"""Transparent predeclared baselines for the v0 evaluation scaffold.

Implements the mandatory baseline suite of
``docs/science/run_c/FORECAST_EVAL_SCAFFOLD_V0.md`` section 3 as pure,
deterministic functions:

* ``climatology`` — (unit, season) base-rate lookup with a global-mean
  fallback for unobserved cells; defines the no-signal floor.
* ``rule`` — a predeclared conjunction of threshold rules over case
  features, combined as the fuzzy-logic minimum (the weakest conjunct
  governs); fully transparent and specified before evaluation.
* ``null`` — constant marginal-rate output; detects degenerate
  "better than nothing" artifacts.
* ``regularized_supervised`` — an L2-penalized logistic fit returning a
  reusable probability callable.

These are contract-layer scoring helpers for synthetic fixtures.  They
perform no downloads, touch no real data, and produce descriptive
research metrics only — nothing here is a real-data finding.

Fitting discipline: every ``fit_*`` surface takes a typed
``FitPartition`` declared ``TRAIN_ONLY`` — held-out cases and their
labels can never be passed to a baseline fit.  Scored ``ForecastCase``
objects and serialized case payloads reject at the fit boundary, and
fit labels admit only strict binary values (``0``/``1``, booleans,
``TargetState`` members, or the ``"POSITIVE"``/``"NEGATIVE"``
vocabulary — truthy strings like ``"yes"``/``"true"``/``"1"`` never
coerce).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping, Sequence

# Order is contract-pinned (FORECAST_EVAL_SCAFFOLD_V0 section 3); all
# four names must be present in an experiment's baseline set.
BASELINE_NAMES = (
    "climatology", "rule", "null", "regularized_supervised")
REQUIRED_BASELINE_NAMES = frozenset(BASELINE_NAMES)

#: The only admissible ``FitPartition.partition`` label — mirrors the
#: ``fitted_on == "TRAIN_ONLY"`` invariant of the frozen regime
#: artifact.  Held-out (test/validation/evaluation) data may never
#: enter a baseline fit.
TRAIN_ONLY_PARTITION = "TRAIN_ONLY"

#: Serialized-case identity fields: a fit row carrying any of these is
#: a scored-case payload, never a legitimate feature row.
_CASE_PAYLOAD_KEYS = frozenset({
    "case_id", "y_state", "y_prob", "vintage_digest",
    "opportunity_id", "outcome_source_id"})


@dataclass(frozen=True)
class FitPartition:
    """A declared, typed train-only fitting partition.

    ``partition`` must equal ``"TRAIN_ONLY"`` — the only admissible
    fitting surface; any other declared partition rejects.  ``rows``
    are feature rows (numeric sequences or str-keyed feature
    mappings), ``labels`` the aligned strict binary targets, and
    ``cell_keys`` optionally carries each row's ``(unit_id, season)``
    cell for climatology fits.  Fit inputs are caller-supplied train
    arrays — never the scored case list.
    """

    partition: str
    rows: Any = ()
    labels: Any = ()
    cell_keys: Any = ()


def _require_train_partition(partition: Any) -> FitPartition:
    """Fail-closed admission of a fit partition."""
    if type(partition) is not FitPartition:
        raise ValueError(
            "baseline fits require a FitPartition — a typed, declared "
            "train-only partition, never the scored case list")
    if partition.partition != TRAIN_ONLY_PARTITION:
        raise ValueError(
            f"baseline fits require partition == "
            f"{TRAIN_ONLY_PARTITION!r}; declared partition "
            f"{partition.partition!r} is inadmissible — held-out or "
            "undeclared data can never enter a baseline fit")
    return partition


def _reject_case_like_rows(materialized: Sequence[Any]) -> None:
    """Reject scored cases inside fit/score row sequences.

    A row that is a ``ForecastCase``-shaped object (carrying
    ``case_id``/``y_state`` attributes) or a mapping containing
    case-identity keys is a held-out case, not a feature row.
    """
    for row in materialized:
        if isinstance(row, Mapping):
            bad = set(row) & _CASE_PAYLOAD_KEYS
            if bad:
                raise ValueError(
                    f"row carries scored-case fields {sorted(bad)} — "
                    "held-out cases are never baseline fit input")
        elif getattr(row, "case_id", None) is not None or \
                getattr(row, "y_state", None) is not None:
            raise ValueError(
                "rows must be feature sequences or str-keyed feature "
                "mappings — scored ForecastCase objects are never "
                "baseline fit input")
        elif isinstance(row, (str, bytes)) or \
                not isinstance(row, Iterable):
            raise ValueError(
                f"row {row!r} is not a feature sequence or mapping")


def _clip01(value: Any) -> float:
    """Clamp a numeric into [0, 1]; non-finite collapses to 0.0."""
    try:
        p = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(p):
        return 0.0
    return min(1.0, max(0.0, p))


def _field(case: Any, name: str, default: Any = None) -> Any:
    """Read ``name`` from a case object or mapping, else ``default``."""
    value = getattr(case, name, None)
    if value is not None:
        return value
    if isinstance(case, Mapping):
        return case.get(name, default)
    return default


def climatology_probs(
        cases: Sequence[Any],
        rates: Mapping[tuple[str, str], float]) -> list[float]:
    """(unit_id, season) base-rate lookup; missing cell -> global mean.

    ``rates`` maps ``(unit_id, season)`` to an opportunity-weighted base
    rate.  A case whose cell is absent inherits the mean over all
    declared rates (the marginal rate); an empty ``rates`` mapping
    yields 0.0 everywhere.  Outputs are clipped to [0, 1].
    """
    values = [_clip01(v) for v in rates.values()]
    global_mean = sum(values) / len(values) if values else 0.0
    out: list[float] = []
    for case in cases:
        key = (str(_field(case, "unit_id", "")),
               str(_field(case, "season", "")))
        out.append(_clip01(rates.get(key, global_mean)))
    return out


@dataclass(frozen=True)
class ThresholdRule:
    """One predeclared transparent threshold rule.

    The rule fires (returns ``high_prob``) when
    ``case.features[feature] >= threshold``; otherwise — including a
    missing or non-numeric feature — it returns ``low_prob``.
    """

    feature: str
    threshold: float
    low_prob: float
    high_prob: float


def rule_probs(cases: Sequence[Any],
               rules: Sequence[ThresholdRule]) -> list[float]:
    """AND-combine predeclared threshold rules over ``case.features``.

    Each rule maps a case to ``high_prob`` when its threshold is met
    and ``low_prob`` otherwise.  Conjunction is the fuzzy-logic
    minimum: the case inherits the weakest per-rule output, so a case
    scores high only when *every* rule fires.  An empty rule list is a
    degenerate declaration and scores 0.0 everywhere.
    """
    out: list[float] = []
    for case in cases:
        features = _field(case, "features", None) or {}
        if not isinstance(features, Mapping):
            features = {}
        if not rules:
            out.append(0.0)
            continue
        per_rule: list[float] = []
        for rule in rules:
            raw = features.get(rule.feature)
            try:
                fired = raw is not None and math.isfinite(float(raw)) \
                    and float(raw) >= float(rule.threshold)
            except (TypeError, ValueError):
                fired = False
            per_rule.append(_clip01(
                rule.high_prob if fired else rule.low_prob))
        out.append(min(per_rule))
    return out


def null_probs(n: int, base_rate: float) -> list[float]:
    """Constant marginal-rate output — the no-signal reference."""
    return [_clip01(base_rate)] * max(0, int(n))


def _rows_to_matrix(rows: Any,
                    names: Sequence[str] | None = None,
                    ) -> tuple[list[list[float]], list[str] | None]:
    """Normalize rows to a float matrix.

    Mapping rows use ``names`` (or the sorted union of keys when
    ``names`` is ``None``); sequence rows are cast positionally.
    Scored-case payloads and case objects reject — see
    ``_reject_case_like_rows``.
    """
    if isinstance(rows, (str, bytes)) or isinstance(rows, Mapping):
        raise ValueError(
            "rows must be a sequence of feature rows — a bare string "
            "or mapping is not a row set")
    try:
        materialized = list(rows)
    except TypeError:
        raise ValueError(
            "rows must be a sequence of feature rows") from None
    _reject_case_like_rows(materialized)
    if materialized and isinstance(materialized[0], Mapping):
        if names is None:
            names = sorted({str(k) for row in materialized
                            for k in row})
        matrix = []
        for row in materialized:
            matrix.append([_clip01ish(row.get(n)) for n in names])
        return matrix, list(names)
    return [[_clip01ish(v) for v in r] for r in materialized], \
        list(names) if names is not None else None


def _clip01ish(value: Any) -> float:
    """Finite float coercion for feature values; missing -> 0.0."""
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    return out if math.isfinite(out) else 0.0


def _as_binary_labels(y: Any) -> list[int]:
    """Coerce fit labels to 0/1 ints — strict, never truthy-coercive.

    Accepts booleans, 0/1 numerics, ``TargetState`` members, and the
    ``"POSITIVE"``/``"NEGATIVE"`` strings.  Everything else —
    ``"yes"``, ``"true"``, ``"1"``, ``None``, other numerics, and
    ``"CENSORED_OR_AMBIGUOUS"`` — rejects: a fit label that is not
    unambiguously binary is a defect, never a guess.
    """
    if isinstance(y, (str, bytes)):
        raise ValueError(
            "labels must be a sequence of binary targets — a bare "
            "string is not a label set")
    out: list[int] = []
    for value in y:
        if isinstance(value, bool):
            out.append(int(value))
        elif isinstance(value, (int, float)):
            if value in (0, 1):
                out.append(int(value))
            else:
                raise ValueError(
                    f"label {value!r} is not binary")
        elif isinstance(value, str):
            token = value.strip().upper()
            if token == "POSITIVE":
                out.append(1)
            elif token == "NEGATIVE":
                out.append(0)
            else:
                raise ValueError(
                    f"label {value!r} is not POSITIVE/NEGATIVE — "
                    "truthy strings like 'yes'/'true'/'1' never "
                    "coerce to a fit label")
        else:
            name = getattr(value, "value", None)
            if name in ("POSITIVE", "NEGATIVE"):
                out.append(1 if name == "POSITIVE" else 0)
            else:
                raise ValueError(
                    f"label {value!r} is not binary")
    return out


def fit_climatology_rates(
        partition: FitPartition) -> dict[tuple[str, str], float]:
    """(unit_id, season) base rates fitted on a TRAIN_ONLY partition.

    ``partition.cell_keys`` must align with ``partition.labels``: one
    ``(unit_id, season)`` string pair per label.  Only the declared
    training partition's labels enter the rates — held-out labels are
    inadmissible.
    """
    p = _require_train_partition(partition)
    labels = _as_binary_labels(p.labels)
    keys = list(p.cell_keys) if p.cell_keys is not None else []
    if len(keys) != len(labels):
        raise ValueError(
            f"cell_keys/labels length mismatch: {len(keys)} cells vs "
            f"{len(labels)} labels")
    if not labels:
        raise ValueError(
            "cannot fit climatology rates on zero rows")
    cells: dict[tuple[str, str], list[int]] = {}
    for key, label in zip(keys, labels):
        if not isinstance(key, (tuple, list)) or len(key) != 2 or \
                not all(isinstance(v, str) for v in key):
            raise ValueError(
                f"cell key {key!r} must be a (unit_id, season) pair "
                "of strings")
        cells.setdefault((key[0], key[1]), []).append(label)
    return {k: sum(v) / len(v) for k, v in cells.items()}


def fit_marginal_rate(partition: FitPartition) -> float:
    """The marginal positive rate of a TRAIN_ONLY partition — the
    honest base rate for the ``null`` baseline."""
    p = _require_train_partition(partition)
    labels = _as_binary_labels(p.labels)
    if not labels:
        raise ValueError("cannot fit a marginal rate on zero labels")
    return sum(labels) / len(labels)


def fit_regularized_supervised(
        partition: FitPartition, *, seed: int = 0, C: float = 1.0,
        ) -> Callable[[Any], list[float]]:
    """Fit an L2-penalized logistic baseline on a TRAIN_ONLY
    ``FitPartition``; return a probability callable
    ``rows -> list[float]``.

    ``partition.rows`` may be numeric sequences or mappings (mapping
    keys are sorted once at fit time and reused at call time);
    ``partition.labels`` are strict binary targets.  Held-out cases —
    ``ForecastCase`` objects or case-payload mappings — reject at the
    fit boundary.  ``C`` is the inverse L2 strength; ``seed`` fixes
    the solver's random state.  A single-class label set cannot be
    fit — a constant callable at the observed rate is returned
    instead of raising.
    """
    p = _require_train_partition(partition)
    matrix, names = _rows_to_matrix(p.rows)
    labels = _as_binary_labels(p.labels)
    if len(matrix) != len(labels):
        raise ValueError(
            f"X/y length mismatch: {len(matrix)} rows vs "
            f"{len(labels)} labels")
    if not matrix:
        raise ValueError("cannot fit a supervised baseline on zero rows")
    positive_rate = sum(labels) / len(labels)

    if len(set(labels)) < 2:
        constant = _clip01(positive_rate)

        def constant_predict(rows: Any) -> list[float]:
            return [constant] * len(list(rows))

        return constant_predict

    from sklearn.linear_model import LogisticRegression

    # Default penalty is L2 (explicit ``penalty`` is deprecated in
    # sklearn >= 1.8); ``C`` is the inverse regularization strength.
    model = LogisticRegression(
        C=float(C), solver="lbfgs", max_iter=1000,
        random_state=int(seed))
    model.fit(matrix, labels)

    def predict(rows: Any) -> list[float]:
        predict_matrix, _ = _rows_to_matrix(rows, names)
        probs = model.predict_proba(predict_matrix)
        classes = list(model.classes_)
        try:
            col = classes.index(1)
        except ValueError:
            return [0.0] * len(predict_matrix)
        return [_clip01(p[col]) for p in probs]

    return predict
