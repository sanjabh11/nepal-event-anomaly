"""Deterministic synthetic fixture generators (Run B).

Every generator is pure and deterministic: same seed -> identical
payload.  No wall-clock reads, no network, no randomness outside the
declared seed, no real event data.  Payloads are JSON-able dicts in
``to_dict()`` shape with a ``record_type`` tag so they round-trip
through ``nepal.research_v0.records.deserialize_record``.

Naming convention: ``synthetic_*`` returns a clean, contract-valid
payload; ``with_<violation>`` variants inject exactly one named defect
so audit-rubric and gate tests can assert rejection paths.

These fixtures are NEVER real-data substitutes: no value here
corresponds to any observed event, location, or measurement.
"""
from __future__ import annotations

import hashlib
import math
import random
from datetime import datetime, timedelta, timezone
from typing import Any

_BASINS = ("koshi", "gandaki", "karnali", "mahakali", "bagmati")
_BASE = datetime(2020, 6, 1, tzinfo=timezone.utc)
_SOURCE_ID = "synthetic_inventory_v0"
_SOURCE_VERSION = "0.0.0-synthetic"


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _rng(seed: int, *salt: str) -> random.Random:
    """Deterministic per-call RNG: seed + salt -> stable stream."""
    return random.Random(int(_sha(f"{seed}:{':'.join(salt)}")[:16], 16))


def synthetic_source_record(seed: int = 0) -> dict[str, Any]:
    """A CANDIDATE_ONLY SourceRecordV0-shaped payload (synthetic)."""
    return {
        "record_type": "SourceRecordV0",
        "source_id": _SOURCE_ID,
        "provider": "synthetic-fixture-generator",
        "doi_or_url": "synthetic://fixture/source",
        "version": _SOURCE_VERSION,
        "as_of_date": "2020-06-01",
        "license_id": "synthetic-fixture-license",
        "redistribution": "synthetic-only",
        "geography": "synthetic-basins",
        "temporal_coverage": "2020 synthetic window",
        "event_time_class": "EXACT_DAY",
        "spatial_semantics": "synthetic geometry",
        "observation_method": "synthetic enumeration",
        "non_event_frame": "synthetic frame",
        "update_cadence": "none",
        "access_status": "synthetic",
        "posture": "CANDIDATE_ONLY",
        "license_notes": "synthetic fixture payload",
        "evidence_sidecar_path": "",
        "evidence_sidecar_sha256": "",
        "evidence_as_of": "",
        "evidence_review_state": "UNREVIEWED",
    }


def synthetic_event_labels(
    seed: int = 0, count: int = 9
) -> list[dict[str, Any]]:
    """EXACT_DAY EventLabelV0-shaped payloads spread over >=3 basins.

    Every label: one-day interval with uncertainty_seconds == 86400
    (covers the whole bracket, class-consistent), two unique
    reviewers, adjudicated.  ``count`` is rounded up to a multiple of
    3 so every basin gets an equal share.
    """
    if count < 3:
        count = 3
    count = int(math.ceil(count / 3.0) * 3)
    labels: list[dict[str, Any]] = []
    for i in range(count):
        basin = _BASINS[i % 3]  # koshi, gandaki, karnali cycle
        day = _BASE + timedelta(days=i * 7)
        eid = f"{_SOURCE_ID}:{_SOURCE_VERSION}:row-{i:03d}"
        labels.append({
            "record_type": "EventLabelV0",
            "event_id": eid,
            "vertical_id": "snow_avalanche",
            "source_id": _SOURCE_ID,
            "source_version": _SOURCE_VERSION,
            "event_time_start": _iso(day),
            "event_time_end": _iso(day + timedelta(days=1)),
            "uncertainty_seconds": 86400.0,
            "event_time_precision": "day",
            "event_time_basis": "synthetic-day-precision",
            "geometry_role": "slope_generalized",
            "coordinate_uncertainty": "synthetic",
            "latitude": 27.0 + 0.5 * (i % 3),
            "longitude": 84.0 + 1.0 * (i % 3),
            "basin_id": basin,
            "cascade_group_id": "",
            "parent_event_id": "",
            "duplicate_of": "",
            "adjudication_state": "TWO_REVIEW_AGREE",
            "adjudication_notes": "synthetic",
            "reviewer_ids": ("rev-synthetic-a", "rev-synthetic-b"),
        })
    return labels


def synthetic_opportunities(
    seed: int = 0, count: int = 6
) -> list[dict[str, Any]]:
    """OBSERVED_FULL ObservationOpportunityV0-shaped payloads."""
    opps: list[dict[str, Any]] = []
    for i in range(count):
        ws = _BASE + timedelta(days=i * 14)
        opps.append({
            "record_type": "ObservationOpportunityV0",
            "opportunity_id": f"opp-synthetic-{i:03d}",
            "unit_id": f"unit-{i % 3:02d}",
            "platform": "synthetic-platform",
            "window_start": _iso(ws),
            "window_end": _iso(ws + timedelta(days=14)),
            "coverage_fraction": 1.0,
            "coverage_quality": "synthetic-full",
            "detection_threshold": "synthetic",
            "state": "OBSERVED_FULL",
            "source_id": _SOURCE_ID,
            "source_as_of": "2020-06-01",
            "frame_ids": (f"frame-{i:03d}-a", f"frame-{i:03d}-b"),
        })
    return opps


def synthetic_control_windows(
    opportunities: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """NEGATIVE ControlWindowV0-shaped payloads derived from the
    supplied OBSERVED_FULL opportunities (state consistency required)."""
    opps = opportunities or synthetic_opportunities()
    controls: list[dict[str, Any]] = []
    for i, opp in enumerate(opps):
        controls.append({
            "record_type": "ControlWindowV0",
            "control_id": f"ctl-synthetic-{i:03d}",
            "unit_id": opp["unit_id"],
            "window_start": opp["window_start"],
            "window_end": opp["window_end"],
            "opportunity_id": opp["opportunity_id"],
            "opportunity_state": opp["state"],
            "state": "NEGATIVE" if opp["state"] == "OBSERVED_FULL"
                     else "CENSORED_OR_AMBIGUOUS",
            "matched_covariates": ("basin", "season"),
            "cascade_group_id": "",
        })
    return controls


def synthetic_holdout_plan(
    labels: list[dict[str, Any]] | None = None,
    embargo_seconds: float = 2592000.0,
) -> dict[str, Any]:
    """A HoldoutPlanV0-shaped payload with complete event coverage.

    Assignment is deterministic by basin: koshi -> train, gandaki ->
    validation, karnali -> test.  Evaluation regions are two named
    test groups — never a single box.
    """
    labels = labels or synthetic_event_labels()
    assignments = {
        lab["event_id"]: {
            "koshi": "koshi_train",
            "gandaki": "gandaki_validation",
            "karnali": "karnali_test",
        }[lab["basin_id"]]
        for lab in labels
    }
    return {
        "record_type": "HoldoutPlanV0",
        "holdout_plan_id": "holdout-synthetic-0",
        "assignment_rule": "basin",
        "train_groups": ("koshi_train",),
        "validation_groups": ("gandaki_validation",),
        "test_groups": ("karnali_test", "karnali_test_west"),
        "event_assignments": {**assignments,
                              f"{_SOURCE_ID}:{_SOURCE_VERSION}:row-extra":
                              "karnali_test_west"},
        "evaluation_region_names": ("karnali_test", "karnali_test_west"),
        "assigned_before_filtering": True,
        "test_locked": True,
        "embargo_seconds": embargo_seconds,
    }


def synthetic_cutoff(
    event_id: str = "", source_id: str = _SOURCE_ID,
    vintage_id: str = "",
) -> dict[str, Any]:
    """A fully-ordered CutoffRecordV0-shaped payload (synthetic)."""
    base = _BASE
    cutoff = {
        "record_type": "CutoffRecordV0",
        "cutoff_id": f"cutoff-synthetic-{_sha(event_id)[:8]}",
        "source_observation_end": _iso(base),
        "source_processing_complete": _iso(base + timedelta(hours=1)),
        "source_publication": _iso(base + timedelta(hours=2)),
        "feature_availability": _iso(base + timedelta(hours=3)),
        "forecast_initialization": _iso(base + timedelta(hours=4)),
        "forecast_issue": _iso(base + timedelta(hours=5)),
        "forecast_valid_start": _iso(base + timedelta(days=1)),
        "forecast_valid_end": _iso(base + timedelta(days=1, hours=6)),
        "archive_availability": _iso(base + timedelta(hours=6)),
        "local_retrieval_time": _iso(base + timedelta(hours=7)),
        "forecast_vintage_id": vintage_id,
        "source_id": source_id,
        "event_id": event_id,
        "event_time_start": _iso(base - timedelta(days=1)),
        "event_time_end": _iso(base),
    }
    return cutoff


def synthetic_feature_matrix(
    seed: int = 0, rows: int = 24, cols: int = 4
) -> dict[str, Any]:
    """A deterministic synthetic feature matrix for FMX-rubric tests.

    Returns a dict with ``columns`` (name -> declared field_class) and
    ``rows`` (list of dicts).  Values are smooth deterministic
    functions of (row, col, seed) — never sampled from real data.
    """
    names = [f"met_{c}" for c in range(cols)]
    field_classes = {n: "meteorological_reforecast" for n in names}
    rng = _rng(seed, "fmx")
    offset = rng.random()
    data = []
    for r in range(rows):
        row = {}
        for c, n in enumerate(names):
            row[n] = round(
                math.sin(0.3 * r + c + offset) + 0.01 * r * (c + 1), 6)
        data.append(row)
    return {"columns": field_classes, "rows": data}


def with_exposure_contamination(
    matrix: dict[str, Any],
) -> dict[str, Any]:
    """Inject one exposure-class column (rubric §2.1 violation)."""
    out = {"columns": dict(matrix["columns"]),
           "rows": [dict(r) for r in matrix["rows"]]}
    out["columns"]["population_density"] = "meteorological_reforecast"
    for i, row in enumerate(out["rows"]):
        row["population_density"] = float(100 + 7 * i)
    return out


def with_b_rank_leakage(
    matrix: dict[str, Any],
) -> dict[str, Any]:
    """Inject a monotonic rank column (rubric §2.2 violation)."""
    out = {"columns": dict(matrix["columns"]),
           "rows": [dict(r) for r in matrix["rows"]]}
    out["columns"]["priority_rank"] = "meteorological_reforecast"
    for i, row in enumerate(out["rows"]):
        row["priority_rank"] = float(i + 1)
    return out


def synthetic_regime_input(
    seed: int = 0, n_per_cluster: int = 60, k: int = 2, dim: int = 3
) -> list[list[float]]:
    """Deterministic Gaussian blobs for GMM stability tests.

    ``k`` well-separated blobs, ``dim`` features, fixed centers derived
    from the seed.  Enough structure for a K=1-vs-K=2 contrast, none
    of it real.
    """
    rng = _rng(seed, "regime")
    centers = [[(c + 1) * 4.0 + d for d in range(dim)]
               for c in range(k)]
    pts: list[list[float]] = []
    for c in range(k):
        for _ in range(n_per_cluster):
            pts.append([centers[c][d] + rng.gauss(0.0, 0.5)
                        for d in range(dim)])
    rng.shuffle(pts)
    return pts


__all__ = [
    "synthetic_source_record",
    "synthetic_event_labels",
    "synthetic_opportunities",
    "synthetic_control_windows",
    "synthetic_holdout_plan",
    "synthetic_cutoff",
    "synthetic_feature_matrix",
    "with_exposure_contamination",
    "with_b_rank_leakage",
    "synthetic_regime_input",
]
