#!/usr/bin/env python3
"""Export an explicitly allow-listed P5 release bundle.

The exporter is deliberately narrower than an evidence-root copier.  A
caller names each physical root and each payload to export.  The resulting
bundle refers only to logical root ids and relative paths; host paths are
kept in the separate root-map provenance file.

This is a clean-room preparation tool.  It does not acquire data, invoke
the release-chain builders, or modify a source root.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import Any, NoReturn


SCHEMA = "P5_RELEASE_BUNDLE_EXPORT_V1"
ROOT_MAP_SCHEMA = "P5_RELEASE_ROOT_MAP_V1"
ROOT_ID_RE = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
SHA256_RE = re.compile(r"^[0-9a-fA-F]{64}$")
RESERVED_ROOT_IDS = {"repository"}


def _error(message: str) -> NoReturn:
    raise ValueError(message)


def _parse_root_spec(raw: str) -> tuple[str, Path]:
    if "=" not in raw:
        _error(f"root must be LOGICAL_ID=HOST_PATH: {raw!r}")
    root_id, host_path = raw.split("=", 1)
    if not ROOT_ID_RE.fullmatch(root_id):
        _error(f"invalid logical root id: {root_id!r}")
    if root_id in RESERVED_ROOT_IDS:
        _error(f"logical root id is reserved: {root_id!r}")
    if not host_path:
        _error(f"empty host path for root {root_id!r}")
    raw_path = Path(host_path).expanduser()
    if raw_path.is_symlink():
        _error(f"root must not be a symlink: {raw_path}")
    path = raw_path.resolve()
    if not path.is_dir():
        _error(f"root is not a regular directory: {path}")
    return root_id, path


def _parse_include_spec(raw: str) -> tuple[str, str]:
    if ":" not in raw:
        _error(f"include must be LOGICAL_ID:RELATIVE_PATH: {raw!r}")
    root_id, relpath = raw.split(":", 1)
    if not ROOT_ID_RE.fullmatch(root_id):
        _error(f"invalid logical root id in include: {root_id!r}")
    return root_id, _safe_relpath(relpath)


def _safe_relpath(raw: str) -> str:
    """Return a portable relative POSIX path or reject it."""

    if not raw or raw.startswith("/") or "\\" in raw:
        _error(f"path must be a non-empty relative POSIX path: {raw!r}")
    path = PurePosixPath(raw)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        _error(f"path contains an unsafe component: {raw!r}")
    normalized = path.as_posix()
    if normalized != raw:
        _error(f"path is not canonical POSIX syntax: {raw!r}")
    return normalized


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _regular_file(path: Path, label: str) -> Path:
    if path.is_symlink() or not path.is_file():
        _error(f"{label} is not a regular non-symlink file: {path}")
    return path


def _assert_inside(path: Path, root: Path, label: str) -> None:
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        _error(f"{label} escapes its declared root: {path}")


def _read_sidecar_digest(sidecar: Path, payload: Path) -> str:
    _regular_file(sidecar, "sidecar")
    try:
        first = sidecar.read_text(encoding="utf-8").split()
    except UnicodeDecodeError as exc:
        _error(f"sidecar is not UTF-8 text: {sidecar} ({exc})")
    if not first or not SHA256_RE.fullmatch(first[0]):
        _error(f"sidecar does not begin with a SHA-256 digest: {sidecar}")
    expected = first[0].lower()
    actual = _sha256(payload)
    if expected != actual:
        _error(
            f"sidecar digest mismatch for {payload}: "
            f"declared {expected}, actual {actual}")
    return expected


def _copy_exclusive(source: Path, destination: Path) -> None:
    """Copy bytes into a fresh destination without replacement semantics."""

    _regular_file(source, "source")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
        )
    except FileExistsError:
        _error(f"refusing to overwrite export file: {destination}")
    try:
        with source.open("rb") as source_stream, os.fdopen(
                descriptor, "wb") as destination_stream:
            shutil.copyfileobj(source_stream, destination_stream)
            destination_stream.flush()
            os.fsync(destination_stream.fileno())
    except BaseException:
        try:
            destination.unlink()
        except OSError:
            pass
        raise


def _write_bytes_exclusive(destination: Path, payload: bytes) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o644,
        )
    except FileExistsError:
        _error(f"refusing to overwrite export file: {destination}")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
    except BaseException:
        try:
            destination.unlink()
        except OSError:
            pass
        raise


def _write_json_exclusive(destination: Path, value: Any) -> None:
    payload = (
        json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False)
        + "\n"
    ).encode("utf-8")
    _write_bytes_exclusive(destination, payload)


def _manifest_source(
    manifest_arg: str,
    repo_root: Path,
) -> tuple[Path, str]:
    manifest = Path(manifest_arg).expanduser()
    if not manifest.is_absolute():
        manifest = repo_root / manifest
    if manifest.is_symlink():
        _error(f"manifest must not be a symlink: {manifest}")
    manifest = manifest.resolve()
    _assert_inside(manifest, repo_root, "manifest")
    _regular_file(manifest, "manifest")
    relpath = manifest.relative_to(repo_root).as_posix()
    return manifest, relpath


def _output_is_safe(
    output_root: Path,
    source_roots: list[Path],
    repo_root: Path,
) -> None:
    if output_root.exists():
        _error(f"refusing to overwrite existing export directory: {output_root}")
    candidate = output_root.resolve()
    for root in [*source_roots, repo_root]:
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            continue
        _error(
            f"export directory must not be inside a source or repository "
            f"root ({root}): {output_root}")


def _plan(args: argparse.Namespace) -> dict[str, Any]:
    if not args.root:
        _error("at least one --root is required")
    if not args.include:
        _error("at least one --include payload is required")

    roots: dict[str, Path] = {}
    seen_host_roots: set[Path] = set()
    for raw in args.root:
        root_id, path = _parse_root_spec(raw)
        if root_id in roots:
            _error(f"duplicate logical root id: {root_id}")
        if path in seen_host_roots:
            _error(f"same host root was declared more than once: {path}")
        roots[root_id] = path
        seen_host_roots.add(path)

    raw_repo_root = Path(args.repo_root).expanduser()
    if raw_repo_root.is_symlink():
        _error(f"repository root must not be a symlink: {raw_repo_root}")
    repo_root = raw_repo_root.resolve()
    if not repo_root.is_dir():
        _error(f"repository root is not a regular directory: {repo_root}")
    manifest, manifest_relpath = _manifest_source(args.manifest, repo_root)
    output_root = Path(args.output_root).expanduser().resolve()
    _output_is_safe(output_root, list(roots.values()), repo_root)

    includes: list[dict[str, Any]] = []
    seen_includes: set[tuple[str, str]] = set()
    for raw in args.include:
        root_id, relpath = _parse_include_spec(raw)
        if root_id not in roots:
            _error(f"include names an undeclared root: {root_id}")
        key = (root_id, relpath)
        if key in seen_includes:
            _error(f"duplicate include: {raw}")
        seen_includes.add(key)
        root = roots[root_id]
        source = root / Path(relpath)
        _assert_inside(source, root, f"include {raw}")
        _regular_file(source, "included payload")
        if relpath.endswith(".sha256"):
            _error(
                f"include payloads must omit the .sha256 suffix; "
                f"the sidecar is bound automatically: {raw}")
        sidecar = root / f"{relpath}.sha256"
        _assert_inside(sidecar, root, f"sidecar for {raw}")
        digest = _read_sidecar_digest(sidecar, source)
        includes.append({
            "logical_root_id": root_id,
            "relpath": relpath,
            "sha256": digest,
            "size_bytes": source.stat().st_size,
            "sidecar_relpath": f"{relpath}.sha256",
            "sidecar_sha256": _sha256(sidecar),
            "source": source,
            "sidecar": sidecar,
        })

    manifest_sidecar = Path(f"{manifest}.sha256")
    manifest_sidecar_source = "generated"
    manifest_sidecar_sha256: str
    if manifest_sidecar.exists():
        _read_sidecar_digest(manifest_sidecar, manifest)
        manifest_sidecar_source = "copied"
        manifest_sidecar_sha256 = _sha256(manifest_sidecar)
    else:
        generated = _generated_manifest_sidecar(
            _sha256(manifest), manifest_relpath)
        manifest_sidecar_sha256 = hashlib.sha256(generated).hexdigest()

    return {
        "bundle_name": args.bundle_name,
        "output_root": output_root,
        "repo_root": repo_root,
        "roots": roots,
        "manifest": manifest,
        "manifest_relpath": manifest_relpath,
        "manifest_sidecar": manifest_sidecar,
        "manifest_sidecar_source": manifest_sidecar_source,
        "manifest_sidecar_sha256": manifest_sidecar_sha256,
        "includes": includes,
    }


def _logical_bundle_entry(
    item: dict[str, Any],
    export_relpath: str,
) -> dict[str, Any]:
    return {
        "logical_root_id": item["logical_root_id"],
        "relpath": item["relpath"],
        "export_relpath": export_relpath,
        "sha256": item["sha256"],
        "size_bytes": item["size_bytes"],
        "sidecar_relpath": f"{export_relpath}.sha256",
        "sidecar_sha256": item["sidecar_sha256"],
    }


def _generated_manifest_sidecar(
    manifest_digest: str,
    manifest_relpath: str,
) -> bytes:
    return (
        f"{manifest_digest}  {Path(manifest_relpath).name}\n"
    ).encode("ascii")


def _build_metadata(plan: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    root_entries = [
        {
            "logical_root_id": root_id,
            "export_relpath": f"roots/{root_id}",
        }
        for root_id in sorted(plan["roots"])
    ]
    root_entries.append({
        "logical_root_id": "repository",
        "export_relpath": "repository",
    })

    files = []
    for item in plan["includes"]:
        export_relpath = f"roots/{item['logical_root_id']}/{item['relpath']}"
        files.append(_logical_bundle_entry(item, export_relpath))

    manifest_export_relpath = f"repository/{plan['manifest_relpath']}"
    manifest_entry = {
        "logical_root_id": "repository",
        "relpath": plan["manifest_relpath"],
        "export_relpath": manifest_export_relpath,
        "sha256": _sha256(plan["manifest"]),
        "size_bytes": plan["manifest"].stat().st_size,
        "sidecar_relpath": f"{manifest_export_relpath}.sha256",
        "sidecar_source": plan["manifest_sidecar_source"],
        "sidecar_sha256": plan["manifest_sidecar_sha256"],
    }
    files.append(manifest_entry)

    bundle = {
        "schema": SCHEMA,
        "bundle_name": plan["bundle_name"],
        "root_map_relpath": "root_map.json",
        "roots": root_entries,
        "files": sorted(files, key=lambda entry: entry["export_relpath"]),
        "source_paths_excluded_from_bundle_metadata": True,
    }
    root_map = {
        "schema": ROOT_MAP_SCHEMA,
        "bundle_name": plan["bundle_name"],
        "host_path_to_logical_id": {
            str(plan["roots"][root_id]): root_id
            for root_id in sorted(plan["roots"])
        }
        | {str(plan["repo_root"]): "repository"},
        "logical_roots": root_entries,
    }
    return bundle, root_map


def _publish(plan: dict[str, Any], bundle: dict[str, Any],
             root_map: dict[str, Any]) -> None:
    output_root: Path = plan["output_root"]
    output_root.parent.mkdir(parents=True, exist_ok=True)
    try:
        output_root.mkdir(mode=0o755)
    except FileExistsError:
        _error(f"refusing to overwrite existing export directory: {output_root}")

    for item in plan["includes"]:
        export_relpath = (
            f"roots/{item['logical_root_id']}/{item['relpath']}")
        destination = output_root / export_relpath
        _copy_exclusive(item["source"], destination)
        if _sha256(destination) != item["sha256"]:
            _error(f"post-copy payload digest mismatch: {destination}")
        sidecar_destination = output_root / f"{export_relpath}.sha256"
        _copy_exclusive(item["sidecar"], sidecar_destination)
        if _sha256(sidecar_destination) != item["sidecar_sha256"]:
            _error(f"post-copy sidecar digest mismatch: {sidecar_destination}")

    manifest_destination = output_root / "repository" / plan["manifest_relpath"]
    _copy_exclusive(plan["manifest"], manifest_destination)
    manifest_entry = next(
        entry for entry in bundle["files"]
        if entry["logical_root_id"] == "repository")
    if _sha256(manifest_destination) != manifest_entry["sha256"]:
        _error(f"post-copy manifest digest mismatch: {manifest_destination}")
    if plan["manifest_sidecar_source"] == "copied":
        manifest_sidecar_destination = (
            output_root / f"repository/{plan['manifest_relpath']}.sha256")
        _copy_exclusive(plan["manifest_sidecar"], manifest_sidecar_destination)
    else:
        manifest_sidecar_destination = (
            output_root / f"repository/{plan['manifest_relpath']}.sha256")
        _write_bytes_exclusive(
            manifest_sidecar_destination,
            _generated_manifest_sidecar(
                manifest_entry["sha256"], plan["manifest_relpath"]))
    if _sha256(manifest_sidecar_destination) != (
            manifest_entry["sidecar_sha256"]):
        _error(
            f"post-copy manifest sidecar digest mismatch: "
            f"{manifest_sidecar_destination}")
    _write_json_exclusive(output_root / "root_map.json", root_map)
    _write_json_exclusive(output_root / "bundle.json", bundle)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Export an explicitly allow-listed release bundle without "
            "copying a live evidence root or overwriting an export."))
    parser.add_argument("--bundle-name", required=True)
    parser.add_argument(
        "--output-root", required=True,
        help="new directory to create; it must not already exist")
    parser.add_argument(
        "--root", action="append", default=[],
        metavar="LOGICAL_ID=HOST_PATH",
        help="declared physical root; repeat for each logical root")
    parser.add_argument(
        "--include", action="append", default=[],
        metavar="LOGICAL_ID:RELATIVE_PATH",
        help=(
            "payload under a declared root; repeat for every payload. "
            "A matching .sha256 sidecar is required and copied."))
    parser.add_argument(
        "--manifest", required=True,
        help="manifest file under --repo-root")
    parser.add_argument(
        "--repo-root", default=".",
        help="repository root containing --manifest (default: cwd)")
    parser.add_argument(
        "--dry-run", action="store_true",
        help="validate the allow-list and print the planned export only")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = _plan(args)
        bundle, root_map = _build_metadata(plan)
        output_root: Path = plan["output_root"]
        if args.dry_run:
            print(json.dumps({
                "dry_run": True,
                "output_root": str(output_root),
                "bundle": bundle,
                "root_map": root_map,
            }, indent=2, sort_keys=True))
        else:
            _publish(plan, bundle, root_map)
            print(json.dumps({
                "export": "EXPORT_OK",
                "bundle_name": plan["bundle_name"],
                "output_root": str(output_root),
                "file_count": len(bundle["files"]),
            }, sort_keys=True))
        return 0
    except (OSError, ValueError) as exc:
        print(f"BLOCKED: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
