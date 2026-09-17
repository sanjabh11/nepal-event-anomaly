"""Value-level feature-matrix audit (FMX_AUDIT_RUBRIC_V0.md).

The contract layer already enforces *shape-level* rules (field-class
registry, denylist regexes). This module audits what shapes cannot:
the actual VALUES, their lineage, and their availability semantics.

Research-only: all inputs are candidate matrices on synthetic or later
gated data; a passing audit means *admissible for research*, nothing
more.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone

# Field classes legal in an occurrence matrix (rubric §1).
OCCURRENCE_FIELD_CLASSES = frozenset({
    "meteorological_reforecast",
    "meteorological_archived_operational",
    "terrain_static",
    "cryosphere_state",
    "hydrology_state",
    "observation_metadata",
    "catalog_label",          # label channel only — never a predictor
})

# Classes that must never appear in occurrence-mode predictors.
EXPOSURE_CLASSES = frozenset({"exposure", "impact"})

# Name-layer denylists (rubric §2.1 / §2.2).
_EXPOSURE_NAME = re.compile(
    r"ghsl|worldpop|population|built_?up|building_count|fatalit|deaths?|"
    r"damage|loss|affected_population|exposure|impact|road_exposure|"
    r"settlement", re.I)
_B_SERIES_NAME = re.compile(
    r"priority|rank|ranked|ranking|top_?\d+|screen_score|screen_rank|"
    r"b_score|exposure_rank", re.I)

# Sentinel sentinels that must be declared per column (rubric §2.6).
_DEFAULT_SENTINELS = frozenset({-999.0, 9999.0, 1e20})

# Synonym map for renamed-leakage detection: normalized token ->
# canonical concept. A column whose normalized name lands here AND
# whose declared class disagrees with the concept's class is suspect.
_SYNONYM_CLASS = {
    "population": "exposure", "pop": "exposure",
    "building": "exposure", "settlement": "exposure",
    "fatality": "impact", "deaths": "impact",
    "damage": "impact", "loss": "impact",
    "rank": "b_series", "priority": "b_series", "score": "b_series",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _parse(iso: str) -> datetime:
    dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"naive timestamp rejected: {iso!r}")
    return dt.astimezone(timezone.utc)


def _norm_name(name: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", name.lower()) if t]


@dataclass(frozen=True)
class ColumnAudit:
    """Audit record for one column (rubric §1 fields)."""
    column_name: str
    declared_field_class: str
    source_lineage: tuple          # (source_id, version, transform...)
    availability_semantics: str    # rule making per-value availability
    unit: str
    value_domain: str              # declared legal range description
    temporal_window: tuple         # (start_iso, end_iso) of source obs
    declared_sentinels: tuple = ()
    missingness_policy: str = ""   # declared NaN handling


@dataclass(frozen=True)
class Verdict:
    column_name: str
    verdict: str                   # pass / reject / censored
    checks_fired: tuple
    reasons: tuple


def _is_rank_like(values: list) -> bool:
    """Dense monotonic integer sequence -> rank-shaped column."""
    try:
        nums = [float(v) for v in values]
    except (TypeError, ValueError):
        return False
    if any(math.isnan(v) or not v.is_integer() for v in nums):
        return False
    s = sorted(set(nums))
    return len(s) >= 3 and all(
        s[i + 1] - s[i] == 1 for i in range(len(s) - 1))


def _spearman_abs(a: list, b: list) -> float:
    """|Spearman rho| between two equal-length value vectors."""
    if len(a) != len(b) or len(a) < 3:
        return 0.0
    try:
        fa = [float(v) for v in a]
        fb = [float(v) for v in b]
    except (TypeError, ValueError):
        return 0.0
    if any(math.isnan(v) for v in fa + fb):
        return 0.0

    def ranks(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        i = 0
        while i < len(order):
            j = i
            while j + 1 < len(order) and v[order[j + 1]] == v[order[i]]:
                j += 1
            for k in range(i, j + 1):
                r[order[k]] = (i + j) / 2.0
            i = j + 1
        return r

    ra, rb = ranks(fa), ranks(fb)
    n = len(ra)
    d2 = sum((ra[i] - rb[i]) ** 2 for i in range(n))
    denom = n * (n * n - 1)
    if denom == 0:
        return 0.0
    return abs(1.0 - 6.0 * d2 / denom)


def _ordering_tau1(a: list, b: list) -> bool:
    """True if column values exactly reproduce an ordering (Kendall τ=1):
    i.e., sorting by a sorts b monotonically (or reverse)."""
    if len(a) != len(b) or len(a) < 3:
        return False
    pairs = sorted(zip(a, b), key=lambda p: p[0])
    bs = [p[1] for p in pairs]
    asc = all(bs[i] <= bs[i + 1] for i in range(len(bs) - 1))
    desc = all(bs[i] >= bs[i + 1] for i in range(len(bs) - 1))
    return asc or desc


def audit_matrix(columns: dict[str, list],
                 audits: list[ColumnAudit],
                 *,
                 cutoff_iso: str | None,
                 forecast_issue_iso: str | None = None,
                 exposure_proxies: dict[str, list] | None = None,
                 b_orderings: dict[str, list] | None = None,
                 preprocessing_provenance: dict[str, str] | None = None,
                 catalog_label_columns: set[str] | None = None,
                 feature_digest_set: set[str] | None = None,
                 train_row_count: int | None = None,
                 preprocessing_fit_rows: int | None = None,
                 ) -> list[Verdict]:
    """Run the rubric's value-level checks over a candidate matrix.

    Parameters:
        columns: {column_name: [values...]}
        audits: per-column ColumnAudit metadata — every column must have
            one; a missing audit fails closed.
        cutoff_iso: prediction cutoff; values/windows on or after it are
            post-event (rubric §2.3). None -> temporal check skipped
            only if no temporal_window declared (then the column fails
            for missing availability semantics instead).
        forecast_issue_iso: issue time; production timestamps after it
            reject (§2.3).
        exposure_proxies: {name: values} bound exposure columns to
            correlate against (§2.1).
        b_orderings: {name: values} bound B-artifact orderings for the
            Kendall-τ=1 check (§2.2).
        preprocessing_provenance: {column: "train_only"|"includes_test"}
            — anything not declared train_only rejects (§2.5).
        catalog_label_columns: caller-declared label channels —
            UNIONED with every audit declaring
            declared_field_class="catalog_label"; a label channel
            appearing in feature_digest_set rejects (B17).  Omitting
            this parameter no longer bypasses the check: the audits
            themselves carry the declaration.
        feature_digest_set: the predictor digest set under audit.
        preprocessing_fit_rows: rows the scaler/imputer saw; must equal
            train_row_count when provided (§2.5).
    """
    audits_by_name = {a.column_name: a for a in audits}
    # NEW-FMX-01: the label-channel set is derived from the audits
    # themselves, unioned with any caller-supplied declaration — a
    # caller can no longer bypass the label/predictor separation by
    # omitting catalog_label_columns.
    cat_labels = set(catalog_label_columns or set())
    cat_labels |= {a.column_name for a in audits
                   if a.declared_field_class == "catalog_label"}
    digest_set = feature_digest_set or set()
    exposure = exposure_proxies or {}
    b_ord = b_orderings or {}
    prov = preprocessing_provenance or {}
    verdicts = []

    for name, values in columns.items():
        fired, reasons = [], []
        audit = audits_by_name.get(name)

        # ---- missing audit record fails closed -------------------
        if audit is None:
            verdicts.append(Verdict(name, "reject", ("A-FIELDS",),
                                    ("no per-column audit record — "
                                     "fail closed",)))
            continue

        # ---- class legality ---------------------------------------
        if audit.declared_field_class not in OCCURRENCE_FIELD_CLASSES:
            fired.append("CLASS-ILLEGAL")
            reasons.append(
                f"declared_field_class {audit.declared_field_class!r} "
                f"not an occurrence class")
        if audit.declared_field_class in EXPOSURE_CLASSES:
            fired.append("CLASS-EXPOSURE")
            reasons.append("exposure/impact class in occurrence matrix")

        # ---- name layer --------------------------------------------
        if _EXPOSURE_NAME.search(name):
            fired.append("NAME-EXPOSURE")
            reasons.append("exposure/impact denylist name")
        if _B_SERIES_NAME.search(name):
            fired.append("NAME-B")
            reasons.append("B-series denylist name")

        # ---- renamed/disguised leakage ------------------------------
        toks = _norm_name(name)
        hinted = {_SYNONYM_CLASS[t] for t in toks if t in _SYNONYM_CLASS}
        if "exposure" in hinted and \
                audit.declared_field_class not in EXPOSURE_CLASSES:
            fired.append("RENAMED-EXPOSURE")
            reasons.append(
                "normalized name implies exposure concept but column "
                "is declared as a physical class")
        if "b_series" in hinted and not _B_SERIES_NAME.search(name):
            fired.append("RENAMED-B")
            reasons.append(
                "normalized name implies rank/score concept under a "
                "denylist-evading header")
        for pname, pvals in exposure.items():
            if _spearman_abs(values, pvals) > 0.9:
                fired.append("VALUE-EXPOSURE")
                reasons.append(
                    f"|Spearman|>0.9 vs bound exposure proxy {pname!r}")
        for bname, bvals in b_ord.items():
            if _ordering_tau1(values, bvals):
                fired.append("VALUE-B-ORDER")
                reasons.append(
                    f"values reproduce ordering of B artifact {bname!r}")
        if _is_rank_like(values) and \
                audit.declared_field_class not in {"observation_metadata"}:
            fired.append("VALUE-RANK-SHAPE")
            reasons.append("dense monotonic integer sequence in a "
                           "non-rank field class")

        # ---- catalog_label channel -----------------------------------
        # A column whose own audit declares catalog_label sits inside
        # the audited matrix: a label channel inside a predictor
        # matrix rejects outright, regardless of what the caller
        # declared.  When feature_digest_set is supplied the
        # membership check additionally fires LABEL-IN-PREDICTORS.
        if audit.declared_field_class == "catalog_label":
            fired.append("LABEL-CLASS-IN-MATRIX")
            reasons.append("catalog_label-classed column present in "
                           "the audited predictor matrix — a label "
                           "channel is never a predictor")
        if name in cat_labels and name in digest_set:
            fired.append("LABEL-IN-PREDICTORS")
            reasons.append("catalog_label column inside the feature "
                           "digest set (B17)")

        # ---- lineage / availability ---------------------------------
        if not audit.source_lineage:
            fired.append("LINEAGE-MISSING")
            reasons.append("empty source_lineage")
        if not audit.availability_semantics:
            fired.append("AVAIL-MISSING")
            reasons.append("availability_semantics not declared — "
                           "per-value availability uncheckable")

        # ---- required metadata (NEW-FMX-02) ---------------------------
        # Every column audit must carry a non-empty unit, value_domain,
        # a two-ended strict-UTC temporal_window, and a declared
        # missingness_policy — a column without them cannot be placed
        # on the availability or legality surfaces at all.
        if not audit.unit or not str(audit.unit).strip():
            fired.append("UNIT-MISSING")
            reasons.append("unit not declared — values are "
                           "dimensionally uninterpretable")
        if not audit.value_domain or \
                not str(audit.value_domain).strip():
            fired.append("DOMAIN-MISSING")
            reasons.append("value_domain not declared — the legal "
                           "range is uncheckable")
        # The per-column missingness vocabulary has no declared enum
        # in the codebase (regimes.MISSINGNESS_POLICIES is the
        # dataset-level set and does not cover the column-level
        # "forbid_nan" already honored below), so the requirement is
        # a non-empty declared string.
        if not audit.missingness_policy or \
                not str(audit.missingness_policy).strip():
            fired.append("MISSINGNESS-MISSING")
            reasons.append("missingness_policy not declared — NaN "
                           "handling is uncheckable")

        # ---- temporal checks -----------------------------------------
        w_start = w_end = None
        window = audit.temporal_window
        if not isinstance(window, (tuple, list)) or len(window) != 2:
            fired.append("WINDOW-MISSING")
            reasons.append("temporal_window must be a "
                           "(start_iso, end_iso) pair")
        else:
            try:
                w_start = _parse(str(window[0]))
                w_end = _parse(str(window[1]))
            except (TypeError, ValueError, AttributeError):
                fired.append("WINDOW-MISSING")
                reasons.append("temporal_window bounds must parse as "
                               "strict UTC timestamps")
        if w_start is not None and w_end is not None:
            if w_start > w_end:
                fired.append("WINDOW-ORDER")
                reasons.append(
                    f"temporal_window start {window[0]} is after "
                    f"end {window[1]}")
            if cutoff_iso and w_end > _parse(cutoff_iso):
                fired.append("POST-CUTOFF")
                reasons.append(
                    f"temporal_window end {window[1]} "
                    f"after cutoff {cutoff_iso}")
            if forecast_issue_iso and \
                    w_end > _parse(forecast_issue_iso):
                fired.append("POST-ISSUE")
                reasons.append("source observation window ends after "
                               "the issue time")

        # ---- locked-test tuning --------------------------------------
        if prov.get(name, "train_only") != "train_only":
            fired.append("TEST-TUNED")
            reasons.append("preprocessing/derivation not declared "
                           "train_only")
        if (train_row_count is not None and
                preprocessing_fit_rows is not None and
                preprocessing_fit_rows > train_row_count):
            fired.append("PREP-FIT-LEAK")
            reasons.append("preprocessing fit rows exceed train rows")

        # ---- integrity ------------------------------------------------
        declared = set(float(s) for s in audit.declared_sentinels)
        for v in values:
            try:
                fv = float(v)
            except (TypeError, ValueError):
                continue
            if fv in _DEFAULT_SENTINELS and fv not in declared:
                fired.append("SENTINEL-UNDECLARED")
                reasons.append(f"undeclared sentinel {fv} present")
                break
        if audit.missingness_policy == "forbid_nan":
            if any(isinstance(v, float) and math.isnan(v)
                   for v in values):
                fired.append("NAN-UNDECLARED")
                reasons.append("non-finite value outside declared "
                               "missingness policy")

        if fired:
            verdicts.append(Verdict(name, "reject", tuple(fired),
                                    tuple(reasons)))
        else:
            verdicts.append(Verdict(name, "pass", ("ALL-CLEAN",),
                                    ("no violation detected",)))

    # NEW-FMX-01: a declared label channel named inside the feature
    # digest set rejects even when the channel is not among the
    # audited columns — the digest set, not the column map, is the
    # predictor surface under audit.
    for lname in sorted((cat_labels & digest_set) - set(columns)):
        verdicts.append(Verdict(
            lname, "reject", ("LABEL-IN-PREDICTORS",),
            ("declared catalog_label channel inside the feature "
             "digest set (B17)",)))
    return verdicts
