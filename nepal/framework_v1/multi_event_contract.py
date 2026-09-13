"""Multi-Event Contract (MEC) — Framework-native research envelope (MEC-*).

A strict, self-hashed contract for multi-event research design.  In this
tranche only **synthetic fixture envelopes** are permitted: synthetic event
IDs, synthetic catalogs, and source-supported date metadata.  No live event
catalog, dirty-main path, or production event identity may be bound.

Contract invariants enforced by :func:`verify_mec_envelope`:

* ``profile_id`` = ``SCIENCE_CONTRACT_T2_RESEARCH``, ``research_only`` and
  ``research_diagnostic_only`` true;
* non-empty source catalog identity with catalog ID, source digest, asset
  IDs, and processing-script digest;
* unique non-empty synthetic event IDs, each with an event group, a row
  hash, and a holdout group;
* source-supported date specs only — ``day`` precision with provenance, or
  an explicit interval; artificial day-15 dates and any ``fallback``/imputed
  source are rejected;
* separate acquisition / publication / feature-availability / target
  windows with consistent ordering; feature availability may not extend
  past the event date;
* holdout assignment must happen **before** eligibility filtering, with
  temporal embargo, geographic separation, and group-disjoint event
  separation metadata;
* validation scope must span at least two geographic regions and two
  events — single-box and Langtang-only designs are rejected;
* no absolute paths in portable fields;
* canonical ``artifact_sha256`` self-hash verified on read.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from .provenance import bind_artifact_envelope, verify_artifact_envelope

MEC_ENVELOPE_TYPE = "MULTI_EVENT_CONTRACT_V1"
MEC_PROFILE_ID = "SCIENCE_CONTRACT_T2_RESEARCH"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_SYNTH_ID_PREFIX = "SYNTH-"
_FORBIDDEN_SCOPE_TOKENS = ("langtang", "30km")
_FORBIDDEN_DATE_SOURCES = ("fallback", "default", "imputed", "synthesized")
_FORBIDDEN_CATALOG_IDS = ("hma_events_all", "hma", "real", "production")

_WINDOW_KEYS = ("acquisition_window", "production_time",
                "publication_time", "issue_time",
                "feature_availability_time", "target_window")


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _iter_strings(obj: Any, prefix: str = ""):
    if isinstance(obj, str):
        yield prefix, obj
    elif isinstance(obj, Mapping):
        for key, value in obj.items():
            yield from _iter_strings(value, f"{prefix}{key}.")
    elif isinstance(obj, (list, tuple)):
        for index, value in enumerate(obj):
            yield from _iter_strings(value, f"{prefix}[{index}].")


def _parse_date(value: Any) -> Optional[tuple[int, int, int]]:
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return None
    try:
        y, m, d = (int(p) for p in value.split("-"))
        if not (1 <= m <= 12 and 1 <= d <= 31):
            return None
        return (y, m, d)
    except ValueError:
        return None


def _window_endpoints(value: Any, label: str, problems: list[str]
                      ) -> tuple[Optional[tuple], Optional[tuple]]:
    """Return (start, end) date tuples for a window spec, or record
    problems.  Accepts an ISO date string or {start, end} mapping."""
    if isinstance(value, str):
        d = _parse_date(value)
        if d is None:
            problems.append(f"{label} is not a valid ISO date")
            return None, None
        return d, d
    if isinstance(value, Mapping):
        start = _parse_date(value.get("start"))
        end = _parse_date(value.get("end"))
        if start is None or end is None:
            problems.append(f"{label} requires ISO start/end dates")
            return None, None
        if start > end:
            problems.append(f"{label} start is after end")
        return start, end
    problems.append(f"{label} must be an ISO date or {{start, end}}")
    return None, None


def _check_date_spec(spec: Any, label: str, problems: list[str]
                     ) -> Optional[tuple]:
    """Validate one event date_spec.  Returns the event date (or interval
    end) for post-event checks, or None on failure."""
    if not isinstance(spec, Mapping):
        problems.append(f"{label}.date_spec must be a mapping")
        return None
    source = spec.get("source")
    if not isinstance(source, str) or not source:
        problems.append(f"{label}.date_spec.source is required "
                        "(date provenance)")
    elif any(tok in source.lower() for tok in _FORBIDDEN_DATE_SOURCES):
        problems.append(f"{label}.date_spec.source {source!r} is a "
                        "fallback/imputed source; source-supported dates "
                        "are required")
    precision = spec.get("precision")
    event_ref: Optional[tuple] = None
    if precision == "day":
        d = _parse_date(spec.get("date"))
        if d is None:
            problems.append(f"{label}.date_spec.date must be an ISO "
                            "YYYY-MM-DD date for day precision")
        else:
            if d[2] == 15:
                problems.append(
                    f"{label}.date_spec uses an artificial day-15 date; "
                    "source-supported dates are required")
            event_ref = d
    elif precision == "interval":
        interval = spec.get("interval")
        if not isinstance(interval, Mapping):
            problems.append(f"{label}.date_spec.interval must be a "
                            "{start, end} mapping")
        else:
            start = _parse_date(interval.get("start"))
            end = _parse_date(interval.get("end"))
            if start is None or end is None:
                problems.append(f"{label}.date_spec.interval requires "
                                "ISO start/end dates")
            elif start > end:
                problems.append(f"{label}.date_spec.interval start is "
                                "after end")
            else:
                event_ref = end
    else:
        problems.append(f"{label}.date_spec.precision must be 'day' or "
                        f"'interval', got {precision!r}")
    return event_ref


def build_mec_envelope(payload: Mapping[str, Any]) -> dict[str, Any]:
    """Bind a MEC envelope with its canonical self-hash.  The payload is
    validated first — malformed envelopes cannot be bound."""
    ok, problems = verify_mec_envelope(payload, structural_only=True)
    if not ok:
        raise ValueError("MEC envelope is not valid: "
                         + "; ".join(problems[:5]))
    return bind_artifact_envelope(dict(payload))


def verify_mec_envelope(payload: Any, *,
                        structural_only: bool = False
                        ) -> tuple[bool, list[str]]:
    """Fail-closed MEC verification.  ``structural_only`` skips the
    envelope self-hash check so :func:`build_mec_envelope` can validate a
    payload before binding it."""
    problems: list[str] = []
    if not isinstance(payload, Mapping):
        return False, ["MEC envelope must be a mapping"]
    if not structural_only:
        ok, env_problems = verify_artifact_envelope(payload)
        if not ok:
            problems.extend(env_problems)

    if payload.get("envelope_type") != MEC_ENVELOPE_TYPE:
        problems.append(f"envelope_type must be {MEC_ENVELOPE_TYPE!r}")
    if payload.get("profile_id") != MEC_PROFILE_ID:
        problems.append(f"profile_id must be {MEC_PROFILE_ID!r}")
    if payload.get("research_only") is not True:
        problems.append("research_only must be true")
    if payload.get("research_diagnostic_only") is not True:
        problems.append("research_diagnostic_only must be true")
    if payload.get("synthetic_fixture") is not True:
        problems.append("synthetic_fixture must be true; live event data "
                        "is not permitted in this tranche")

    catalog = payload.get("source_catalog")
    if not isinstance(catalog, Mapping):
        problems.append("source_catalog must be a mapping")
        catalog = {}
    else:
        cid = catalog.get("catalog_id")
        if not isinstance(cid, str) or not cid:
            problems.append("source_catalog.catalog_id is required")
        elif any(tok in cid.lower() for tok in _FORBIDDEN_CATALOG_IDS):
            problems.append(f"source_catalog.catalog_id {cid!r} names a "
                            "live/production catalog; synthetic fixtures "
                            "only")
        if not _is_sha256(catalog.get("source_sha256")):
            problems.append("source_catalog.source_sha256 must be a "
                            "lowercase SHA-256")
        if not _is_sha256(catalog.get("processing_script_sha256")):
            problems.append("source_catalog.processing_script_sha256 must "
                            "be a lowercase SHA-256")
        if not isinstance(catalog.get("asset_ids"), list) or not \
                catalog["asset_ids"]:
            problems.append("source_catalog.asset_ids must be a "
                            "non-empty list")

    events = payload.get("events")
    if not isinstance(events, list) or not events:
        problems.append("events must be a non-empty list")
        events = []
    seen_ids: set[str] = set()
    for i, ev in enumerate(events):
        label = f"events[{i}]"
        if not isinstance(ev, Mapping):
            problems.append(f"{label} must be a mapping")
            continue
        eid = ev.get("event_id")
        if not isinstance(eid, str) or not eid:
            problems.append(f"{label}.event_id must be a non-empty string")
        else:
            if eid in seen_ids:
                problems.append(f"{label}.event_id {eid!r} is a duplicate")
            seen_ids.add(eid)
            if not eid.startswith(_SYNTH_ID_PREFIX) or \
                    ev.get("synthetic") is not True:
                problems.append(
                    f"{label}.event_id {eid!r} is not a synthetic fixture "
                    "ID; live/production event identity is forbidden")
        if not isinstance(ev.get("event_group_id"), str) or not \
                ev["event_group_id"]:
            problems.append(f"{label}.event_group_id is required")
        if not isinstance(ev.get("holdout_group"), str) or not \
                ev["holdout_group"]:
            problems.append(f"{label}.holdout_group is required")
        if not _is_sha256(ev.get("row_sha256")):
            problems.append(f"{label}.row_sha256 must be a lowercase "
                            "SHA-256")
        event_ref = _check_date_spec(ev.get("date_spec"), label, problems)
        windows = ev.get("windows")
        if not isinstance(windows, Mapping):
            problems.append(f"{label}.windows must be a mapping")
            windows = {}
        for wkey in windows:
            if wkey not in _WINDOW_KEYS:
                problems.append(f"{label}.windows.{wkey} is not a known "
                                "window key")
        for wkey in _WINDOW_KEYS:
            if wkey in windows:
                _ws, we = _window_endpoints(
                    windows[wkey], f"{label}.windows.{wkey}", problems)
                if wkey == "feature_availability_time" and we is not None \
                        and event_ref is not None and we > event_ref:
                    problems.append(
                        f"{label}.windows.feature_availability_time ends "
                        "after the event date — post-event features are "
                        "forbidden")

    holdout = payload.get("holdout")
    if not isinstance(holdout, Mapping):
        problems.append("holdout must be a mapping")
        holdout = {}
    else:
        if holdout.get("assigned_before_filtering") is not True:
            problems.append("holdout.assigned_before_filtering must be "
                            "true — holdout groups are fixed before "
                            "eligibility filtering")
        embargo = holdout.get("temporal_embargo_days")
        if not isinstance(embargo, int) or embargo < 0:
            problems.append("holdout.temporal_embargo_days must be a "
                            "non-negative integer")
        geo = holdout.get("geographic_holdout")
        if not isinstance(geo, Mapping) or not isinstance(
                geo.get("min_separation_km"), (int, float)) or \
                geo.get("min_separation_km", 0) <= 0:
            problems.append("holdout.geographic_holdout.min_separation_km "
                            "must be positive")
        sep = holdout.get("event_separation")
        if not isinstance(sep, Mapping) or sep.get("group_disjoint") \
                is not True:
            problems.append("holdout.event_separation.group_disjoint "
                            "must be true")

    scope = payload.get("validation_scope")
    if not isinstance(scope, Mapping):
        problems.append("validation_scope must be a mapping")
        scope = {}
    else:
        sid = scope.get("scope_id")
        if not isinstance(sid, str) or not sid:
            problems.append("validation_scope.scope_id is required")
        elif any(tok in sid.lower() for tok in _FORBIDDEN_SCOPE_TOKENS):
            problems.append(f"validation_scope.scope_id {sid!r} is a "
                            "single-box/Langtang-only design and is "
                            "rejected")
        if not isinstance(scope.get("n_geographic_regions"), int) or \
                scope["n_geographic_regions"] < 2:
            problems.append("validation_scope must span at least 2 "
                            "geographic regions")
        if not isinstance(scope.get("min_events"), int) or \
                scope["min_events"] < 2:
            problems.append("validation_scope must cover at least 2 "
                            "events")

    if not isinstance(payload.get("claim_scope"), str) or not \
            payload["claim_scope"]:
        problems.append("claim_scope is required")

    for dotted, value in _iter_strings(
            {k: v for k, v in payload.items()
             if k != "artifact_sha256"}):
        if value.startswith("/"):
            problems.append(f"absolute path in portable MEC field "
                            f"{dotted!r}")
            break
    return (not problems), problems
