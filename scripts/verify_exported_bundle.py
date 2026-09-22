#!/usr/bin/env python3
"""Verify a clean-room P5 release-bundle export.

The exporter deliberately separates host paths (root_map.json) from logical
bundle paths.  This verifier therefore checks the copied bytes and sidecars,
resolves index/closure references through logical roots, and never requires a
source host path to exist.  It is read-only and does not create a report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath
from typing import Any, Mapping


BUNDLE_SCHEMA = "P5_RELEASE_BUNDLE_EXPORT_V1"
ROOT_MAP_SCHEMA = "P5_RELEASE_ROOT_MAP_V1"
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")


def _safe_relpath(value: Any) -> bool:
    if not isinstance(value, str) or not value or value.startswith("/"):
        return False
    if "\\" in value:
        return False
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
        and path.as_posix() == value
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _inside(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return False
    return True


def _has_symlink_component(path: Path, root: Path) -> bool:
    """Reject aliases through a symlinked directory inside the export."""

    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    current = root
    for part in relative.parts:
        current /= part
        if current.is_symlink():
            return True
    return False


def _load_json(path: Path, label: str, problems: list[str]) -> Any:
    if not path.is_file() or path.is_symlink():
        problems.append(f"{label} is missing or is a symlink: {path}")
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        problems.append(f"{label} is not valid UTF-8 JSON: {exc}")
        return None


def _as_mapping(value: Any, label: str, problems: list[str]) -> Mapping[str, Any] | None:
    if not isinstance(value, Mapping):
        problems.append(f"{label} must be an object")
        return None
    return value


def _export_path(bundle_root: Path, relpath: str, problems: list[str], label: str) -> Path | None:
    if not _safe_relpath(relpath):
        problems.append(f"{label} must be a canonical relative POSIX path")
        return None
    path = bundle_root / PurePosixPath(relpath)
    if not _inside(path, bundle_root):
        problems.append(f"{label} escapes the bundle root")
        return None
    if _has_symlink_component(path, bundle_root):
        problems.append(f"{label} traverses a symlinked export path")
        return None
    return path


def _verify_sidecar(path: Path, payload_digest: str, problems: list[str], label: str) -> None:
    if not path.is_file() or path.is_symlink():
        problems.append(f"{label} is missing or is a symlink: {path}")
        return
    try:
        tokens = path.read_text(encoding="utf-8").split()
    except (OSError, UnicodeDecodeError) as exc:
        problems.append(f"{label} is not readable UTF-8 text: {exc}")
        return
    if not tokens or not SHA256_RE.fullmatch(tokens[0]):
        problems.append(f"{label} does not begin with a SHA-256 digest")
    elif tokens[0].lower() != payload_digest.lower():
        problems.append(f"{label} digest does not match its payload")


def _root_entries(
    bundle: Mapping[str, Any],
    root_map: Mapping[str, Any],
    problems: list[str],
) -> dict[str, str]:
    bundle_roots = bundle.get("roots")
    map_roots = root_map.get("logical_roots")
    if not isinstance(bundle_roots, list) or not isinstance(map_roots, list):
        problems.append("bundle.roots and root_map.logical_roots must be lists")
        return {}

    def normalize(items: list[Any], label: str) -> dict[str, str]:
        result: dict[str, str] = {}
        for item in items:
            entry = _as_mapping(item, label, problems)
            if entry is None:
                continue
            root_id = entry.get("logical_root_id")
            export_relpath = entry.get("export_relpath")
            if not isinstance(root_id, str) or not root_id:
                problems.append(f"{label} logical_root_id must be non-empty")
                continue
            if root_id in result:
                problems.append(f"{label} contains duplicate root id {root_id!r}")
            if not _safe_relpath(export_relpath):
                problems.append(f"{label} {root_id!r} has unsafe export_relpath")
            result[root_id] = export_relpath
        return result

    bundle_by_id = normalize(bundle_roots, "bundle.roots")
    map_by_id = normalize(map_roots, "root_map.logical_roots")
    if bundle_by_id != map_by_id:
        problems.append("bundle.roots and root_map.logical_roots differ")

    host_map = root_map.get("host_path_to_logical_id")
    if not isinstance(host_map, Mapping) or not host_map:
        problems.append("root_map.host_path_to_logical_id must be a non-empty object")
    else:
        mapped_ids = list(host_map.values())
        all_string_ids = all(isinstance(value, str) for value in mapped_ids)
        if not all_string_ids:
            problems.append("root_map host mappings must point to string logical ids")
        if all_string_ids:
            if len(mapped_ids) != len(set(mapped_ids)):
                problems.append("root_map host mappings must assign each logical root exactly once")
            if set(mapped_ids) != set(map_by_id):
                problems.append("root_map host mappings must cover every logical root exactly")
        for host_path in host_map:
            if not isinstance(host_path, str) or not Path(host_path).is_absolute():
                problems.append("root_map host paths must remain absolute provenance strings")
    return bundle_by_id


def _verify_files(
    bundle_root: Path,
    bundle: Mapping[str, Any],
    roots: Mapping[str, str],
    problems: list[str],
) -> tuple[dict[tuple[str, str], Mapping[str, Any]], dict[str, list[Mapping[str, Any]]]]:
    files = bundle.get("files")
    if not isinstance(files, list) or not files:
        problems.append("bundle.files must be a non-empty list")
        return {}, {}
    by_key: dict[tuple[str, str], Mapping[str, Any]] = {}
    by_relpath: dict[str, list[Mapping[str, Any]]] = {}
    seen_exports: set[str] = set()
    for index, raw in enumerate(files):
        label = f"bundle.files[{index}]"
        entry = _as_mapping(raw, label, problems)
        if entry is None:
            continue
        required = (
            "logical_root_id", "relpath", "export_relpath", "sha256",
            "size_bytes", "sidecar_relpath", "sidecar_sha256",
        )
        for name in required:
            if name not in entry:
                problems.append(f"{label}.{name} is missing")
        root_id = entry.get("logical_root_id")
        relpath = entry.get("relpath")
        export_relpath = entry.get("export_relpath")
        if not isinstance(root_id, str) or root_id not in roots:
            problems.append(f"{label} names undeclared logical root {root_id!r}")
            continue
        if not _safe_relpath(relpath):
            problems.append(f"{label}.relpath is unsafe")
            continue
        expected_export = f"{roots[root_id].rstrip('/')}/{relpath}"
        if export_relpath != expected_export:
            problems.append(f"{label}.export_relpath does not match its logical root")
        if not _safe_relpath(export_relpath):
            problems.append(f"{label}.export_relpath is unsafe")
            continue
        if export_relpath in seen_exports:
            problems.append(f"duplicate exported path: {export_relpath}")
        seen_exports.add(export_relpath)
        if not _safe_relpath(entry.get("sidecar_relpath")):
            problems.append(f"{label}.sidecar_relpath is unsafe")
            continue
        if entry.get("sidecar_relpath") != f"{export_relpath}.sha256":
            problems.append(f"{label}.sidecar_relpath is not payload.sha256")
        digest = entry.get("sha256")
        sidecar_digest = entry.get("sidecar_sha256")
        if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
            problems.append(f"{label}.sha256 must be a SHA-256 digest")
        if not isinstance(sidecar_digest, str) or not SHA256_RE.fullmatch(sidecar_digest):
            problems.append(f"{label}.sidecar_sha256 must be a SHA-256 digest")
        size = entry.get("size_bytes")
        if not isinstance(size, int) or isinstance(size, bool) or size < 0:
            problems.append(f"{label}.size_bytes must be a non-negative integer")

        payload = _export_path(bundle_root, export_relpath, problems, f"{label}.export_relpath")
        sidecar = _export_path(bundle_root, entry.get("sidecar_relpath"), problems,
                               f"{label}.sidecar_relpath")
        if payload is None or sidecar is None:
            continue
        if not payload.is_file() or payload.is_symlink():
            problems.append(f"{label} payload is missing or a symlink: {payload}")
            continue
        actual = _sha256(payload)
        if isinstance(digest, str) and actual.lower() != digest.lower():
            problems.append(f"{label} payload SHA-256 mismatch")
        if isinstance(size, int) and payload.stat().st_size != size:
            problems.append(f"{label} payload size mismatch")
        if isinstance(digest, str) and SHA256_RE.fullmatch(digest):
            _verify_sidecar(sidecar, digest, problems, f"{label}.sidecar")
        if isinstance(sidecar_digest, str) and SHA256_RE.fullmatch(sidecar_digest):
            if sidecar.is_file() and not sidecar.is_symlink() and _sha256(sidecar).lower() != sidecar_digest.lower():
                problems.append(f"{label} sidecar SHA-256 mismatch")
        key = (root_id, relpath)
        if key in by_key:
            problems.append(f"duplicate logical file binding: {key}")
        by_key[key] = entry
        by_relpath.setdefault(relpath, []).append(entry)
    return by_key, by_relpath


def _resolve_reference(
    reference: Mapping[str, Any],
    label: str,
    by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    by_relpath: Mapping[str, list[Mapping[str, Any]]],
    problems: list[str],
) -> Mapping[str, Any] | None:
    relpath = reference.get("relpath")
    digest = reference.get("sha256")
    if not _safe_relpath(relpath):
        problems.append(f"{label}.relpath is unsafe or missing")
        return None
    if not isinstance(digest, str) or not SHA256_RE.fullmatch(digest):
        problems.append(f"{label}.sha256 must be a SHA-256 digest")
        return None
    root_id = reference.get("root_id", reference.get("logical_root_id"))
    if root_id is not None:
        if not isinstance(root_id, str):
            problems.append(f"{label}.root_id must be a logical-root string")
            return None
        entry = by_key.get((root_id, relpath))
        candidates = [] if entry is None else [entry]
    else:
        candidates = [entry for entry in by_relpath.get(relpath, [])
                      if entry.get("sha256", "").lower() == digest.lower()]
    if len(candidates) != 1:
        problems.append(
            f"{label} does not resolve uniquely in the exported bundle "
            f"(matches={len(candidates)})")
        return None
    entry = candidates[0]
    if entry.get("sha256", "").lower() != digest.lower():
        problems.append(f"{label}.sha256 does not match exported payload")
    expected_size = reference.get("size_bytes")
    if expected_size is not None and entry.get("size_bytes") != expected_size:
        problems.append(f"{label}.size_bytes does not match exported payload")
    return entry


def _verify_index(
    document: Mapping[str, Any],
    label: str,
    by_key: Mapping[tuple[str, str], Mapping[str, Any]],
    by_relpath: Mapping[str, list[Mapping[str, Any]]],
    problems: list[str],
) -> int:
    files = document.get("files")
    if not isinstance(files, list) or not files:
        problems.append(f"{label}.files must be a non-empty list")
        return 0
    verified = 0
    for index, raw in enumerate(files):
        reference = _as_mapping(raw, f"{label}.files[{index}]", problems)
        if reference is None:
            continue
        if reference.get("sidecar_exception") not in (None, ""):
            problems.append(f"{label}.files[{index}] declares an unsupported sidecar exception")
        entry = _resolve_reference(reference, f"{label}.files[{index}]", by_key,
                                   by_relpath, problems)
        if entry is not None:
            sidecar_digest = reference.get("sidecar_sha256")
            if sidecar_digest != entry.get("sidecar_sha256"):
                problems.append(f"{label}.files[{index}].sidecar_sha256 mismatch")
            verified += 1
    return verified


def _walk_digest_references(value: Any, path: str = "") -> list[tuple[str, Mapping[str, Any]]]:
    found: list[tuple[str, Mapping[str, Any]]] = []
    if isinstance(value, Mapping):
        if "relpath" in value and "sha256" in value:
            found.append((path or "object", value))
        for key, child in value.items():
            found.extend(_walk_digest_references(child, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for index, child in enumerate(value):
            found.extend(_walk_digest_references(child, f"{path}[{index}]"))
    return found


def verify_bundle(bundle_root: Path) -> dict[str, Any]:
    problems: list[str] = []
    bundle_root = bundle_root.expanduser()
    if bundle_root.is_symlink() or not bundle_root.is_dir():
        return {"status": "EXPORTED_BUNDLE_FAIL", "problems": [
            f"bundle root is not a regular directory: {bundle_root}"]}
    bundle_path = bundle_root / "bundle.json"
    root_map_path = bundle_root / "root_map.json"
    bundle = _load_json(bundle_path, "bundle.json", problems)
    root_map = _load_json(root_map_path, "root_map.json", problems)
    bundle_obj = _as_mapping(bundle, "bundle", problems)
    root_map_obj = _as_mapping(root_map, "root_map", problems)
    if bundle_obj is None or root_map_obj is None:
        return {"status": "EXPORTED_BUNDLE_FAIL", "problems": problems}
    if bundle_obj.get("schema") != BUNDLE_SCHEMA:
        problems.append(f"bundle.schema must be {BUNDLE_SCHEMA!r}")
    if root_map_obj.get("schema") != ROOT_MAP_SCHEMA:
        problems.append(f"root_map.schema must be {ROOT_MAP_SCHEMA!r}")
    if bundle_obj.get("bundle_name") != root_map_obj.get("bundle_name"):
        problems.append("bundle and root_map bundle_name differ")
    if bundle_obj.get("root_map_relpath") != "root_map.json":
        problems.append("bundle.root_map_relpath must be root_map.json")
    if bundle_obj.get("source_paths_excluded_from_bundle_metadata") is not True:
        problems.append("bundle must declare source paths excluded from metadata")

    roots = _root_entries(bundle_obj, root_map_obj, problems)
    for root_id, export_relpath in roots.items():
        root_path = _export_path(bundle_root, export_relpath, problems,
                                 f"root {root_id}")
        if root_path is None or not root_path.is_dir() or root_path.is_symlink():
            problems.append(f"root {root_id!r} is missing or is a symlink")
    by_key, by_relpath = _verify_files(bundle_root, bundle_obj, roots, problems)

    index_count = 0
    closure_count = 0
    bound_references = 0
    index_schemas: set[str] = set()
    for (root_id, relpath), entry in by_key.items():
        if not relpath.lower().endswith(".json"):
            continue
        payload = _export_path(bundle_root, entry["export_relpath"], problems,
                               f"payload {root_id}:{relpath}")
        if payload is None or not payload.is_file():
            continue
        try:
            document = json.loads(payload.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(document, Mapping):
            continue
        schema = document.get("schema")
        label = f"{root_id}:{relpath}"
        if isinstance(schema, str) and schema.startswith("P5_EVIDENCE_INDEX"):
            index_count += 1
            index_schemas.add(schema)
            bound_references += _verify_index(document, label, by_key, by_relpath, problems)
        elif isinstance(schema, str) and schema.startswith("P5_RELEASE_CLOSURE"):
            closure_count += 1
            root_of_trust = document.get("root_of_trust")
            if isinstance(root_of_trust, Mapping):
                declared_index_schema = root_of_trust.get("index_schema")
                if (isinstance(declared_index_schema, str) and
                        declared_index_schema not in index_schemas):
                    # The index may appear later in lexical order; defer the
                    # final schema check until the scan completes below.
                    pass
            refs = _walk_digest_references(document)
            for ref_label, reference in refs:
                if _resolve_reference(reference, f"{label}.{ref_label}", by_key,
                                       by_relpath, problems) is not None:
                    bound_references += 1

    if index_count != 1:
        problems.append(f"export must contain exactly one evidence index (found {index_count})")
    if closure_count != 1:
        problems.append(f"export must contain exactly one release closure (found {closure_count})")
    # Re-scan the closure document after the index set is known so a closure
    # cannot bind a different or absent index schema.
    for (root_id, relpath), entry in by_key.items():
        if not relpath.lower().endswith(".json"):
            continue
        payload = _export_path(bundle_root, entry["export_relpath"], problems,
                               f"payload {root_id}:{relpath}")
        if payload is None or not payload.is_file():
            continue
        try:
            document = json.loads(payload.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError):
            continue
        if not isinstance(document, Mapping):
            continue
        schema = document.get("schema")
        if isinstance(schema, str) and schema.startswith("P5_RELEASE_CLOSURE"):
            root_of_trust = document.get("root_of_trust")
            if isinstance(root_of_trust, Mapping):
                declared_index_schema = root_of_trust.get("index_schema")
                if declared_index_schema not in index_schemas:
                    problems.append("closure root_of_trust.index_schema does not match the exported index")
    status = "EXPORTED_BUNDLE_OK" if not problems else "EXPORTED_BUNDLE_FAIL"
    return {
        "status": status,
        "bundle_name": bundle_obj.get("bundle_name"),
        "file_count": len(bundle_obj.get("files", [])) if isinstance(bundle_obj.get("files"), list) else 0,
        "index_count": index_count,
        "closure_count": closure_count,
        "digest_bound_reference_count": bound_references,
        "problems": problems,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Verify a moved P5 clean-room release-bundle export.")
    parser.add_argument("bundle_root", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result = verify_bundle(args.bundle_root)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result.get("status") == "EXPORTED_BUNDLE_OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
