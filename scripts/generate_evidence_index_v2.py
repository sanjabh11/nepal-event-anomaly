#!/usr/bin/env python3
"""Generate an exhaustive ``P5_EVIDENCE_INDEX_V2``.

V2 inventories every regular non-sidecar payload below the supplied root
mapping.  Nested logical roots are explicit partitions of one physical root;
their payloads are assigned to the deepest declared logical root exactly once.
The generator is read-only with respect to evidence payloads and uses
exclusive creation for generated outputs.

Usage::

    generate_evidence_index_v2.py --root-map MAP [--index-out PATH]
        [--report-out PATH] [--supersedes RELPATH]

``MAP`` is either an inline JSON object or a JSON file.  A mapping value may
be a path string or an object with ``path``, ``role``, ``kind``
(``physical``/``logical``), ``physical_root_id``, and ``path_prefix``.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import validate_evidence_index as vei
from p5_safe_io import write_once_bytes, write_once_sidecar


REPO = Path(__file__).resolve().parents[1]
MANIFEST_DEFAULT = REPO / "docs/science/ARTIFACT_MANIFEST_V0.json"
SCHEMA = vei.SCHEMA_V2


class GenerationError(ValueError):
    """A fail-closed input or inventory error."""


def _sha256_path(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_json_value(raw: str, label: str):
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        path = Path(raw)
        if not path.is_file():
            raise GenerationError(
                f"{label} is neither inline JSON nor a readable JSON file")
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise GenerationError(f"{label} file unreadable: {exc}") from exc


def _mapping_object(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, list):
        result = {}
        for item in value:
            if not isinstance(item, dict):
                raise GenerationError("root mapping list entries must be objects")
            rid = item.get("root_id", item.get("id"))
            if not isinstance(rid, str) or not rid.strip():
                raise GenerationError(
                    "root mapping list entries require a non-empty root_id")
            if rid in result:
                raise GenerationError(f"duplicate root_id in root mapping: {rid}")
            result[rid] = {k: v for k, v in item.items()
                           if k not in {"root_id", "id"}}
        return result
    raise GenerationError("root mapping must be a JSON object or list")


def _canonical_roots(raw_mapping):
    mapping = _mapping_object(raw_mapping)
    canonical = {}
    for rid, raw in mapping.items():
        if isinstance(raw, str):
            raw = {"path": raw}
        if not isinstance(raw, dict):
            raise GenerationError(f"root mapping {rid!r} must be a path/object")
        if not isinstance(rid, str) or not rid.strip():
            raise GenerationError("root mapping ids must be non-empty strings")
        path = raw.get("path")
        if not isinstance(path, str) or not path.strip():
            raise GenerationError(f"root mapping {rid!r} requires path")
        path_resolved = Path(path).expanduser().resolve()
        role = raw.get("role", f"evidence root {rid}")
        if not isinstance(role, str) or not role.strip():
            raise GenerationError(f"root mapping {rid!r} role is empty")
        kind = vei._v2_root_kind(raw)
        physical_id = raw.get("physical_root_id")
        if physical_id is None:
            physical_id = raw.get("parent_root_id", raw.get("parent"))
        if kind == "physical":
            physical_id = rid
        prefix = raw.get("path_prefix")
        if prefix is None:
            prefix = raw.get("logical_relpath",
                            raw.get("partition_relpath"))
        if prefix is None and kind == "physical":
            prefix = ""
        item = {
            "path": str(path_resolved),
            "role": role,
            "kind": kind,
            "physical_root_id": physical_id,
        }
        if prefix is not None:
            item["path_prefix"] = prefix
        canonical[rid] = item
    if not canonical:
        raise GenerationError("at least one root mapping is required")

    problems = []
    specs = vei._v2_root_specs(canonical, None, problems)
    if problems:
        raise GenerationError("invalid root mapping: " + "; ".join(problems))
    # Store the normalized path prefix computed by the validator for logical
    # roots whose prefix was intentionally omitted in the input.
    for rid, spec in specs.items():
        canonical[rid]["path_prefix"] = spec["path_prefix"] or ""
    return canonical, specs


def _load_exclusions(raw, specs):
    if raw is None:
        return []
    value = _load_json_value(raw, "--exclusions")
    if not isinstance(value, list):
        raise GenerationError("--exclusions must contain a JSON list")
    result = []
    for i, item in enumerate(value):
        if not isinstance(item, dict):
            raise GenerationError(f"exclusions[{i}] must be an object")
        rid = item.get("root_id")
        relpath = item.get("relpath")
        reason = item.get("reason")
        if rid not in specs:
            raise GenerationError(
                f"exclusions[{i}].root_id {rid!r} is not declared")
        if not isinstance(relpath, str) or vei._check_relpath(relpath):
            raise GenerationError(f"exclusions[{i}].relpath is not relative")
        if not isinstance(reason, str) or not reason.strip():
            raise GenerationError(f"exclusions[{i}].reason is required")
        result.append({"root_id": rid, "relpath": relpath,
                       "reason": reason})
    return result


def _logical_assignment(path: Path, specs):
    resolved = path.expanduser().resolve()
    candidates = []
    for rid, spec in specs.items():
        try:
            resolved.relative_to(spec["path_resolved"])
        except ValueError:
            continue
        candidates.append(rid)
    if not candidates:
        return None
    deepest = max(len(specs[r]["path_resolved"].parts) for r in candidates)
    best = [rid for rid in candidates
            if len(specs[rid]["path_resolved"].parts) == deepest]
    if len(best) != 1:
        raise GenerationError(
            f"output path has duplicate logical root assignment: "
            + ", ".join(sorted(best)))
    rid = best[0]
    try:
        relpath = path.expanduser().relative_to(
            specs[rid]["path_resolved"]).as_posix()
    except ValueError:
        relpath = resolved.relative_to(specs[rid]["path_resolved"]).as_posix()
    if not relpath or relpath == ".":
        return None
    return rid, relpath


def _append_output_exclusion(exclusions, output, specs, label):
    if output is None:
        return
    assignment = _logical_assignment(output, specs)
    if assignment is None:
        return
    rid, relpath = assignment
    key = (rid, relpath)
    if any((e["root_id"], e["relpath"]) == key for e in exclusions):
        return
    exclusions.append({
        "root_id": rid,
        "relpath": relpath,
        "reason": f"{label} is an index-generation output, not evidence payload",
    })


def _supersedes_target(raw, specs, index_out):
    if raw is None:
        candidates = []
        for rid in sorted(specs):
            for relpath in (
                    "retrieval/p5_evidence_index_v1.json",
                    "p5_evidence_index_v1.json"):
                candidate = specs[rid]["path_resolved"] / relpath
                if candidate.is_file():
                    candidates.append((rid, relpath, candidate))
        if not candidates:
            return None
        return candidates[0]

    if vei._is_absolute_string(raw):
        raise GenerationError(
            "supersedes path must be relative/logical in V2")
    root_id = None
    relpath = raw
    if isinstance(raw, str) and ":" in raw:
        possible_root, possible_rel = raw.split(":", 1)
        if possible_root in specs:
            root_id, relpath = possible_root, possible_rel
    if not isinstance(relpath, str) or vei._check_relpath(relpath):
        raise GenerationError("supersedes path must be a safe relative path")

    candidates = []
    if root_id is not None:
        candidates.append((root_id,
                          specs[root_id]["path_resolved"] / relpath))
    else:
        candidates.append((None, index_out.parent / relpath))
        candidates.extend((rid, spec["path_resolved"] / relpath)
                           for rid, spec in specs.items())
    for candidate_root, candidate in candidates:
        if candidate.is_file():
            if candidate_root is None:
                assignment = _logical_assignment(candidate, specs)
                if assignment is None:
                    raise GenerationError(
                        "supersedes target is outside supplied roots")
                candidate_root, relpath = assignment
            return candidate_root, relpath, candidate
    raise GenerationError(f"supersedes target not found: {raw!r}")


def _manifest_binding(manifest_path: Path, repo_root: Path):
    if not manifest_path.is_file():
        raise GenerationError(f"manifest not found: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise GenerationError(f"manifest unreadable: {exc}") from exc
    content_head = manifest.get("content_head")
    manifest_commit = manifest.get("manifest_commit")
    if not (isinstance(content_head, str) and vei._HEX40(content_head)
            and isinstance(manifest_commit, str)
            and vei._HEX40(manifest_commit)):
        raise GenerationError("manifest content_head/manifest_commit are invalid")
    if content_head != manifest_commit:
        raise GenerationError(
            "manifest_commit must equal content_head before V2 generation")
    try:
        relpath = manifest_path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError:
        relpath = manifest_path.name
    digest = _sha256_path(manifest_path)
    return {
        "relpath": relpath,
        "sha256": digest,
        "content_head": content_head,
        "manifest_commit": manifest_commit,
    }


def _repo_head(repo_root: Path):
    proc = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo_root,
        capture_output=True, text=True, check=False)
    head = proc.stdout.strip()
    if proc.returncode != 0 or not vei._HEX40(head):
        raise GenerationError("repository HEAD is not a resolvable 40-hex commit")
    return head


def build_index(root_mappings, index_out, *, report_out=None,
                manifest_path=MANIFEST_DEFAULT, supersedes=None,
                exclusions=None, repo_root=REPO, generated_utc=None,
                detached_closure=None):
    """Build and write one V2 index; return ``(index_doc, report)``.

    ``root_mappings`` is the parsed mapping object or list accepted by the
    CLI.  The function intentionally refuses existing index/report/sidecar
    outputs before scanning or writing any payload.
    """
    index_out = Path(index_out).expanduser().resolve()
    report_out = (Path(report_out).expanduser().resolve()
                  if report_out is not None else None)
    detached_closure = (Path(detached_closure).expanduser().resolve()
                        if detached_closure is not None else None)
    if index_out.exists():
        raise GenerationError(f"refusing to overwrite existing index: {index_out}")
    if report_out is not None and report_out.exists():
        raise GenerationError(
            f"refusing to overwrite existing report: {report_out}")
    if report_out is not None and report_out == index_out:
        raise GenerationError("index output and report output must differ")
    index_sidecar = Path(str(index_out) + vei.SIDECAR_SUFFIX_V2)
    if index_sidecar.exists():
        raise GenerationError(
            f"refusing to overwrite existing index sidecar: {index_sidecar}")

    roots, specs = _canonical_roots(root_mappings)
    inventory_problems = []
    inventory = vei._v2_inventory(specs, inventory_problems)
    if inventory_problems:
        raise GenerationError("inventory failed: " + "; ".join(inventory_problems))

    exclusions = _load_exclusions(exclusions, specs)
    _append_output_exclusion(exclusions, index_out, specs, "index output")
    _append_output_exclusion(exclusions, report_out, specs, "report output")
    _append_output_exclusion(
        exclusions, detached_closure, specs, "detached release closure")
    excluded_keys = {(e["root_id"], e["relpath"]) for e in exclusions}
    if len(excluded_keys) != len(exclusions):
        raise GenerationError("duplicate exclusion assignment")

    # Explicit exclusions must name existing payloads.  Generated outputs
    # are the only allowed not-yet-existing exclusions at this point.
    output_keys = set()
    for output, label in ((index_out, "index output"),
                          (report_out, "report output"),
                          (detached_closure, "detached release closure")):
        assignment = _logical_assignment(output, specs) if output else None
        if assignment:
            output_keys.add(assignment)
    for exclusion in exclusions:
        key = (exclusion["root_id"], exclusion["relpath"])
        if key in output_keys:
            continue
        path = specs[key[0]]["path_resolved"] / key[1]
        if not path.is_file():
            raise GenerationError(
                f"explicit exclusion is not an on-disk payload: {key!r}")
        if key not in inventory:
            raise GenerationError(
                f"explicit exclusion is not in deterministic inventory: {key!r}")

    missing_sidecars = []
    files = []
    for key in sorted(inventory):
        if key in excluded_keys:
            continue
        item = inventory[key]
        payload = item["path"]
        sidecar = Path(str(payload) + vei.SIDECAR_SUFFIX_V2)
        if not sidecar.is_file():
            missing_sidecars.append(f"{key[0]}:{key[1]}")
            continue
        payload_sha = _sha256_path(payload)
        sidecar_bytes = sidecar.read_bytes()
        first = sidecar_bytes.split()[0].decode("utf-8", "replace") \
            if sidecar_bytes.split() else ""
        if first != payload_sha:
            raise GenerationError(
                f"sidecar payload digest mismatch for {key[0]}:{key[1]}")
        files.append({
            "root_id": key[0],
            "relpath": key[1],
            "sha256": payload_sha,
            "size_bytes": payload.stat().st_size,
            "sidecar_sha256": _sha256_path(sidecar),
            "sidecar_exception": None,
            "state": "current",
            "activity": "deterministic exhaustive non-sidecar inventory",
            "entity_role": "payload",
        })
    if missing_sidecars:
        raise GenerationError(
            "included payloads require sidecars: " + ", ".join(missing_sidecars))

    generated = (generated_utc or datetime.now(timezone.utc).strftime(
        "%Y-%m-%dT%H:%M:%SZ"))
    manifest = _manifest_binding(Path(manifest_path).expanduser().resolve(),
                                 Path(repo_root).resolve())
    head = _repo_head(Path(repo_root).resolve())
    supersedes_target = _supersedes_target(supersedes, specs, index_out)
    supersedes_doc = None
    if supersedes_target is not None:
        rid, relpath, target = supersedes_target
        supersedes_doc = {"root_id": rid, "relpath": relpath,
                          "sha256": _sha256_path(target)}

    physical_ids = sorted(rid for rid, spec in specs.items()
                          if spec["kind"] == "physical")
    logical_ids = sorted(rid for rid, spec in specs.items()
                        if spec["kind"] == "logical")
    partitions = [{
        "root_id": rid,
        "kind": specs[rid]["kind"],
        "physical_root_id": specs[rid]["physical_root_id"],
        "path_prefix": specs[rid]["path_prefix"] or "",
    } for rid in sorted(specs)]
    topology = {
        "kind": "partitioned_logical_roots",
        "physical_roots": physical_ids,
        "logical_roots": logical_ids,
        "partitions": partitions,
    }
    coverage_scope = {
        "mode": "exhaustive",
        "payload_definition": "all_non_sidecar_payload_files",
        "sidecar_suffix": vei.SIDECAR_SUFFIX_V2,
        "root_ids": sorted(specs),
    }
    # Outputs do not exist when the inventory is built, but they will be
    # regular payload files by the time the just-written index is validated.
    planned_output_count = len(output_keys - set(inventory))
    inventory_count = len(inventory) + planned_output_count
    included_count = len(files)
    excluded_count = len(exclusions)
    closure = {
        "status": "CLOSED",
        "inventory_coverage": "PASS",
        "sidecar_validation": "PASS",
        "exclusions": "PASS",
        "duplicate_physical_assignment": "PASS",
        "manifest_head_consistency": "PASS",
        "no_self_reference": "PASS",
    }
    final_verification = {
        "status": "CLOSED",
        "verified_utc": generated,
        "closure": closure,
        "counts": {
            "payload_files": inventory_count,
            "included_files": included_count,
            "excluded_files": excluded_count,
        },
    }
    doc = {
        "schema": SCHEMA,
        "title": "Exhaustive cross-root evidence index v2",
        "generated_utc": generated,
        "generator": {
            "agent": "audit-3 evidence-index v2 generator",
            "repo_commit": head,
            "tool": "scripts/generate_evidence_index_v2.py",
        },
        "manifest_sha256": manifest["sha256"],
        "manifest": manifest,
        "content_head": manifest["content_head"],
        "manifest_commit": manifest["manifest_commit"],
        "roots": roots,
        "topology": topology,
        "coverage_scope": coverage_scope,
        "exclusions": sorted(exclusions,
                              key=lambda e: (e["root_id"], e["relpath"])),
        "files": files,
        "final_verification": final_verification,
        "claim_scope": vei.CLAIM_SCOPE_V2,
        "supersedes": supersedes_doc,
    }
    encoded = json.dumps(doc, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    index_out.parent.mkdir(parents=True, exist_ok=True)
    try:
        index_digest = write_once_bytes(index_out, encoded)
        write_once_sidecar(index_out)
    except (OSError, FileExistsError) as exc:
        raise GenerationError(f"could not create index output: {exc}") from exc

    report = {
        "status": "INDEX_OK",
        "schema": SCHEMA,
        "index": str(index_out),
        "index_sha256": index_digest,
        "files_indexed": included_count,
        "payload_files": inventory_count,
        "excluded_files": excluded_count,
        "coverage_scope": coverage_scope,
    }
    if report_out is not None:
        report_out.parent.mkdir(parents=True, exist_ok=True)
        try:
            write_once_bytes(
                report_out,
                (json.dumps(report, indent=2, sort_keys=True) + "\n")
                .encode("utf-8"))
        except (OSError, FileExistsError) as exc:
            raise GenerationError(f"could not create report output: {exc}") from exc
    return doc, report


generate_index = build_index


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Generate an exhaustive P5_EVIDENCE_INDEX_V2 without "
                    "overwriting output or evidence payloads.")
    parser.add_argument("--root-map", "--root-mappings", "--root-mapping",
                        required=True,
                        dest="root_map",
                        help="inline JSON or JSON file mapping logical root "
                             "ids to physical/logical root declarations")
    parser.add_argument("--index-out", "--index-output", default=None,
                        help="index JSON output (exclusive-create)")
    parser.add_argument("--report-out", "--report-output", default=None,
                        help="optional JSON report output (exclusive-create)")
    parser.add_argument("--supersedes", default=None,
                        help="relative/logical superseded index path, or "
                             "root_id:relative/path")
    parser.add_argument("--exclusions", default=None,
                        help="inline JSON or JSON file containing explicit "
                             "exclusion objects")
    parser.add_argument("--detached-closure", default=None,
                        help="future exclusive-created closure path to "
                             "exclude from the index inventory")
    parser.add_argument("--manifest", default=str(MANIFEST_DEFAULT),
                        help="manifest JSON used for head consistency")
    parser.add_argument("--repo-root", default=str(REPO),
                        help="repository root used for git HEAD and manifest "
                             "relative provenance")
    args = parser.parse_args(argv)
    try:
        raw_mapping = _load_json_value(args.root_map, "--root-map")
        _, specs = _canonical_roots(raw_mapping)
        index_out = (Path(args.index_out).expanduser()
                     if args.index_out else
                     specs[sorted(specs)[0]]["path_resolved"] /
                     "retrieval/p5_evidence_index_v2.json")
        build_index(raw_mapping, index_out, report_out=args.report_out,
                    manifest_path=args.manifest, supersedes=args.supersedes,
                    exclusions=args.exclusions, repo_root=args.repo_root,
                    detached_closure=args.detached_closure)
        result = {"status": "INDEX_OK", "index": str(index_out.resolve())}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 0
    except (GenerationError, OSError, ValueError) as exc:
        result = {"status": "INDEX_FAIL", "problems": [str(exc)]}
        print(json.dumps(result, indent=2, sort_keys=True))
        return 1


if __name__ == "__main__":
    sys.exit(main())
