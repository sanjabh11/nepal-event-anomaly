"""Real-data leakage audit primitives for the R14 scientific cube."""
from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping, Sequence

VOLATILE_KEYS = frozenset({"created_at", "evaluated_at", "retrieved_at", "path", "pid", "hostname", "temp_dir"})


def _strip_volatile(value: Any) -> Any:
    if isinstance(value, Mapping):
        return {k: _strip_volatile(v) for k, v in sorted(value.items()) if k not in VOLATILE_KEYS}
    if isinstance(value, list):
        return [_strip_volatile(v) for v in value]
    return value


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(_strip_volatile(value), sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def audit_feature_rows(rows: Sequence[Mapping[str, Any]], *, outcome_ids: set[str] | None = None) -> dict[str, Any]:
    """Audit leakage invariants from materialized rows, not metadata claims."""
    outcome_ids = outcome_ids or set()
    violations: list[str] = []
    temporal = spatial = footprint = outcome = duplicate = False
    seen_groups: dict[str, str] = {}
    seen_outcomes: set[str] = set()
    for index, row in enumerate(rows):
        if row.get("station_id") or row.get("snowpack_id") or row.get("bulletin_text"):
            violations.append(f"row[{index}]:station_or_snowpack_source")
        if row.get("outcome_evidence") is True or row.get("outcome_id") in outcome_ids:
            outcome = True
            violations.append(f"row[{index}]:outcome_in_predictors")
        if row.get("future_data") is True or row.get("post_cutoff") is True:
            temporal = True
            violations.append(f"row[{index}]:future_or_post_cutoff")
        if row.get("static_terrain_as_temporal") is True:
            violations.append(f"row[{index}]:static_terrain_contamination")
        dependency = row.get("dependency_group_id")
        split = row.get("split")
        if dependency and split:
            prior = seen_groups.get(str(dependency))
            if prior and prior != split:
                footprint = True
                violations.append(f"row[{index}]:dependency_group_crosses_split")
            seen_groups[str(dependency)] = str(split)
        event_id = row.get("duplicate_event_group")
        if event_id:
            if event_id in seen_outcomes:
                duplicate = True
                violations.append(f"row[{index}]:duplicate_event_crosses_rows")
            seen_outcomes.add(str(event_id))
        if row.get("spatial_overlap_with_holdout") is True:
            spatial = True
            violations.append(f"row[{index}]:spatial_holdout_overlap")
    return {
        "schema": "R14_REAL_DATA_LEAKAGE_AUDIT_V1",
        "row_count": len(rows),
        "temporal_leakage": temporal,
        "spatial_leakage": spatial,
        "forcing_footprint_leakage": footprint,
        "outcome_leakage": outcome,
        "duplicate_event_leakage": duplicate,
        "static_terrain_excluded_from_temporal": not any(r.get("static_terrain_as_temporal") is True for r in rows),
        "violations": violations,
        "passed": not violations,
        "materialized_rows_sha256": _digest(list(rows)),
        "research_only": True,
    }


__all__ = ["audit_feature_rows", "VOLATILE_KEYS"]
