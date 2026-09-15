"""Pure metric functions for the v0 evaluation scaffold.

Implements the predeclared metric set of
``docs/science/run_c/FORECAST_EVAL_SCAFFOLD_V0.md`` section 4 on aligned
sequences of binary truth and probabilities.  The caller excludes
``CENSORED_OR_AMBIGUOUS`` cases before calling — these functions see
only the unambiguous subset and never silently treat censored windows
as negatives.

Every function is deterministic and JSON-safe: degenerate inputs
(empty sequences, single-class truth, non-positive denominators) return
0.0-filled results rather than NaN, so reports always serialize through
``canonical_json``.

These are descriptive research computations only — nothing here is a
real-data finding or an authorization of any kind.
"""
from __future__ import annotations

import math
from typing import Any, Sequence

_EPS = 1e-6


def _binary(y_true: Any) -> list[int]:
    """Coerce truth values to 0/1.

    Accepts 0/1 numerics, booleans, ``TargetState`` members, and the
    strings "POSITIVE"/"NEGATIVE".  Anything else rejects — a truth
    value that is not unambiguously binary must never be guessed.
    """
    out: list[int] = []
    for value in y_true:
        if isinstance(value, bool):
            out.append(int(value))
        elif isinstance(value, (int, float)):
            if value in (0, 1):
                out.append(int(value))
            else:
                raise ValueError(
                    f"truth value {value!r} is not binary")
        elif isinstance(value, str):
            token = value.strip().upper()
            if token == "POSITIVE":
                out.append(1)
            elif token == "NEGATIVE":
                out.append(0)
            else:
                raise ValueError(
                    f"truth state {value!r} is not POSITIVE/NEGATIVE")
        else:
            name = getattr(value, "value", None)
            if name in ("POSITIVE", "NEGATIVE"):
                out.append(1 if name == "POSITIVE" else 0)
            else:
                raise ValueError(
                    f"truth value {value!r} is not binary")
    return out


def _probs(y_prob: Any) -> list[float]:
    """Coerce predicted probabilities to finite floats in [0, 1]."""
    out: list[float] = []
    for value in y_prob:
        p = float(value)
        if not math.isfinite(p) or p < 0.0 or p > 1.0:
            raise ValueError(
                f"predicted probability {value!r} is not finite in [0,1]")
        out.append(p)
    return out


def _flags(y_flag: Any) -> list[int]:
    return [1 if bool(f) else 0 for f in y_flag]


def _aligned(y_true: Any, y_prob: Any) -> tuple[list[int], list[float]]:
    y = _binary(y_true)
    p = _probs(y_prob)
    if len(y) != len(p):
        raise ValueError(
            f"aligned-sequence length mismatch: {len(y)} truth values "
            f"vs {len(p)} probabilities")
    return y, p


def brier_score(y_true: Any, y_prob: Any) -> float:
    """Mean squared probability error on the unambiguous subset."""
    y, p = _aligned(y_true, y_prob)
    if not y:
        return 0.0
    return sum((pi - yi) ** 2 for yi, pi in zip(y, p)) / len(y)


def calibration_bins(y_true: Any, y_prob: Any,
                     n_bins: int = 10) -> list[dict]:
    """Equal-width reliability bins over [0, 1].

    Returns one dict per bin with ``count``, ``mean_predicted``, and
    ``mean_observed``; empty bins report 0.0 means so the curve is
    always JSON-serializable.  ``p == 1.0`` lands in the last bin.
    """
    y, p = _aligned(y_true, y_prob)
    n_bins = max(1, int(n_bins))
    bins = [
        {"bin": i, "lower": i / n_bins, "upper": (i + 1) / n_bins,
         "count": 0, "sum_predicted": 0.0, "sum_observed": 0.0,
         "mean_predicted": 0.0, "mean_observed": 0.0}
        for i in range(n_bins)]
    for yi, pi in zip(y, p):
        idx = min(n_bins - 1, int(pi * n_bins))
        bins[idx]["count"] += 1
        bins[idx]["sum_predicted"] += pi
        bins[idx]["sum_observed"] += yi
    for b in bins:
        if b["count"]:
            b["mean_predicted"] = b["sum_predicted"] / b["count"]
            b["mean_observed"] = b["sum_observed"] / b["count"]
        del b["sum_predicted"]
        del b["sum_observed"]
    return bins


def _logit(p: float) -> float:
    p = min(1.0 - _EPS, max(_EPS, p))
    return math.log(p / (1.0 - p))


def _sigmoid(z: float) -> float:
    if z >= 0:
        return 1.0 / (1.0 + math.exp(-z))
    ez = math.exp(z)
    return ez / (1.0 + ez)


def calibration_fit(y_true: Any, y_prob: Any) -> dict:
    """Logistic recalibration fit: ``{"slope", "intercept"}``.

    Fits ``logit(E[y]) = intercept + slope * logit(p)`` by Newton
    iteration — a well-calibrated scorer has intercept ~ 0 and
    slope ~ 1.  Degenerate inputs (fewer than two observations or a
    single-class truth) return slope 0.0 with the marginal-rate
    intercept.
    """
    y, p = _aligned(y_true, y_prob)
    if len(y) < 2 or len(set(y)) < 2:
        rate = sum(y) / len(y) if y else 0.5
        return {"slope": 0.0, "intercept": _logit(rate)}
    z = [_logit(pi) for pi in p]
    a, b = 0.0, 1.0
    for _ in range(64):
        g0 = g1 = 0.0
        h00 = h01 = h11 = 0.0
        for yi, zi in zip(y, z):
            mu = _sigmoid(a + b * zi)
            w = mu * (1.0 - mu)
            r = yi - mu
            g0 += r
            g1 += r * zi
            h00 += w
            h01 += w * zi
            h11 += w * zi * zi
        det = h00 * h11 - h01 * h01
        if not math.isfinite(det) or abs(det) < 1e-12:
            break
        da = (h11 * g0 - h01 * g1) / det
        db = (-h01 * g0 + h00 * g1) / det
        a += da
        b += db
        if abs(da) < 1e-10 and abs(db) < 1e-10:
            break
    if not (math.isfinite(a) and math.isfinite(b)):
        rate = sum(y) / len(y)
        return {"slope": 0.0, "intercept": _logit(rate)}
    return {"slope": b, "intercept": a}


def pr_curve(y_true: Any, y_prob: Any) -> dict:
    """Precision-recall curve plus area under it.

    Thresholds are the distinct predicted probabilities in descending
    order.  ``auprc`` accumulates ``delta_recall * precision`` at each
    distinct threshold (average-precision-style step integration), so
    ties are handled atomically.  A truth vector with no positives
    yields ``auprc == 0.0`` with the curve still reported.
    """
    y, p = _aligned(y_true, y_prob)
    n_pos = sum(y)
    order = sorted(range(len(p)), key=lambda i: (-p[i], i))
    precision: list[float] = []
    recall: list[float] = []
    thresholds: list[float] = []
    auprc = 0.0
    tp = fp = 0
    prev_recall = 0.0
    i = 0
    while i < len(order):
        t = p[order[i]]
        while i < len(order) and p[order[i]] == t:
            if y[order[i]]:
                tp += 1
            else:
                fp += 1
            i += 1
        prec = tp / (tp + fp)
        rec = tp / n_pos if n_pos else 0.0
        precision.append(prec)
        recall.append(rec)
        thresholds.append(t)
        auprc += (rec - prev_recall) * prec
        prev_recall = rec
    return {"precision": precision, "recall": recall,
            "thresholds": thresholds, "auprc": auprc}


def event_recall(y_true: Any, y_flag: Any) -> float:
    """Fraction of adjudicated-positive cases flagged at the fixed
    decision threshold.  No positives -> 0.0."""
    y = _binary(y_true)
    flags = _flags(y_flag)
    if len(y) != len(flags):
        raise ValueError(
            f"aligned-sequence length mismatch: {len(y)} truth values "
            f"vs {len(flags)} flags")
    n_pos = sum(y)
    if not n_pos:
        return 0.0
    return sum(1 for yi, fi in zip(y, flags) if yi and fi) / n_pos


def false_alarms_per_opportunity(y_flag: Any,
                                 n_opportunities: int) -> float:
    """Flagged count divided by verified observation opportunities.

    ``n_opportunities`` is the count of ``OBSERVED_FULL``-verified
    opportunities (the caller supplies it).  A non-positive denominator
    yields 0.0 rather than NaN so reports stay JSON-safe.
    """
    flags = _flags(y_flag)
    if n_opportunities <= 0:
        return 0.0
    return sum(flags) / n_opportunities


__all__ = [
    "brier_score", "calibration_bins", "calibration_fit", "pr_curve",
    "event_recall", "false_alarms_per_opportunity",
]
