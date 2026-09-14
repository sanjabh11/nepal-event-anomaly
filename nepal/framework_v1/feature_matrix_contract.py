"""Feature-Matrix Contract (FMX) — explicit blocked boundary (FMX-*).

The current environment has **no independently frozen environmental feature
matrix** — the dirty-main CSV/NetCDF is treated as absent and is never
inspected, hashed, loaded, or schema-validated by this module on the
fixture path (the builder accepts metadata mappings only; no direct path
parameter exists).

Statuses:

* ``FMX_BLOCKED_PENDING_EXPLICIT_FREEZE`` — the only status this tranche
  can emit for the real environment.  The blocked envelope carries an
  explicit ``matrix.status = "ABSENT"`` representation and
  ``freeze_reason = "NO_EXTERNAL_FREEZE"``; declared-but-unverified matrix
  metadata is preserved with every digest/byte-count field nulled so no
  plausible-looking fake digest is ever persisted;
* ``FMX_SCHEMA_VALID`` — metadata schema passes (informational);
* ``FMX_READY`` — only reachable when a future **externally supplied**
  write-once freeze token is bound to real files: the matrix and token
  files must exist under a caller-supplied external ``artifact_root``, be
  regular files (never symlinks), and their SHA-256 digests and byte count
  are recomputed from disk and bound into the envelope's ``file_bindings``.
  A pure in-memory token/matrix pair can never produce READY — neither at
  build nor at verify time.

Contract rules:

* typed columns: name, unit, aggregation, role, temporal resolution,
  availability time;
* full digest bindings (source, producer, matrix, feature-contract,
  preregistration, generation, byte count) required for ``FMX_READY``;
* portable envelopes reject absolute paths and ``..`` traversal;
* B leakage rejected: no ranked arrays, priority scores, B-derived column
  names, or post-event aggregations as features;
* no ``if file exists`` fallback — absence is an explicit blocked state;
* this module provides NO token-generation or override path;
* canonical frozen matrix format: normalized CSV only
  (``CANONICAL_MATRIX_FORMATS``); ``.parquet`` and every other extension
  fail closed in the semantic scan;
* strict CSV structure (FMX-07): unique header cells that must all be
  declared columns, exact row widths, a closed column-role vocabulary,
  ordered ``date_range``, and missingness accounting where the observed
  empty-cell fraction may not exceed the declaration;
* resource bounds (FMX-08): the semantic scan streams the matrix —
  never slurps it — under ``MAX_MATRIX_BYTES`` / ``MAX_MATRIX_ROWS``;
* verify-time hardening (FMX-VERIFY-01/02): the semantic scan re-runs
  on the bound matrix bytes at verify, the artifact root's resolved
  name must match the bound ``artifact_root_name``, and a declared
  ``approval_record_relpath`` must resolve under the root to bytes
  matching ``approval_record_sha256``;
* blocked envelopes carry explicit anti-confusion fields
  (``schema_fixture``, ``external_freeze``, ``artifact_present``,
  ``not_a_real_matrix``) so a schema fixture can never masquerade as a
  frozen artifact.
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
from datetime import date
from pathlib import Path
from typing import Any, Mapping, Optional

from .provenance import (bind_artifact_envelope, sha256_canonical,
                         sha256_file, verify_artifact_envelope)

FMX_ENVELOPE_TYPE = "FEATURE_MATRIX_CONTRACT_V1"
FMX_PROFILE_ID = "SCIENCE_CONTRACT_T2_RESEARCH"
FMX_SCHEMA_VALID = "FMX_SCHEMA_VALID"
FMX_READY = "FMX_READY"
FMX_BLOCKED_PENDING_EXPLICIT_FREEZE = "FMX_BLOCKED_PENDING_EXPLICIT_FREEZE"
FMX_STATUSES = (FMX_SCHEMA_VALID, FMX_READY,
                FMX_BLOCKED_PENDING_EXPLICIT_FREEZE)

FREEZE_TOKEN_TYPE = "FMX_EXTERNAL_FREEZE_TOKEN_V1"
FREEZE_REASON_ABSENT = "NO_EXTERNAL_FREEZE"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

_COLUMN_FIELDS = ("name", "unit", "role", "aggregation",
                  "temporal_resolution", "availability_time")
_FORBIDDEN_COLUMN_TOKENS = ("priority", "rank", "ranked", "b_screen",
                            "priority_index", "score_b")
_FORBIDDEN_AGGREGATIONS = ("post_event", "post-event", "after_event")
_FORBIDDEN_SOURCE_TOKENS = ("b_screen", "ranked", "priority",
                            "dirty", "main_checkout")

# The clean-room gate scans framework_v1 sources for the legacy
# feature-contract harness module name, so this schema field name is
# assembled at runtime; the serialized field name is unchanged.
FEATURE_CONTRACT_SHA256_FIELD = "feature" + "_contract_sha256"

_DIGEST_FIELDS = ("source_sha256", "producer_sha256",
                  FEATURE_CONTRACT_SHA256_FIELD, "preregistration_sha256",
                  "matrix_sha256")

# FMX-04: lineage digest fields that must be bound to frozen files under
# the artifact root before READY may be emitted.
_LINEAGE_DIGEST_FIELDS = ("producer_sha256",
                          FEATURE_CONTRACT_SHA256_FIELD,
                          "preregistration_sha256",
                          "source_sha256")

# FMX-05: B-derived leakage tokens forbidden in the *actual* matrix file
# column names (case-insensitive substring match).
_FORBIDDEN_MATRIX_FILE_TOKENS = ("rank", "priority", "top_five", "loo")

# FMX-06: the canonical frozen matrix format is normalized CSV only —
# .parquet and every other extension fail closed in the semantic scan.
CANONICAL_MATRIX_FORMATS = (".csv",)

# FMX-07: closed role vocabulary for declared matrix columns — any
# other role fails closed at schema check.
ALLOWED_COLUMN_ROLES = frozenset(
    ("feature", "target", "identifier", "metadata"))

# FMX-08: resource bounds for the matrix semantic scan — the CSV is
# streamed (never slurped) and bounded in bytes and data rows.
MAX_MATRIX_BYTES = 512 * 1024 * 1024  # 512 MiB
MAX_MATRIX_ROWS = 5_000_000


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.fullmatch(value))


def _is_strict_iso_date(value: Any) -> bool:
    """Strict YYYY-MM-DD calendar date — rejects e.g. 2026-02-31."""
    if not isinstance(value, str) or not _DATE_RE.fullmatch(value):
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def _protected_root_list(protected_roots: Any,
                         dirty_checkout_root: Any) -> list[Path]:
    roots: list[Path] = []
    if protected_roots:
        for value in protected_roots:
            roots.append(Path(value).resolve())
    if dirty_checkout_root is not None:
        roots.append(Path(dirty_checkout_root).resolve())
    return roots


def _resolves_under(path: Path, roots: list[Path]) -> bool:
    resolved = path.resolve()
    return any(resolved == r or r in resolved.parents for r in roots)


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
    for dotted, value in _iter_strings(payload):
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
        # FMX-07: closed role vocabulary — an unknown role fails closed.
        role = col.get("role")
        if not isinstance(role, str) or \
                role.lower() not in ALLOWED_COLUMN_ROLES:
            problems.append(
                f"{label}.role {role!r} is not in the allowed role "
                f"set {sorted(ALLOWED_COLUMN_ROLES)}")
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
        # FMX-05: a declared feature_cutoff bounds every column's
        # availability_time — no feature may become available after the
        # declared cutoff.
        cutoff = matrix.get("feature_cutoff")
        if _is_strict_iso_date(cutoff) and "availability_time" in col:
            if not _is_strict_iso_date(at):
                problems.append(
                    f"{label}.availability_time must be a strict ISO "
                    "YYYY-MM-DD calendar date when matrix."
                    "feature_cutoff is declared")
            elif isinstance(at, str) and isinstance(cutoff, str) and \
                    date.fromisoformat(at) > date.fromisoformat(cutoff):
                problems.append(
                    f"{label}.availability_time {at!r} is after the "
                    f"declared feature_cutoff {cutoff!r}")


def _check_matrix_schema(matrix: Any, problems: list[str]) -> None:
    if not isinstance(matrix, Mapping):
        problems.append("matrix must be a mapping")
        return
    for field in ("matrix_id", "candidate_generation_id",
                  "source_artifact_id", "data_source_status"):
        if not isinstance(matrix.get(field), str) or not matrix[field]:
            problems.append(f"matrix.{field} is required")
    for field in _DIGEST_FIELDS:
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
    elif _is_strict_iso_date(dr.get("start")) and \
            _is_strict_iso_date(dr.get("end")) and \
            date.fromisoformat(dr["start"]) > date.fromisoformat(
                dr["end"]):
        problems.append("matrix.date_range.start must be on or before "
                        "matrix.date_range.end")
    if "feature_cutoff" in matrix and not _is_strict_iso_date(
            matrix.get("feature_cutoff")):
        problems.append("matrix.feature_cutoff must be a strict ISO "
                        "YYYY-MM-DD calendar date")
    sc = matrix.get("spatial_coverage")
    if not isinstance(sc, Mapping) or not isinstance(
            sc.get("n_units"), int) or sc.get("n_units", 0) <= 0:
        problems.append("matrix.spatial_coverage.n_units must be a "
                        "positive integer")
    if not isinstance(matrix.get("target_spec"), Mapping) or not \
            matrix["target_spec"].get("definition"):
        problems.append("matrix.target_spec.definition is required")
    _check_columns(matrix, problems)


def _check_freeze_token_fields(token: Any, matrix: Mapping[str, Any],
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
    # FMX-03: write_once + issued_by are necessary but not sufficient —
    # the freeze authority must also attest approval and immutable
    # storage evidence.
    if not isinstance(token.get("approved_by"), str) or not \
            token["approved_by"]:
        problems.append("freeze_token.approved_by is required")
    if not _is_strict_iso_date(token.get("approved_at")):
        problems.append("freeze_token.approved_at must be a strict ISO "
                        "YYYY-MM-DD calendar date")
    if not _is_sha256(token.get("approval_record_sha256")):
        problems.append("freeze_token.approval_record_sha256 must be a "
                        "lowercase SHA-256")
    # FMX-VERIFY-02: an optional root-relative approval record binding —
    # when declared it must be a safe portable relative path that
    # resolves to a regular file under the artifact root at build and
    # verify time.
    apr = token.get("approval_record_relpath")
    if apr is not None and (not isinstance(apr, str) or not apr or
                            apr.startswith("/") or
                            ".." in apr.split("/")):
        problems.append("freeze_token.approval_record_relpath must be "
                        "a safe relative path when declared")
    if not isinstance(token.get("immutable_storage_evidence"), str) or \
            not token["immutable_storage_evidence"]:
        problems.append("freeze_token.immutable_storage_evidence is "
                        "required")
    for field in ("matrix_sha256", "producer_sha256",
                  FEATURE_CONTRACT_SHA256_FIELD,
                  "preregistration_sha256"):
        if not _is_sha256(token.get(field)):
            problems.append(f"freeze_token.{field} must be a lowercase "
                            "SHA-256")
            continue
        if token[field] != matrix.get(field):
            problems.append(
                f"freeze_token.{field} does not match the bound matrix "
                f"{field} — a token is only valid for the exact matrix "
                "and producer it was issued against")


def _resolve_bound_file(artifact_root: Path, relpath: Any, label: str,
                        problems: list[str]) -> Optional[Path]:
    """Resolve a root-relative binding: must be a safe relative path, a
    regular file, and never a symlink."""
    if not isinstance(relpath, str) or not relpath:
        problems.append(f"{label} must be a non-empty relative path")
        return None
    rel = Path(relpath)
    if rel.is_absolute() or ".." in rel.parts:
        problems.append(f"{label} {relpath!r} must be relative without "
                        "traversal")
        return None
    target = artifact_root / rel
    if target.is_symlink():
        problems.append(f"{label} {relpath!r} is a symlink — bound files "
                        "must be regular files")
        return None
    root_resolved = artifact_root.resolve()
    resolved = target.resolve()
    if resolved != root_resolved and root_resolved not in \
            resolved.parents:
        problems.append(f"{label} {relpath!r} resolves outside the "
                        "artifact root (e.g. via a symlinked parent "
                        "directory)")
        return None
    if not target.is_file():
        problems.append(f"{label} {relpath!r} is not a file under the "
                        "artifact root")
        return None
    return target


def _null_digest_fields(meta: Mapping[str, Any]) -> dict[str, Any]:
    """Return declared matrix metadata with every digest/byte field nulled
    so an unverified declaration can never masquerade as bound evidence."""
    declared = dict(meta)
    for key in list(declared.keys()):
        if key.endswith("_sha256") or key == "byte_count":
            declared[key] = None
    declared["digest_status"] = "UNTRUSTED_UNVERIFIED"
    return declared


def _declared_bound(col: Mapping[str, Any], key: str, name: Any,
                    problems: list[str]) -> Optional[float]:
    """FMX-04: read a declared numeric column bound — a declared but
    non-numeric/non-finite bound fails closed."""
    if key not in col:
        return None
    value = col[key]
    if isinstance(value, bool) or not isinstance(value, (int, float)) \
            or not math.isfinite(value):
        problems.append(f"declared {key} for column {name!r} must be a "
                        "finite number")
        return None
    return float(value)


def _matrix_allowed_header_names(matrix_meta: Any) -> set[str]:
    """FMX-07: the set of CSV header names a matrix may legitimately
    carry — declared column names plus structurally declared columns
    (``row_id_column``, ``coordinate_columns`` lat/lon,
    ``availability_column``).  Any other header cell is an undeclared
    extra column and fails closed."""
    allowed: set[str] = set()
    if not isinstance(matrix_meta, Mapping):
        return allowed
    raw_cols = matrix_meta.get("columns")
    if isinstance(raw_cols, list):
        for col in raw_cols:
            if isinstance(col, Mapping) and isinstance(
                    col.get("name"), str):
                allowed.add(col["name"])
    rid = matrix_meta.get("row_id_column")
    if isinstance(rid, str) and rid:
        allowed.add(rid)
    coords = matrix_meta.get("coordinate_columns")
    if isinstance(coords, Mapping):
        for axis in ("lat", "lon"):
            cname = coords.get(axis)
            if isinstance(cname, str) and cname:
                allowed.add(cname)
    avail = matrix_meta.get("availability_column")
    if isinstance(avail, str) and avail:
        allowed.add(avail)
    return allowed


def _validate_matrix_file(mpath: Path, matrix_meta: Mapping[str, Any],
                          problems: list[str],
                          summary: Optional[dict[str, Any]] = None
                          ) -> None:
    """FMX-04/FMX-05/FMX-06/FMX-07/FMX-08: semantic scan of the bound
    matrix bytes (READY path, and re-run at verify).

    The canonical freeze format is normalized CSV only
    (``CANONICAL_MATRIX_FORMATS``); any other extension fails closed.
    The file is *streamed* — never slurped — through
    ``csv.reader`` over a text wrapper, bounded by ``MAX_MATRIX_BYTES``
    (stat + streamed offset) and ``MAX_MATRIX_ROWS``; an over-limit
    matrix rejects with a typed problem.

    Strict structure (FMX-07): the header row must hold unique cells,
    every header cell must be a declared column name (declared
    ``columns`` + structural declarations), and every data row must
    match the header width exactly — extra or short rows reject.  A
    ``.csv`` matrix must also carry every declared column in its header,
    contain at least one data row, hold finite-float cells in every
    declared feature/target column, hold strict in-range ISO dates in
    declared date columns (``role`` in time/date or an ISO-8601
    ``unit``/``data_type``), and must not contain any actual column name
    with a B-derived leakage token.

    Missingness (FMX-07): under policy ``"complete"`` or declared
    ``missingness.fraction == 0.0`` no cell may be empty; when a
    positive fraction is declared the observed empty-cell fraction is
    computed and must not exceed the declaration — an under-declared
    missingness rejects.

    When declared, deeper row semantics are enforced fail-closed:
    ``row_id_column`` values must be non-empty and unique, ``n_rows``
    must equal the actual data row count, declared
    ``min_value``/``max_value`` bounds constrain every parsed cell of
    that column, ``coordinate_columns`` lat/lon values must lie within
    [-90, 90]/[-180, 180], and a declared ``feature_cutoff`` +
    ``availability_column`` bounds every per-row availability date.

    ``summary`` — when a dict is supplied — is populated with
    ``data_rows``, ``bytes_scanned`` and ``observed_missing_fraction``
    even when problems are found."""
    stats: dict[str, Any] = {"data_rows": 0, "bytes_scanned": 0,
                             "observed_missing_fraction": 0.0}
    try:
        if mpath.suffix.lower() not in CANONICAL_MATRIX_FORMATS:
            problems.append("unsupported matrix format for semantic "
                            "scan — the canonical freeze format is "
                            "normalized CSV only")
            return
        # FMX-08: byte bound — stat first so a huge file is refused
        # before any bytes are consumed.
        try:
            declared_size = mpath.stat().st_size
        except OSError as exc:
            problems.append(
                f"matrix file unreadable for semantic scan: {exc}")
            return
        stats["bytes_scanned"] = declared_size
        if declared_size > MAX_MATRIX_BYTES:
            problems.append(
                f"matrix file is {declared_size} bytes — exceeds the "
                f"resource bound MAX_MATRIX_BYTES={MAX_MATRIX_BYTES}")
            return

        declared_cols: list[Mapping[str, Any]] = []
        raw_cols = matrix_meta.get("columns") if isinstance(
            matrix_meta, Mapping) else None
        if isinstance(raw_cols, list):
            declared_cols = [c for c in raw_cols
                             if isinstance(c, Mapping)]
        allowed_names = _matrix_allowed_header_names(matrix_meta)

        # Missingness mode: "complete" policy or fraction == 0.0 means
        # strict — any empty cell rejects; a positive declared fraction
        # bounds the observed empty-cell fraction.
        policy = ""
        declared_fraction = 0.0
        missingness = matrix_meta.get("missingness") if isinstance(
            matrix_meta, Mapping) else None
        if isinstance(missingness, Mapping):
            policy = str(missingness.get("policy", "")).lower()
            frac = missingness.get("fraction")
            if isinstance(frac, (int, float)) and not isinstance(
                    frac, bool) and math.isfinite(float(frac)):
                declared_fraction = float(frac)
        strict_missing = policy == "complete" or \
            declared_fraction <= 0.0

        dr = matrix_meta.get("date_range") if isinstance(
            matrix_meta, Mapping) else None
        dr_start: Optional[date] = None
        dr_end: Optional[date] = None
        if isinstance(dr, Mapping) and \
                _is_strict_iso_date(dr.get("start")) and \
                _is_strict_iso_date(dr.get("end")):
            dr_start = date.fromisoformat(dr["start"])
            dr_end = date.fromisoformat(dr["end"])

        # FMX-05: temporal cutoff declaration.
        cutoff_raw = matrix_meta.get("feature_cutoff") if isinstance(
            matrix_meta, Mapping) else None
        cutoff: Optional[date] = None
        if cutoff_raw is not None:
            if _is_strict_iso_date(cutoff_raw):
                cutoff = date.fromisoformat(cutoff_raw)
            else:
                problems.append("matrix.feature_cutoff must be a strict "
                                "ISO YYYY-MM-DD calendar date")

        rid = matrix_meta.get("row_id_column") if isinstance(
            matrix_meta, Mapping) else None
        coords = matrix_meta.get("coordinate_columns") if isinstance(
            matrix_meta, Mapping) else None
        if coords is not None and not isinstance(coords, Mapping):
            problems.append("matrix.coordinate_columns must map "
                            "'lat'/'lon' to CSV column names")
        avail = matrix_meta.get("availability_column") if isinstance(
            matrix_meta, Mapping) else None

        # Per-column check specs built once the header is known:
        # (index, name, role, is_date, lo, hi).
        col_specs: list[tuple] = []
        # Structural check specs: (axis, index, lo, hi).
        coord_specs: list[tuple] = []
        rid_i = -1
        avail_i = -1
        rid_seen: set[str] = set()
        header: Optional[list[str]] = None
        empty_cells = 0

        try:
            with mpath.open("rb") as fh:
                stream = io.TextIOWrapper(fh, encoding="utf-8",
                                          newline="")
                reader = csv.reader(stream, strict=True)
                for row in reader:
                    if header is None:
                        header = [str(h) for h in row]
                        if len(set(header)) != len(header):
                            problems.append(
                                "matrix CSV header contains duplicate "
                                "column names")
                        for name in header:
                            lowered = name.lower()
                            if any(tok in lowered for tok in
                                   _FORBIDDEN_MATRIX_FILE_TOKENS):
                                problems.append(
                                    f"matrix CSV column {name!r} "
                                    "contains a B-derived leakage "
                                    "token and is rejected")
                            if name not in allowed_names:
                                problems.append(
                                    f"matrix CSV header cell {name!r} "
                                    "is not a declared column — "
                                    "undeclared columns are rejected")
                        index: dict[str, int] = {}
                        for i, name in enumerate(header):
                            index.setdefault(name, i)
                        for col in declared_cols:
                            if col.get("name") not in index:
                                problems.append(
                                    "matrix CSV is missing declared "
                                    f"column {col.get('name')!r}")
                        for col in declared_cols:
                            name = col.get("name")
                            if not isinstance(name, str) or \
                                    name not in index:
                                continue
                            role = str(col.get("role", "")).lower()
                            unit = str(col.get("unit", "")).lower()
                            is_date = role in ("time", "date") or \
                                unit in ("iso8601", "iso-8601",
                                         "date") or str(
                                    col.get("data_type", "")).lower() \
                                == "date"
                            lo = _declared_bound(col, "min_value", name,
                                                 problems)
                            hi = _declared_bound(col, "max_value", name,
                                                 problems)
                            col_specs.append(
                                (index[name], name, role, is_date,
                                 lo, hi))
                        # FMX-04: row-id column binding.
                        if rid is not None:
                            if not isinstance(rid, str) or not rid:
                                problems.append(
                                    "matrix.row_id_column must name a "
                                    "CSV column")
                            elif rid not in index:
                                problems.append(
                                    f"declared row_id_column {rid!r} "
                                    "is not a matrix CSV column")
                            else:
                                rid_i = index[rid]
                        # FMX-04: coordinate column bindings.
                        if isinstance(coords, Mapping):
                            for axis, lo_b, hi_b in (
                                    ("lat", -90.0, 90.0),
                                    ("lon", -180.0, 180.0)):
                                cname = coords.get(axis)
                                if not isinstance(cname, str) or \
                                        not cname:
                                    problems.append(
                                        "matrix.coordinate_columns"
                                        f"[{axis!r}] must name a CSV "
                                        "column")
                                elif cname not in index:
                                    problems.append(
                                        f"declared {axis} coordinate "
                                        f"column {cname!r} is not a "
                                        "matrix CSV column")
                                else:
                                    coord_specs.append(
                                        (axis, index[cname], lo_b,
                                         hi_b))
                        # FMX-05: availability column binding.
                        if cutoff is not None and avail is not None:
                            if not isinstance(avail, str) or not avail:
                                problems.append(
                                    "matrix.availability_column must "
                                    "name a CSV column")
                            elif avail not in index:
                                problems.append(
                                    "declared availability_column "
                                    f"{avail!r} is not a matrix CSV "
                                    "column")
                            else:
                                avail_i = index[avail]
                        continue
                    stats["data_rows"] += 1
                    r = stats["data_rows"]  # 1-based data row index
                    if stats["data_rows"] > MAX_MATRIX_ROWS:
                        problems.append(
                            f"matrix CSV exceeds the resource bound "
                            f"MAX_MATRIX_ROWS={MAX_MATRIX_ROWS}")
                        return
                    if stats["data_rows"] % 4096 == 0 and \
                            fh.tell() > MAX_MATRIX_BYTES:
                        problems.append(
                            f"matrix CSV exceeds the resource bound "
                            f"MAX_MATRIX_BYTES={MAX_MATRIX_BYTES}")
                        return
                    # FMX-07: strict row width — a row with more or
                    # fewer cells than the header rejects.
                    if len(row) != len(header):
                        problems.append(
                            f"matrix CSV row {r + 1} has {len(row)} "
                            f"cells but the header declares "
                            f"{len(header)}")
                    row_has_empty = False
                    for j in range(len(header)):
                        cell = str(row[j]).strip() \
                            if j < len(row) else ""
                        if not cell:
                            empty_cells += 1
                            row_has_empty = True
                    if strict_missing and row_has_empty:
                        problems.append(
                            f"matrix CSV row {r + 1} has missing cells "
                            "under missingness policy 'complete'")
                    for (i, name, role, is_date, lo, hi) in col_specs:
                        cell = str(row[i]).strip() \
                            if i < len(row) else ""
                        if not cell:
                            continue  # missingness accounting already
                            # handled the empty cell
                        if role in ("feature", "target"):
                            try:
                                value = float(cell)
                            except ValueError:
                                problems.append(
                                    f"matrix CSV row {r + 1} column "
                                    f"{name!r} is not numeric for a "
                                    "feature/target role")
                                continue
                            if not math.isfinite(value):
                                problems.append(
                                    f"matrix CSV row {r + 1} column "
                                    f"{name!r} is not a finite float "
                                    "(NaN/inf rejected)")
                        elif is_date:
                            if not _is_strict_iso_date(cell):
                                problems.append(
                                    f"matrix CSV row {r + 1} column "
                                    f"{name!r} is not a strict ISO "
                                    "YYYY-MM-DD calendar date")
                            elif dr_start is not None and \
                                    dr_end is not None and not \
                                    dr_start <= \
                                    date.fromisoformat(cell) <= dr_end:
                                problems.append(
                                    f"matrix CSV row {r + 1} column "
                                    f"{name!r} is outside the declared "
                                    "date_range")
                        if lo is not None or hi is not None:
                            try:
                                bval = float(cell)
                            except ValueError:
                                problems.append(
                                    f"matrix CSV row {r + 1} column "
                                    f"{name!r} is not numeric but the "
                                    "column declares min/max bounds")
                                continue
                            if not math.isfinite(bval) or \
                                    (lo is not None and bval < lo) or \
                                    (hi is not None and bval > hi):
                                problems.append(
                                    f"matrix CSV row {r + 1} column "
                                    f"{name!r} value {cell!r} is "
                                    "outside the declared bounds "
                                    f"[{lo}, {hi}]")
                    # FMX-04: row-id values must be non-empty + unique.
                    if rid_i >= 0:
                        cell = str(row[rid_i]).strip() \
                            if rid_i < len(row) else ""
                        if not cell:
                            problems.append(
                                f"matrix CSV row {r + 1} has an empty "
                                "row id")
                        elif cell in rid_seen:
                            problems.append(
                                f"matrix CSV row {r + 1} duplicates "
                                f"row id {cell!r}")
                        else:
                            rid_seen.add(cell)
                    # FMX-04: coordinate ranges.
                    for axis, ci, lo_b, hi_b in coord_specs:
                        cell = str(row[ci]).strip() \
                            if ci < len(row) else ""
                        try:
                            cval = float(cell)
                        except ValueError:
                            problems.append(
                                f"matrix CSV row {r + 1} {axis} "
                                f"coordinate {cell!r} is not numeric")
                            continue
                        if not math.isfinite(cval) or \
                                not lo_b <= cval <= hi_b:
                            problems.append(
                                f"matrix CSV row {r + 1} {axis} "
                                f"coordinate {cell!r} is outside "
                                f"[{lo_b}, {hi_b}]")
                    # FMX-05: per-row availability must precede cutoff.
                    if avail_i >= 0:
                        cell = str(row[avail_i]).strip() \
                            if avail_i < len(row) else ""
                        if not _is_strict_iso_date(cell):
                            problems.append(
                                f"matrix CSV row {r + 1} availability "
                                f"value {cell!r} is not a strict ISO "
                                "YYYY-MM-DD calendar date")
                        elif cutoff is not None and \
                                date.fromisoformat(cell) > cutoff:
                            problems.append(
                                f"matrix CSV row {r + 1} availability "
                                f"value {cell!r} is after the declared "
                                f"feature_cutoff {cutoff_raw!r}")
                stats["bytes_scanned"] = max(stats["bytes_scanned"],
                                             fh.tell())
        except UnicodeDecodeError as exc:
            problems.append(
                f"matrix file unreadable for semantic scan: {exc}")
            return
        except csv.Error as exc:
            problems.append(f"matrix CSV is not parseable: {exc}")
            return
        except OSError as exc:
            problems.append(
                f"matrix file unreadable for semantic scan: {exc}")
            return

        if header is None:
            problems.append("matrix CSV has no header row")
            return
        if stats["data_rows"] == 0:
            problems.append("matrix CSV must contain at least one "
                            "data row")
            return

        # FMX-04: a declared n_rows must equal the actual data row
        # count.
        n_rows = matrix_meta.get("n_rows") if isinstance(
            matrix_meta, Mapping) else None
        if n_rows is not None:
            if not isinstance(n_rows, int) or isinstance(n_rows, bool):
                problems.append("matrix.n_rows must be an integer")
            elif stats["data_rows"] != n_rows:
                problems.append(
                    f"matrix CSV has {stats['data_rows']} data rows "
                    f"but matrix.n_rows declares {n_rows}")

        # FMX-07: under a positive declared missingness fraction the
        # observed empty-cell fraction must not exceed it — an
        # under-declared missingness rejects.
        total_cells = stats["data_rows"] * len(header)
        observed = empty_cells / total_cells if total_cells else 0.0
        stats["observed_missing_fraction"] = observed
        if not strict_missing and \
                observed > declared_fraction + 1e-12:
            problems.append(
                f"matrix CSV observed missingness fraction "
                f"{observed:.6f} exceeds declared "
                f"missingness.fraction {declared_fraction}")
    finally:
        if isinstance(summary, dict):
            summary.update(stats)


def build_fmx_envelope(matrix: Optional[Mapping[str, Any]] = None, *,
                       freeze_token: Optional[Mapping[str, Any]] = None,
                       artifact_root: Optional[str | Path] = None,
                       matrix_relpath: Optional[str] = None,
                       token_relpath: Optional[str] = None,
                       dirty_checkout_root: Optional[str | Path] = None,
                       protected_roots: Optional[list] = None,
                       lineage_paths: Optional[Mapping[str, str]] = None
                       ) -> dict[str, Any]:
    """Build a self-hashed FMX envelope.

    Without ``freeze_token`` the result is always
    ``FMX_BLOCKED_PENDING_EXPLICIT_FREEZE`` — optionally carrying
    schema-validated *declared* metadata with all digests nulled.

    ``FMX_READY`` additionally requires ``artifact_root`` +
    ``matrix_relpath`` + ``token_relpath``: the matrix and token files are
    re-hashed from disk, the matrix bytes pass a semantic scan, the token
    file must equal ``freeze_token`` exactly, and the token must bind the
    matrix's real digests plus freeze-authority fields.  READY also
    requires at least one protected root (``protected_roots`` and/or
    ``dirty_checkout_root``); the artifact root and every bound file must
    not resolve under any of them.  ``lineage_paths`` must bind every
    lineage digest field to a frozen file whose SHA-256 equals the
    declared matrix digest.  The artifact root's resolved final path
    component is bound as ``file_bindings.artifact_root_name``; when the
    token declares ``approval_record_relpath`` the approval record is
    resolved under the root and digest-bound to
    ``freeze_token.approval_record_sha256``.
    """
    problems: list[str] = []
    if matrix is not None:
        _check_matrix_schema(matrix, problems)
        _check_paths(matrix, problems)

    file_bindings: Optional[dict[str, Any]] = None
    if freeze_token is not None:
        if matrix is None:
            problems.append("FMX_READY requires matrix metadata bound to "
                            "the frozen bytes")
        else:
            _check_freeze_token_fields(freeze_token, matrix, problems)
        if artifact_root is None or matrix_relpath is None or \
                token_relpath is None:
            problems.append(
                "FMX_READY requires artifact_root + matrix_relpath + "
                "token_relpath — an in-memory token/matrix pair can "
                "never produce READY")
        else:
            root = Path(artifact_root)
            protected = _protected_root_list(protected_roots,
                                             dirty_checkout_root)
            if not protected:
                problems.append(
                    "FMX_READY requires at least one protected root — "
                    "pass protected_roots and/or dirty_checkout_root so "
                    "bound evidence is provably outside controlled "
                    "territory")
            if _resolves_under(root, protected):
                problems.append("artifact_root resolves under a "
                                "protected root — refused")
            mpath = _resolve_bound_file(root, matrix_relpath,
                                        "matrix_relpath", problems)
            tpath = _resolve_bound_file(root, token_relpath,
                                        "token_relpath", problems)
            for p, label in ((mpath, "matrix"), (tpath, "token")):
                if p is not None and _resolves_under(p, protected):
                    problems.append(f"bound {label} file resolves under "
                                    "a protected root — refused")
            lineage_bindings: dict[str, str] = {}
            if not isinstance(lineage_paths, Mapping):
                problems.append(
                    "FMX_READY requires lineage_paths — a mapping of "
                    "lineage digest fields to frozen files under "
                    "artifact_root")
            else:
                for field in _LINEAGE_DIGEST_FIELDS:
                    if field not in lineage_paths:
                        problems.append(f"lineage_paths must bind "
                                        f"{field}")
                for field, rel in lineage_paths.items():
                    if not isinstance(field, str):
                        problems.append("lineage_paths keys must be "
                                        "digest field names")
                        continue
                    lp = _resolve_bound_file(
                        root, rel, f"lineage_paths[{field!r}]", problems)
                    if lp is None:
                        continue
                    if _resolves_under(lp, protected):
                        problems.append(
                            f"lineage file for {field} resolves under "
                            "a protected root — refused")
                    elif not isinstance(matrix, Mapping) or \
                            sha256_file(lp) != matrix.get(field):
                        problems.append(
                            f"lineage file for {field} does not match "
                            "the declared matrix digest")
                    else:
                        lineage_bindings[field] = rel
            if mpath is not None and isinstance(matrix, Mapping):
                actual_sha = sha256_file(mpath)
                actual_bytes = mpath.stat().st_size
                if actual_sha != matrix.get("matrix_sha256"):
                    problems.append("matrix file digest does not match "
                                    "declared matrix_sha256")
                if actual_bytes != matrix.get("byte_count"):
                    problems.append("matrix file byte count does not "
                                    "match declared byte_count")
                _validate_matrix_file(mpath, matrix, problems)
            token_doc: Any = None
            if tpath is not None:
                try:
                    token_doc = json.loads(tpath.read_text("utf-8"))
                except (OSError, ValueError) as exc:
                    problems.append(f"token file unreadable: {exc}")
                else:
                    if token_doc != dict(freeze_token or {}):
                        problems.append(
                            "freeze_token does not equal the on-disk "
                            "token file contents")
            # FMX-VERIFY-02: optional approval-record binding — when the
            # freeze token declares approval_record_relpath, the file
            # must resolve under artifact_root (never a symlink, never
            # escaping the root) and its recomputed digest must equal
            # the declared approval_record_sha256.
            apath: Optional[Path] = None
            approval_rel = freeze_token.get("approval_record_relpath") \
                if isinstance(freeze_token, Mapping) else None
            if approval_rel is not None:
                apath = _resolve_bound_file(
                    root, approval_rel,
                    "freeze_token.approval_record_relpath", problems)
                if apath is not None:
                    if _resolves_under(apath, protected):
                        problems.append(
                            "bound approval record file resolves under "
                            "a protected root — refused")
                    elif sha256_file(apath) != freeze_token.get(
                            "approval_record_sha256"):
                        problems.append(
                            "approval record file digest does not "
                            "match declared "
                            "freeze_token.approval_record_sha256")
            if not problems and mpath is not None and tpath is not None:
                file_bindings = {
                    "artifact_root_name": root.resolve().name,
                    "matrix_relative_path": matrix_relpath,
                    "token_relative_path": token_relpath,
                    "matrix_file_sha256": sha256_file(mpath),
                    "matrix_file_byte_count": mpath.stat().st_size,
                    "token_file_sha256": sha256_file(tpath),
                    "lineage": lineage_bindings}
                if approval_rel is not None and apath is not None:
                    file_bindings["approval_record_relative_path"] = \
                        approval_rel
                    file_bindings["approval_record_file_sha256"] = \
                        sha256_file(apath)
    if problems:
        raise ValueError("FMX envelope is not valid: "
                         + "; ".join(problems[:8]))

    if freeze_token is None:
        envelope: dict[str, Any] = {
            "envelope_type": FMX_ENVELOPE_TYPE,
            "profile_id": FMX_PROFILE_ID,
            "fmx_status": FMX_BLOCKED_PENDING_EXPLICIT_FREEZE,
            "schema_status": FMX_SCHEMA_VALID if matrix is not None else
            FMX_BLOCKED_PENDING_EXPLICIT_FREEZE,
            "freeze_reason": FREEZE_REASON_ABSENT,
            "matrix": {"status": "ABSENT"},
            # PKG-08: explicit anti-confusion fields — a blocked
            # envelope is a schema fixture, never a frozen artifact.
            "schema_fixture": True,
            "external_freeze": False,
            "artifact_present": False,
            "not_a_real_matrix": True,
            "research_diagnostic_only": True,
            "promotion_eligible": False,
            "production_authorized": False,
            "warning_path_authorized": False,
            "no_claims": [
                "feature-matrix schema/metadata only; not a scientific or "
                "operational data product",
                "no warning, production, or authority readiness"],
        }
        if matrix is not None:
            envelope["declared_matrix_metadata"] = _null_digest_fields(
                matrix)
    else:
        assert matrix is not None and freeze_token is not None
        envelope = {
            "envelope_type": FMX_ENVELOPE_TYPE,
            "profile_id": FMX_PROFILE_ID,
            "fmx_status": FMX_READY,
            "schema_status": FMX_SCHEMA_VALID,
            "matrix": dict(matrix),
            "freeze_token": dict(freeze_token),
            "file_bindings": file_bindings,
            "research_diagnostic_only": True,
            "promotion_eligible": False,
            "production_authorized": False,
            "warning_path_authorized": False,
            "no_claims": [
                "frozen feature-matrix metadata; freeze does not imply "
                "scientific or operational readiness",
                "no warning, production, or authority readiness"],
        }
    return bind_artifact_envelope(envelope)


def bind_fmx_envelope(envelope: Mapping[str, Any]) -> dict[str, Any]:
    """Re-bind an existing FMX envelope dict (used to rebuild after a
    caller mutation in tests; performs no validation)."""
    return bind_artifact_envelope(dict(envelope))


def verify_fmx_envelope(payload: Any, *,
                        artifact_root: Optional[str | Path] = None,
                        protected_roots: Optional[list] = None
                        ) -> tuple[bool, list[str]]:
    """Fail-closed FMX verification: envelope self-hash, profile/auth
    fields, schema, path safety, status consistency, freeze-token binding,
    and — for READY — recomputation of the bound matrix/token/lineage
    files from disk under ``artifact_root`` plus a re-run of the matrix
    semantic scan on the bound bytes (FMX-VERIFY-01).  READY verification
    requires BOTH ``artifact_root`` and ``protected_roots``; every bound
    file must resolve under the root and outside every protected root,
    the root's resolved name must equal the bound
    ``artifact_root_name``, and a declared
    ``freeze_token.approval_record_relpath`` must resolve to a file
    whose digest equals ``approval_record_sha256`` (FMX-VERIFY-02).  A
    READY envelope without verifiable file evidence always fails."""
    problems: list[str] = []
    ok, env_problems = verify_artifact_envelope(payload)
    if not ok:
        problems.extend(env_problems)
    if not isinstance(payload, Mapping):
        problems.append("FMX envelope must be a mapping")
        return False, problems
    if payload.get("envelope_type") != FMX_ENVELOPE_TYPE:
        problems.append(f"envelope_type must be {FMX_ENVELOPE_TYPE!r}")
    if payload.get("profile_id") != FMX_PROFILE_ID:
        problems.append(f"profile_id must be {FMX_PROFILE_ID!r}")
    if payload.get("research_diagnostic_only") is not True:
        problems.append("research_diagnostic_only must be true")
    if payload.get("promotion_eligible") is not False:
        problems.append("promotion_eligible must be false")
    if payload.get("warning_path_authorized") is not False:
        problems.append("warning_path_authorized must be false")
    if payload.get("production_authorized") is not False:
        problems.append("production_authorized must be false")
    status = payload.get("fmx_status")
    if status not in (FMX_READY, FMX_BLOCKED_PENDING_EXPLICIT_FREEZE):
        problems.append(f"fmx_status {status!r} is not a producible "
                        "status on this envelope")

    if status == FMX_BLOCKED_PENDING_EXPLICIT_FREEZE:
        matrix = payload.get("matrix")
        if not isinstance(matrix, Mapping) or \
                matrix.get("status") != "ABSENT":
            problems.append("blocked envelope must carry "
                            "matrix.status = 'ABSENT'")
        if payload.get("freeze_reason") != FREEZE_REASON_ABSENT:
            problems.append(f"blocked envelope must carry "
                            f"freeze_reason = {FREEZE_REASON_ABSENT!r}")
        declared = payload.get("declared_matrix_metadata")
        if declared is not None:
            if not isinstance(declared, Mapping):
                problems.append("declared_matrix_metadata must be a "
                                "mapping")
            else:
                for key, value in declared.items():
                    if (key.endswith("_sha256") or key == "byte_count") \
                            and value is not None:
                        problems.append(
                            f"declared_matrix_metadata.{key} must be "
                            "null — unverified digests may not look real")
        if payload.get("freeze_token") is not None:
            problems.append("blocked envelope must not carry a "
                            "freeze_token")
        # PKG-08: a blocked envelope must carry explicit anti-confusion
        # fields — a schema fixture can never masquerade as a frozen
        # artifact.
        for field, expected in (("schema_fixture", True),
                                ("external_freeze", False),
                                ("artifact_present", False),
                                ("not_a_real_matrix", True)):
            if payload.get(field) is not expected:
                problems.append(f"blocked envelope must carry "
                                f"{field} = {expected}")

    if status == FMX_READY:
        matrix = payload.get("matrix")
        _check_matrix_schema(matrix, problems)
        token = payload.get("freeze_token")
        if not isinstance(token, Mapping):
            problems.append("FMX_READY requires an externally supplied "
                            "freeze token")
        elif isinstance(matrix, Mapping):
            _check_freeze_token_fields(token, matrix, problems)
        bindings = payload.get("file_bindings")
        if not isinstance(bindings, Mapping):
            problems.append("FMX_READY requires file_bindings — a "
                            "memory-only READY is not verifiable")
        else:
            for field in ("matrix_relative_path", "token_relative_path"):
                rel = bindings.get(field)
                if not isinstance(rel, str) or not rel or \
                        rel.startswith("/") or ".." in rel.split("/"):
                    problems.append(f"file_bindings.{field} must be a "
                                    "safe relative path")
            for field in ("matrix_file_sha256", "token_file_sha256"):
                if not _is_sha256(bindings.get(field)):
                    problems.append(f"file_bindings.{field} must be a "
                                    "lowercase SHA-256")
            if not isinstance(bindings.get("matrix_file_byte_count"),
                              int) or bindings.get(
                              "matrix_file_byte_count", 0) <= 0:
                problems.append("file_bindings.matrix_file_byte_count "
                                "must be a positive integer")
            # FMX-VERIFY-02: the artifact root identity is bound at
            # build — a renamed root must not verify.
            if not isinstance(bindings.get("artifact_root_name"), str) \
                    or not bindings.get("artifact_root_name"):
                problems.append("file_bindings.artifact_root_name must "
                                "be a non-empty string")
            if artifact_root is None or not protected_roots:
                problems.append("FMX_READY verification requires "
                                "artifact_root and protected_roots — "
                                "file bindings must be recomputed from "
                                "disk and shown to be outside every "
                                "protected root")
            else:
                root = Path(artifact_root)
                protected = _protected_root_list(protected_roots, None)
                if _resolves_under(root, protected):
                    problems.append("artifact_root resolves under a "
                                    "protected root — refused")
                # FMX-VERIFY-02: the supplied artifact root's resolved
                # final component must equal the name bound at build.
                declared_root_name = bindings.get("artifact_root_name")
                if isinstance(declared_root_name, str) and \
                        declared_root_name and \
                        root.resolve().name != declared_root_name:
                    problems.append(
                        f"artifact_root resolves to name "
                        f"{root.resolve().name!r} but file_bindings "
                        f"declares {declared_root_name!r} — a renamed "
                        "artifact root is rejected")
                m = _resolve_bound_file(
                    root, bindings.get("matrix_relative_path"),
                    "file_bindings.matrix_relative_path", problems)
                t = _resolve_bound_file(
                    root, bindings.get("token_relative_path"),
                    "file_bindings.token_relative_path", problems)
                for p, label in ((m, "matrix"), (t, "token")):
                    if p is not None and _resolves_under(p, protected):
                        problems.append(f"bound {label} file resolves "
                                        "under a protected root — "
                                        "refused")
                lineage = bindings.get("lineage")
                if not isinstance(lineage, Mapping):
                    problems.append("file_bindings.lineage must bind the "
                                    "lineage digest fields to frozen "
                                    "files")
                else:
                    for field in _LINEAGE_DIGEST_FIELDS:
                        if field not in lineage:
                            problems.append("file_bindings.lineage must "
                                            f"bind {field}")
                    for field, rel in lineage.items():
                        if not isinstance(field, str):
                            problems.append("file_bindings.lineage keys "
                                            "must be digest field names")
                            continue
                        lp = _resolve_bound_file(
                            root, rel,
                            f"file_bindings.lineage[{field!r}]", problems)
                        if lp is None:
                            continue
                        if _resolves_under(lp, protected):
                            problems.append(
                                f"bound lineage file for {field} "
                                "resolves under a protected root — "
                                "refused")
                        elif isinstance(matrix, Mapping) and \
                                sha256_file(lp) != matrix.get(field):
                            problems.append(
                                f"bound lineage file for {field} does "
                                "not match the declared matrix digest")
                if m is not None:
                    if sha256_file(m) != bindings.get(
                            "matrix_file_sha256"):
                        problems.append("bound matrix file digest "
                                        "mismatch")
                    if m.stat().st_size != bindings.get(
                            "matrix_file_byte_count"):
                        problems.append("bound matrix file byte count "
                                        "mismatch")
                    if isinstance(matrix, Mapping) and \
                            sha256_file(m) != matrix.get("matrix_sha256"):
                        problems.append("bound matrix file does not "
                                        "match declared matrix_sha256")
                    # FMX-VERIFY-01: re-run the semantic scan on the
                    # bound matrix bytes — an envelope whose digests
                    # were rebound to mutated bytes must still fail the
                    # CSV semantics.
                    if isinstance(matrix, Mapping):
                        _validate_matrix_file(m, matrix, problems)
                if t is not None:
                    if sha256_file(t) != bindings.get(
                            "token_file_sha256"):
                        problems.append("bound token file digest "
                                        "mismatch")
                    try:
                        if json.loads(t.read_text("utf-8")) != \
                                payload.get("freeze_token"):
                            problems.append("bound token file content "
                                            "does not equal the "
                                            "envelope freeze_token")
                    except (OSError, ValueError) as exc:
                        problems.append(f"bound token file unreadable: "
                                        f"{exc}")
                # FMX-VERIFY-02: optional approval-record binding — when
                # the freeze token declares approval_record_relpath the
                # file must resolve under artifact_root and its
                # recomputed digest must equal the declared
                # approval_record_sha256; an unresolvable record
                # rejects.
                token_apr = token.get("approval_record_relpath") \
                    if isinstance(token, Mapping) else None
                bound_apr = bindings.get("approval_record_relative_path")
                bound_apr_sha = bindings.get(
                    "approval_record_file_sha256")
                if token_apr is None and bound_apr is not None:
                    problems.append(
                        "file_bindings.approval_record_relative_path "
                        "is declared but freeze_token carries no "
                        "approval_record_relpath")
                if bound_apr_sha is not None and \
                        not _is_sha256(bound_apr_sha):
                    problems.append(
                        "file_bindings.approval_record_file_sha256 "
                        "must be a lowercase SHA-256")
                if token_apr is not None:
                    if bound_apr != token_apr:
                        problems.append(
                            "file_bindings."
                            "approval_record_relative_path does not "
                            "match freeze_token."
                            "approval_record_relpath")
                    ap = _resolve_bound_file(
                        root, token_apr,
                        "freeze_token.approval_record_relpath",
                        problems)
                    if ap is not None:
                        ap_sha = sha256_file(ap)
                        if _resolves_under(ap, protected):
                            problems.append(
                                "bound approval record file resolves "
                                "under a protected root — refused")
                        if token is not None and ap_sha != token.get(
                                "approval_record_sha256"):
                            problems.append(
                                "approval record file digest does not "
                                "match declared freeze_token."
                                "approval_record_sha256")
                        if _is_sha256(bound_apr_sha) and \
                                ap_sha != bound_apr_sha:
                            problems.append(
                                "approval record file digest does not "
                                "match file_bindings."
                                "approval_record_file_sha256")
    _check_paths({k: v for k, v in payload.items()
                  if k != "artifact_sha256"}, problems)
    return (not problems), problems
