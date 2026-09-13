"""Feature-Matrix Contract (FMX) — explicit blocked boundary (FMX-*).

The current environment has **no independently frozen environmental feature
matrix** — the dirty-main CSV/NetCDF is treated as absent and is never
inspected, hashed, loaded, or schema-validated by this module (the builder
accepts metadata mappings only; no path parameter exists).

Statuses:

* ``FMX_BLOCKED_PENDING_EXPLICIT_FREEZE`` — the only status this tranche
  can emit for the real environment;
* ``FMX_SCHEMA_VALID`` — metadata schema passes (informational);
* ``FMX_READY`` — only reachable when a future **externally supplied**
  write-once freeze token is bound to the exact matrix, producer,
  feature-contract, and preregistration digests.  This module provides NO
  token-generation or override path.

Contract rules:

* typed columns: name, unit, aggregation, role, temporal resolution,
  availability time;
* full digest bindings (source, producer, matrix, feature-contract,
  preregistration, generation, byte count) required for ``FMX_READY``;
* portable envelopes reject absolute paths and ``..`` traversal;
* B leakage rejected: no ranked arrays, priority scores, B-derived column
  names, or post-event aggregations as features;
* no ``if file exists`` fallback — absence is an explicit blocked state.
"""
from __future__ import annotations

import re
from typing import Any, Mapping, Optional

from .provenance import bind_artifact_envelope, verify_artifact_envelope

FMX_ENVELOPE_TYPE = "FEATURE_MATRIX_CONTRACT_V1"
FMX_SCHEMA_VALID = "FMX_SCHEMA_VALID"
FMX_READY = "FMX_READY"
FMX_BLOCKED_PENDING_EXPLICIT_FREEZE = "FMX_BLOCKED_PENDING_EXPLICIT_FREEZE"
FMX_STATUSES = (FMX_SCHEMA_VALID, FMX_READY,
                FMX_BLOCKED_PENDING_EXPLICIT_FREEZE)

FREEZE_TOKEN_TYPE = "FMX_EXTERNAL_FREEZE_TOKEN_V1"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_COLUMN_FIELDS = ("name", "unit", "role", "aggregation",
                  "temporal_resolution", "availability_time")
_FORBIDDEN_COLUMN_TOKENS = ("priority", "rank", "ranked", "b_screen",
                            "priority_index", "score_b")
_FORBIDDEN_AGGREGATIONS = ("post_event", "post-event", "after_event")
_FORBIDDEN_SOURCE_TOKENS = ("b_screen", "ranked", "priority",
                            "dirty", "main_checkout")


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


def _check_paths(payload: Mapping[str, Any], problems: list[str]) -> None:
    for dotted, value in _iter_strings(
            {k: v for k, v in payload.items()
             if k != "artifact_sha256"}):
        if value.startswith("/"):
            problems.append(f"absolute path in portable FMX field "
                            f"{dotted!r}")
        elif ".." in value.split("/"):
            problems.append(f"traversal in portable FMX field {dotted!r}")


def _check_columns(matrix: Mapping[str, Any], problems: list[str]) -> None:
    columns = matrix.get("columns")
    if not isinstance(columns, list) or not columns:
        problems.append("matrix.columns must be a non-empty list")
        return
    for i, col in enumerate(columns):
        label = f"matrix.columns[{i}]"
        if not isinstance(col, Mapping):
            problems.append(f"{label} must be a mapping")
            continue
        for field in _COLUMN_FIELDS:
            if field not in col:
                problems.append(f"{label}.{field} is required")
        name = str(col.get("name", "")).lower()
        if any(tok in name for tok in _FORBIDDEN_COLUMN_TOKENS):
            problems.append(
                f"{label}: column name {col.get('name')!r} is a "
                "B-derived ranked/priority feature and is rejected")
        unit = str(col.get("unit", "")).lower()
        if any(tok in unit for tok in _FORBIDDEN_COLUMN_TOKENS):
            problems.append(
                f"{label}: unit {col.get('unit')!r} describes a "
                "B-derived ranked/priority value and is rejected")
        agg = str(col.get("aggregation", "")).lower()
        if any(tok in agg for tok in _FORBIDDEN_AGGREGATIONS):
            problems.append(
                f"{label}: aggregation {col.get('aggregation')!r} is "
                "post-event leakage and is rejected")
        at = col.get("availability_time")
        if "availability_time" in col and not (
                isinstance(at, str) and _DATE_RE.fullmatch(at)):
            problems.append(f"{label}.availability_time must be an ISO "
                            "YYYY-MM-DD date")


def _check_matrix_schema(matrix: Any, problems: list[str]) -> None:
    if not isinstance(matrix, Mapping):
        problems.append("matrix must be a mapping")
        return
    for field in ("matrix_id", "candidate_generation_id",
                  "source_artifact_id", "data_source_status"):
        if not isinstance(matrix.get(field), str) or not matrix[field]:
            problems.append(f"matrix.{field} is required")
    for field in ("source_sha256", "producer_sha256",
                  "feature_contract_sha256", "preregistration_sha256",
                  "matrix_sha256"):
        if not _is_sha256(matrix.get(field)):
            problems.append(f"matrix.{field} must be a lowercase SHA-256")
    if not isinstance(matrix.get("byte_count"), int) or \
            matrix.get("byte_count", 0) <= 0:
        problems.append("matrix.byte_count must be a positive integer")
    src = str(matrix.get("source_artifact_id", "")).lower()
    if any(tok in src for tok in _FORBIDDEN_SOURCE_TOKENS):
        problems.append("matrix.source_artifact_id names a B-derived or "
                        "dirty source and is rejected")
    missingness = matrix.get("missingness")
    if not isinstance(missingness, Mapping) or not isinstance(
            missingness.get("fraction"), (int, float)) or not \
            0.0 <= float(missingness.get("fraction", 1.0)) <= 1.0:
        problems.append("matrix.missingness.fraction must be in [0, 1]")
    dr = matrix.get("date_range")
    if not isinstance(dr, Mapping) or not (
            isinstance(dr.get("start"), str)
            and _DATE_RE.fullmatch(dr["start"])) or not (
            isinstance(dr.get("end"), str)
            and _DATE_RE.fullmatch(dr["end"])):
        problems.append("matrix.date_range requires ISO start/end")
    sc = matrix.get("spatial_coverage")
    if not isinstance(sc, Mapping) or not isinstance(
            sc.get("n_units"), int) or sc.get("n_units", 0) <= 0:
        problems.append("matrix.spatial_coverage.n_units must be a "
                        "positive integer")
    if not isinstance(matrix.get("target_spec"), Mapping) or not \
            matrix["target_spec"].get("definition"):
        problems.append("matrix.target_spec.definition is required")
    _check_columns(matrix, problems)


def _check_freeze_token(token: Any, matrix: Mapping[str, Any],
                        problems: list[str]) -> None:
    if not isinstance(token, Mapping):
        problems.append("freeze_token must be a mapping supplied by an "
                        "external freeze authority")
        return
    if token.get("token_type") != FREEZE_TOKEN_TYPE:
        problems.append(f"freeze_token.token_type must be "
                        f"{FREEZE_TOKEN_TYPE!r}")
    if token.get("write_once") is not True:
        problems.append("freeze_token.write_once must be true — token "
                        "reuse/override is forbidden")
    if not isinstance(token.get("issued_by"), str) or not \
            token["issued_by"]:
        problems.append("freeze_token.issued_by is required")
    for field in ("matrix_sha256", "producer_sha256",
                  "feature_contract_sha256", "preregistration_sha256"):
        if not _is_sha256(token.get(field)):
            problems.append(f"freeze_token.{field} must be a lowercase "
                            "SHA-256")
            continue
        if token[field] != matrix.get(field):
            problems.append(
                f"freeze_token.{field} does not match the bound matrix "
                f"{field} — a token is only valid for the exact matrix "
                "and producer it was issued against")


def build_fmx_envelope(matrix: Mapping[str, Any], *,
                       freeze_token: Optional[Mapping[str, Any]] = None
                       ) -> dict[str, Any]:
    """Build a self-hashed FMX envelope.

    With ``freeze_token=None`` the envelope is
    ``FMX_BLOCKED_PENDING_EXPLICIT_FREEZE`` — the only status the current
    environment can produce.  ``FMX_READY`` requires an externally supplied
    write-once token bound to the exact matrix/producer/contract/
    preregistration digests; schema-valid metadata alone never reaches
    ``FMX_READY``.
    """
    problems: list[str] = []
    _check_matrix_schema(matrix, problems)
    _check_paths(matrix, problems)
    if freeze_token is not None:
        _check_freeze_token(freeze_token, matrix, problems)
    if problems:
        raise ValueError("FMX envelope is not valid: "
                         + "; ".join(problems[:6]))
    status = (FMX_READY if freeze_token is not None
              else FMX_BLOCKED_PENDING_EXPLICIT_FREEZE)
    envelope: dict[str, Any] = {
        "envelope_type": FMX_ENVELOPE_TYPE,
        "fmx_status": status,
        "schema_status": FMX_SCHEMA_VALID,
        "matrix": dict(matrix),
        "research_diagnostic_only": True,
        "promotion_eligible": False,
        "production_authorized": False,
        "no_claims": [
            "feature-matrix schema/metadata only; not a scientific or "
            "operational data product",
            "no warning, production, or authority readiness"],
    }
    if freeze_token is not None:
        envelope["freeze_token"] = dict(freeze_token)
    return bind_artifact_envelope(envelope)


def bind_fmx_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Re-bind an existing FMX envelope dict (used to rebuild after a
    caller mutation in tests; performs no validation)."""
    return bind_artifact_envelope(dict(envelope))


def verify_fmx_envelope(payload: Any) -> tuple[bool, list[str]]:
    """Fail-closed FMX envelope verification: envelope self-hash, schema,
    path safety, status consistency, and freeze-token binding."""
    problems: list[str] = []
    ok, env_problems = verify_artifact_envelope(payload)
    if not ok:
        problems.extend(env_problems)
    if not isinstance(payload, Mapping):
        problems.append("FMX envelope must be a mapping")
        return False, problems
    if payload.get("envelope_type") != FMX_ENVELOPE_TYPE:
        problems.append(f"envelope_type must be {FMX_ENVELOPE_TYPE!r}")
    status = payload.get("fmx_status")
    if status not in FMX_STATUSES:
        problems.append(f"fmx_status {status!r} is not a known status")

    matrix = payload.get("matrix")
    schema_problems: list[str] = []
    _check_matrix_schema(matrix, schema_problems)
    problems.extend(schema_problems)

    token = payload.get("freeze_token")
    if status == FMX_READY:
        if token is None:
            problems.append("FMX_READY requires an externally supplied "
                            "freeze token")
        elif isinstance(matrix, Mapping):
            _check_freeze_token(token, matrix, problems)
    elif token is not None:
        problems.append("freeze_token is present but fmx_status is not "
                        "FMX_READY — inconsistent")
    _check_paths(payload, problems)
    return (not problems), problems
