"""Thin byte-bound source intake (Round-11 PoC contract).

Two functions, both fail-closed:

- ``build_source_manifest`` — constructs the EXISTING non-fixture
  ``source_manifest`` shape for real acquired bytes.  It creates no
  new hashing policy: byte verification stays with
  ``_hashing.verify_source_evidence`` and the shared floor.
- ``load_hmaglofdb_rows`` — parses a byte-bound HMAGLOFDB CSV through
  an explicit ``column_map`` into ``SourceRow`` records.  Missing
  columns, unknown semantic values, duplicate keys, invalid
  intervals, unknown basins, and unbound files reject — there is no
  fuzzy column inference.

Every error is a ``ValueError``; callers quarantine the attempt.
"""

from __future__ import annotations

import csv
import hashlib
import stat
from pathlib import Path
from typing import Any, Mapping, Sequence

from ._hashing import verify_source_evidence, read_evidence_file
from ..science_v0.events import SourceRow, BASIN_UNIVERSE
from .records import MECHANISM_IDS

#: Required semantic keys in ``column_map`` (audit contract).
_REQUIRED_COLUMN_MAP = (
    "source_row_key", "basin", "interval_start", "interval_end",
    "declared_precision", "mechanism")

#: Optional semantic keys.
_OPTIONAL_COLUMN_MAP = (
    "cascade_group_id", "parent_source_row_key", "observed_on")

#: Declared-precision vocabulary the loader accepts.  The timing
#: classifier bounds width-measured classes; the coarse terms are
#: preserved verbatim so heterogeneous source precision is never
#: silently upgraded (finding 13).
_DECLARED_PRECISIONS = frozenset({
    "exact_timestamp", "day", "interval", "interval_3d",
    "interval_8_30d", "month", "season", "monsoon_slice", "year",
    "unresolved"})

_VERSION_TOKEN = "source_version="


def build_source_manifest(
        evidence_root: Path,
        *,
        source_id: str,
        source_version: str,
        source_files: Sequence[Mapping[str, str]],
        units: Sequence[str],
        feature_allowlist: Sequence[str],
        lineage: str) -> dict:
    """Build the existing non-fixture ``source_manifest`` shape.

    ``source_files`` entries are ``{"relpath", "sha256"}`` mappings
    whose paths are declared relative to ``evidence_root`` and whose
    digests were computed by the acquirer (``sha256_file`` or the
    retrieval record).  This function validates shape and binds
    ``source_version`` into ``lineage`` as a parseable
    ``source_version=…;`` token — the serialized schema is exact and
    admits no version field, so the version rides lineage verbatim.

    Byte verification is NOT performed here: call
    ``verify_source_evidence`` (which re-checks containment, symlink
    policy, and declared digests) on the returned manifest.
    """
    problems: list[str] = []
    if not isinstance(source_id, str) or not source_id.strip():
        problems.append("source_id must be a non-empty string")
    if not isinstance(source_version, str) or \
            not source_version.strip():
        problems.append("source_version must be a non-empty string")
    if not isinstance(lineage, str) or not lineage.strip():
        problems.append("lineage must be a non-empty string")
    if not isinstance(evidence_root, Path):
        problems.append("evidence_root must be a pathlib.Path")
    if not isinstance(source_files, (list, tuple)) or \
            not source_files:
        problems.append("source_files must be a non-empty sequence "
                        "of {relpath, sha256} records")
        files = []
    else:
        files = []
        seen = set()
        for i, rec in enumerate(source_files):
            if not isinstance(rec, Mapping):
                problems.append(f"source_files[{i}] must be a mapping")
                continue
            extra = set(rec) - {"relpath", "sha256"}
            if extra:
                problems.append(
                    f"source_files[{i}] carries undeclared fields "
                    f"{sorted(extra)}")
            rel = rec.get("relpath")
            sha = rec.get("sha256")
            if not isinstance(rel, str) or not rel.strip():
                problems.append(f"source_files[{i}].relpath must be "
                                "a non-empty string")
                continue
            if rel.startswith("/") or rel.startswith("~") or \
                    ".." in Path(rel).parts:
                problems.append(
                    f"source_files[{i}].relpath {rel!r} escapes the "
                    "evidence root (absolute or traversal)")
                continue
            if rel in seen:
                problems.append(f"duplicate source_files relpath "
                                f"{rel!r}")
            seen.add(rel)
            if not isinstance(sha, str) or len(sha) != 64 or \
                    not all(c in "0123456789abcdef" for c in sha):
                problems.append(
                    f"source_files[{i}].sha256 must be 64 lowercase "
                    "hex — a declared byte digest")
                continue
            files.append({"relpath": rel, "sha256": sha})
        if not files:
            problems.append("no well-formed source_files records")
    for name, seq in (("units", units),
                      ("feature_allowlist", feature_allowlist)):
        if not isinstance(seq, (list, tuple)) or not seq or \
                any(not isinstance(u, str) or not u.strip()
                    for u in seq):
            problems.append(
                f"{name} must be a non-empty sequence of non-empty "
                "strings")
        elif len(set(seq)) != len(seq):
            problems.append(f"{name} contains duplicates")
    digests = [f["sha256"] for f in files]
    if problems:
        raise ValueError("build_source_manifest: " +
                         "; ".join(problems))
    return {
        "source_id": source_id.strip(),
        "source_digests": digests,
        "units": [u.strip() for u in units],
        "feature_allowlist": [f.strip() for f in feature_allowlist],
        "lineage":
            f"{_VERSION_TOKEN}{source_version.strip()}; {lineage.strip()}",
        "evidence_root": str(evidence_root),
        "source_files": files}


def _manifest_version(lineage: str) -> str | None:
    """Recover the ``source_version=…;`` token ``build_source_manifest``
    embeds in lineage."""
    if _VERSION_TOKEN in lineage:
        return lineage.split(_VERSION_TOKEN, 1)[1].split(";", 1)[0]
    return None


def load_hmaglofdb_rows(
        path: Path,
        *,
        source_manifest: Mapping[str, Any],
        column_map: Mapping[str, str]) -> tuple[SourceRow, ...]:
    """Parse a byte-bound HMAGLOFDB CSV into ``SourceRow`` records.

    Fail-closed on: ``path`` not bound by ``source_manifest`` (the
    file must be a declared ``source_files`` member whose bytes
    verify), missing ``column_map`` keys, unknown semantic keys,
    missing CSV columns, duplicate ``source_row_key`` values,
    unparseable or inverted intervals, unknown basins/mechanisms/
    precision terms, and an empty row set.  No column is inferred —
    every semantic name must be mapped explicitly.
    """
    problems: list[str] = []
    # --- byte binding: verify BEFORE any byte is read (R11.1-1) ---
    if not isinstance(source_manifest, Mapping):
        raise ValueError("source_manifest must be a mapping")
    # The real GLOF path admits exactly the seven-key non-fixture
    # manifest — a fixture marker or any extra field is a contract
    # violation (R11.2-1), never a silent alias.
    required_keys = {"source_id", "source_digests", "units",
                     "feature_allowlist", "lineage",
                     "evidence_root", "source_files"}
    if set(source_manifest) != required_keys:
        extra = sorted(set(source_manifest) - required_keys)
        missing = sorted(required_keys - set(source_manifest))
        detail = []
        if extra:
            detail.append(f"undeclared keys {extra} — fixture "
                          "markers and side fields are not "
                          "admissible on the real GLOF path")
        if missing:
            detail.append(f"missing required keys {missing}")
        raise ValueError("source_manifest must carry exactly the "
                         "seven declared keys: " + "; ".join(detail))
    root_s = source_manifest["evidence_root"]
    if not isinstance(root_s, str) or not root_s:
        raise ValueError("source_manifest lacks evidence_root")
    sf = source_manifest["source_files"]
    if not isinstance(sf, (list, tuple)) or \
            any(not isinstance(f, Mapping) for f in sf):
        raise ValueError(
            "source_manifest.source_files must be a sequence of "
            "{relpath, sha256} mappings")
    declared = {f.get("relpath"): f.get("sha256") for f in sf}
    root = Path(root_s)
    # The caller's path must lie LEXICALLY inside the declared
    # evidence root — an outside-root path that only resolves
    # inside has passed through a symlink alias (R11.2-4), and
    # '..' detours are equally inadmissible.
    p_abs = Path(path).absolute()
    r_abs = Path(root_s).absolute()
    if p_abs.parts[:len(r_abs.parts)] != r_abs.parts or \
            ".." in p_abs.parts[len(r_abs.parts):]:
        raise ValueError(
            f"intake path {path} does not lie inside the declared "
            f"evidence_root {root_s!r} — a path that only resolves "
            "inside via a symlink or '..' alias may not load "
            "evidence bytes")
    # Walk the lexical components under the root: no symlink is
    # admissible anywhere in the intake path (R11.1-1).
    acc = r_abs
    for part in p_abs.parts[len(r_abs.parts):]:
        acc = acc / part
        try:
            st = acc.lstat()
        except OSError as exc:
            raise ValueError(
                f"intake path component {acc} cannot be "
                f"stat'd: {exc}")
        if stat.S_ISLNK(st.st_mode):
            raise ValueError(
                f"intake path component {acc} is a symlink — "
                "load the declared evidence file directly, "
                "never through an alias")
    try:
        rel = str(path.resolve().relative_to(root.resolve()))
    except (ValueError, OSError):
        raise ValueError(
            f"intake path {path} is not inside the manifest's "
            f"evidence_root {root_s!r} — unbound bytes may not be "
            "loaded")
    if rel not in declared:
        raise ValueError(
            f"intake path {rel!r} is not a declared source_files "
            "member — the manifest must name every loaded file")
    # whole-manifest byte policy FIRST — containment, symlink policy,
    # and declared digests are verified before the intake file's
    # bytes are ever opened for parsing.
    evidence_problems = verify_source_evidence(source_manifest)
    if evidence_problems:
        raise ValueError("source_manifest evidence problems: " +
                         "; ".join(evidence_problems))
    # The parser consumes ONLY these identity-pinned bytes — the
    # same walk/pin the verifier applies; no second unverified read.
    data = read_evidence_file(root.resolve(), rel,
                              label="intake file")
    got = hashlib.sha256(data).hexdigest()
    if got != declared[rel]:
        raise ValueError(
            f"intake bytes for {rel!r} do not match the declared "
            "sha256 — the manifest binds exact bytes")
    sid = source_manifest.get("source_id", "")
    version = _manifest_version(
        source_manifest.get("lineage", "")
        if isinstance(source_manifest.get("lineage"), str) else "")
    if not sid or not version:
        raise ValueError(
            "source_manifest lacks a bound source_id or "
            "source_version=…; lineage token — build manifests with "
            "build_source_manifest")
    declared_units = source_manifest.get("units")
    if not isinstance(declared_units, (list, tuple)) or \
            any(not isinstance(u, str) for u in declared_units):
        raise ValueError("source_manifest lacks a declared units "
                         "list")
    unit_set = set(declared_units)

    # --- column map ---
    if not isinstance(column_map, Mapping):
        raise ValueError("column_map must be a mapping")
    missing = [k for k in _REQUIRED_COLUMN_MAP
               if k not in column_map]
    if missing:
        raise ValueError(f"column_map missing required semantic "
                         f"keys: {missing}")
    unknown = set(column_map) - set(_REQUIRED_COLUMN_MAP) - \
        set(_OPTIONAL_COLUMN_MAP)
    if unknown:
        raise ValueError(f"column_map carries unknown semantic "
                         f"keys: {sorted(unknown)}")
    # Every mapped target must be a non-empty string and distinct —
    # two semantic keys may never share one CSV column (R11.1-2).
    mapped = []
    for key, value in column_map.items():
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"column_map[{key!r}] must be a non-empty string "
                f"header name; got {value!r}")
        mapped.append(value.strip())
    if len(set(mapped)) != len(mapped):
        raise ValueError("column_map maps two semantic keys to the "
                         "same CSV header — each must be distinct")
    column_map = {k: v.strip() for k, v in column_map.items()}

    # --- parse the verified bytes (never re-open the path) ---
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError(f"intake file {rel!r} is not UTF-8: {exc}")
    try:
        import io
        reader = csv.DictReader(io.StringIO(text, newline=""))
        if reader.fieldnames is None:
            raise ValueError("empty CSV — no header row")
        if len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError(
                "CSV header contains duplicate column names — "
                "DictReader would silently collapse them")
        header = set(reader.fieldnames)
        missing_cols = sorted(
            {c for c in column_map.values()
             if isinstance(c, str)} - header)
        if missing_cols:
            raise ValueError(
                f"CSV lacks mapped columns: {missing_cols} — "
                "the column_map must name real header fields")
        rows = []
        seen_keys: set[str] = set()
        for lineno, raw in enumerate(reader, start=2):
            row_problems = []
            key = (raw.get(column_map["source_row_key"]) or "").strip()
            if not key:
                row_problems.append("empty source_row_key")
            elif key in seen_keys:
                row_problems.append(
                    f"duplicate source_row_key {key!r}")
            seen_keys.add(key)
            basin = (raw.get(column_map["basin"]) or "").strip()
            if basin not in BASIN_UNIVERSE:
                row_problems.append(
                    f"basin {basin!r} not in the declared "
                    "basin universe")
            elif basin not in unit_set:
                row_problems.append(
                    f"basin {basin!r} is not declared in the "
                    "source manifest's units — the manifest "
                    "binds the loaded unit universe")
            mech = (raw.get(column_map["mechanism"]) or "").strip()
            if mech not in MECHANISM_IDS:
                row_problems.append(
                    f"mechanism {mech!r} not in the declared "
                    "mechanism vocabulary")
            prec = (raw.get(column_map["declared_precision"])
                    or "").strip()
            if prec not in _DECLARED_PRECISIONS:
                row_problems.append(
                    f"declared_precision {prec!r} not in "
                    f"{sorted(_DECLARED_PRECISIONS)}")
            start = (raw.get(column_map["interval_start"])
                     or "").strip()
            end = (raw.get(column_map["interval_end"])
                   or "").strip()
            from datetime import datetime
            try:
                t0 = datetime.fromisoformat(
                    start.replace("Z", "+00:00"))
                t1 = datetime.fromisoformat(
                    end.replace("Z", "+00:00"))
                if t0.tzinfo is None or t1.tzinfo is None:
                    row_problems.append(
                        f"naive interval {start!r}..{end!r} — "
                        "explicit-UTC ISO required")
                elif t1 <= t0:
                    row_problems.append(
                        "interval_end must be after "
                        "interval_start")
            except (TypeError, ValueError):
                row_problems.append(
                    f"unparseable interval "
                    f"{start!r}..{end!r} — explicit-UTC ISO "
                    "required")
            if row_problems:
                problems.append(
                    f"line {lineno}: " +
                    "; ".join(row_problems))
                continue
            rows.append(SourceRow(
                source_id=sid,
                source_version=version,
                source_row_key=key,
                mechanism=mech,
                interval_start=start,
                interval_end=end,
                declared_precision=prec,
                basin=basin,
                cascade_group_id=(
                    (raw.get(column_map["cascade_group_id"])
                     or "").strip() or None)
                if "cascade_group_id" in column_map else None,
                parent_source_row_key=(
                    (raw.get(column_map["parent_source_row_key"])
                     or "").strip() or None)
                if "parent_source_row_key" in column_map else None,
                observed_on=(
                    (raw.get(column_map["observed_on"])
                     or "").strip() or None)
                if "observed_on" in column_map else None))
    except OSError as exc:
        raise ValueError(f"cannot read intake file {path}: {exc}")
    if problems:
        raise ValueError("load_hmaglofdb_rows: " +
                         "; ".join(problems))
    if not rows:
        raise ValueError("load_hmaglofdb_rows: zero well-formed "
                         "rows — an empty inventory is not a PoC "
                         "input")
    return tuple(rows)
