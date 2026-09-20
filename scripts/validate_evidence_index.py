#!/usr/bin/env python3
"""P5_EVIDENCE_INDEX_V1 cross-root evidence index validator.

The audited v0 index (``retrieval/p5_evidence_index_v0.json``) stored
absolute host paths inside ``files[]``, carried stale digests, lacked
provenance fields, and had no automated check.  The V1 schema fixes
the shape — absolute paths are allowed ONLY under ``roots.<id>.path``;
every ``files[]`` entry is a ``(root_id, relpath)`` pair — and this
validator fails closed on schema violations, malformed digests, path
escapes, duplicates, self-reference, stale bytes, stale sidecars, and
incomplete provenance.

Usage::

    validate_evidence_index.py <index.json>
        [--root-map '<json object: root_id -> absolute path>']
        [--report-out <path>]

``--root-map`` overrides the on-disk locations recorded in
``roots.<id>.path`` (e.g. when the index was generated on another
host); it may be an inline JSON object or a path to a JSON file.

Emits ``{status, problems, files_checked, files_ok, ...}`` as JSON to
stdout (and to ``--report-out`` if given).  Exit 0 on INDEX_OK, 1 on
INDEX_FAIL.  READ-ONLY: the validator never writes into evidence
roots.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path, PurePosixPath, PureWindowsPath

SCHEMA_V1 = "P5_EVIDENCE_INDEX_V1"
CLAIM_SCOPE_V1 = "research_only_no_operational_authorization"
STATES_V1 = frozenset({"current", "immutable", "superseded"})

_HEX64 = re.compile(r"[0-9a-fA-F]{64}").fullmatch
_HEX40 = re.compile(r"[0-9a-fA-F]{40}").fullmatch
# Strict ISO-8601 UTC: trailing Z only — no offsets, no date-only,
# no space separator.
_ISO8601_Z = re.compile(
    r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?Z").fullmatch

REQUIRED_TOP_FIELDS = (
    "schema", "title", "generated_utc", "generator",
    "manifest_sha256", "roots", "files", "claim_scope",
)
REQUIRED_FILE_FIELDS = (
    "root_id", "relpath", "sha256", "size_bytes",
    "sidecar_sha256", "state", "activity", "entity_role",
)


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_path(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_absolute_string(s: str) -> bool:
    """True if the WHOLE string parses as an absolute path on POSIX or
    Windows semantics."""
    if not s:
        return False
    return (s.startswith(("/", "\\"))
            or PurePosixPath(s).is_absolute()
            or PureWindowsPath(s).is_absolute())


def _iter_strings(obj):
    """Yield every string value nested anywhere in a JSON object."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _iter_strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from _iter_strings(v)


def _check_relpath(relpath):
    """Return a problem string, or None if relpath is a clean relative
    path (no absolute form, no '..' escape, no NUL)."""
    if not isinstance(relpath, str) or not relpath.strip():
        return "relpath must be a non-empty string"
    if "\x00" in relpath:
        return "relpath contains a NUL byte"
    if _is_absolute_string(relpath):
        return (f"relpath {relpath!r} is an absolute path — absolute "
                f"paths are allowed only under roots.<id>.path")
    pp, wp = PurePosixPath(relpath), PureWindowsPath(relpath)
    if ".." in pp.parts or ".." in wp.parts:
        return f"relpath {relpath!r} escapes its root ('..' segment)"
    return None


def _check_generated_utc(value):
    """Strict ISO-8601 Z check.  Returns a problem string or None."""
    if not isinstance(value, str) or not _ISO8601_Z(value):
        return ("generated_utc must be strict ISO-8601 UTC "
                "(YYYY-MM-DDTHH:MM:SS[.ffffff]Z — trailing 'Z' required, "
                "no other offset)")
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return f"generated_utc {value!r} does not parse as a real datetime"
    return None


def _report(status, problems, files_checked, files_ok, index_path,
            schema=None):
    return {
        "status": status,
        "index": str(index_path),
        "schema": schema,
        "problems": list(problems),
        "files_checked": files_checked,
        "files_ok": files_ok,
    }


def _resolve(path: Path) -> Path:
    try:
        return path.resolve()
    except OSError:
        return path.absolute()


def validate_index(index_path, root_map=None):
    """Validate a V1 evidence index.  Returns the report dict.

    ``root_map``: optional {root_id: absolute path} override for live
    verification; when None, ``roots.<id>.path`` is used.
    """
    problems = []
    files_checked = 0
    files_ok = 0
    index_path = Path(index_path)
    index_resolved = _resolve(index_path)

    try:
        doc = json.loads(index_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError) as exc:
        return _report("INDEX_FAIL", [f"index unreadable: {exc}"],
                       0, 0, index_path)
    except json.JSONDecodeError as exc:
        return _report("INDEX_FAIL", [f"index is not valid JSON: {exc}"],
                       0, 0, index_path)
    if not isinstance(doc, dict):
        return _report("INDEX_FAIL",
                       ["index root must be a JSON object"],
                       0, 0, index_path)

    # ---- 1. schema + required top-level fields --------------------
    if doc.get("schema") != SCHEMA_V1:
        problems.append(
            f"schema must be {SCHEMA_V1!r} (got {doc.get('schema')!r})")
    for field in REQUIRED_TOP_FIELDS:
        if field not in doc:
            problems.append(f"missing required top-level field {field!r}")
    if "title" in doc and not (isinstance(doc["title"], str)
                               and doc["title"].strip()):
        problems.append("title must be a non-empty string")
    if "claim_scope" in doc and doc.get("claim_scope") != CLAIM_SCOPE_V1:
        problems.append(f"claim_scope must be {CLAIM_SCOPE_V1!r} "
                        f"(got {doc.get('claim_scope')!r})")

    # ---- 5. provenance completeness --------------------------------
    if "generator" in doc:
        gen = doc["generator"]
        if not isinstance(gen, dict):
            problems.append("generator must be an object "
                            "{agent, repo_commit}")
        else:
            if not (isinstance(gen.get("agent"), str)
                    and gen["agent"].strip()):
                problems.append("generator.agent must be a non-empty string")
            if not (isinstance(gen.get("repo_commit"), str)
                    and _HEX40(gen["repo_commit"])):
                problems.append("generator.repo_commit must be 40 hex chars")
    if "generated_utc" in doc:
        err = _check_generated_utc(doc["generated_utc"])
        if err:
            problems.append(err)
    if "manifest_sha256" in doc:
        if not (isinstance(doc["manifest_sha256"], str)
                and _HEX64(doc["manifest_sha256"])):
            problems.append("manifest_sha256 must be 64 hex chars")

    # ---- roots ----------------------------------------------------
    roots = doc.get("roots")
    declared_roots = set()
    if not isinstance(roots, dict) or not roots:
        problems.append("roots must be a non-empty object "
                        "{root_id: {path, role}}")
        roots = {}
    for rid, spec in roots.items():
        declared_roots.add(rid)
        if not isinstance(spec, dict):
            problems.append(f"roots.{rid}: must be an object "
                            f"{{path, role}}")
            continue
        rp = spec.get("path")
        if not (isinstance(rp, str) and rp.strip()):
            problems.append(f"roots.{rid}.path must be a non-empty string")
        elif not _is_absolute_string(rp):
            problems.append(f"roots.{rid}.path must be an absolute path "
                            f"(got {rp!r})")
        if not (isinstance(spec.get("role"), str)
                and spec["role"].strip()):
            problems.append(f"roots.{rid}.role must be a non-empty string")

    # ---- 3. effective root map + resolve check ---------------------
    effective = {rid: Path(spec["path"])
                 for rid, spec in roots.items()
                 if isinstance(spec, dict)
                 and isinstance(spec.get("path"), str) and spec["path"]}
    if root_map is not None:
        if not isinstance(root_map, dict):
            problems.append("--root-map must be a JSON object "
                            "{root_id: absolute path}")
        else:
            for rid, p in root_map.items():
                if rid not in declared_roots:
                    problems.append(f"--root-map key {rid!r} is not a "
                                    f"declared root_id")
                elif not (isinstance(p, str) and _is_absolute_string(p)):
                    problems.append(f"--root-map[{rid!r}] must be an "
                                    f"absolute path string")
                else:
                    effective[rid] = Path(p)
    resolved_roots = {}
    for rid, p in effective.items():
        rp = _resolve(p.expanduser())
        resolved_roots[rid] = rp
        if not rp.is_dir():
            problems.append(f"root {rid!r} does not resolve to an "
                            f"existing directory: {rp}")

    # ---- 2 + 4. file entries ----------------------------------------
    files = doc.get("files")
    if not isinstance(files, list):
        problems.append("files must be a list")
    else:
        seen = set()
        for i, entry in enumerate(files):
            ctx = f"files[{i}]"
            if not isinstance(entry, dict):
                problems.append(f"{ctx}: entry must be a JSON object")
                continue
            files_checked += 1
            entry_problems = []

            def bad(msg, _l=entry_problems):
                _l.append(msg)

            for field in REQUIRED_FILE_FIELDS:
                if field not in entry:
                    bad(f"missing field {field!r}")

            # No absolute paths ANYWHERE under files[] — the only
            # permitted absolute path in the document is roots.*.path.
            for s in _iter_strings(entry):
                if _is_absolute_string(s):
                    bad(f"absolute path {s!r} is forbidden under files[]")
                    break

            rid = entry.get("root_id")
            relpath = entry.get("relpath")
            rp_err = (_check_relpath(relpath)
                      if "relpath" in entry else None)
            if rp_err:
                bad(rp_err)
            if "root_id" in entry:
                if not isinstance(rid, str) or rid not in declared_roots:
                    bad(f"root_id {rid!r} is not declared in roots")

            sha = entry.get("sha256")
            sha_ok = (isinstance(sha, str) and bool(_HEX64(sha)))
            if "sha256" in entry and not sha_ok:
                bad("sha256 must be exactly 64 hex chars")

            sb = entry.get("size_bytes")
            sb_ok = (isinstance(sb, int) and not isinstance(sb, bool)
                     and sb >= 0)
            if "size_bytes" in entry and not sb_ok:
                bad("size_bytes must be a non-negative integer")

            sc = entry.get("sidecar_sha256")
            sc_ok = (sc is None
                     or (isinstance(sc, str) and bool(_HEX64(sc))))
            if "sidecar_sha256" in entry and not sc_ok:
                bad("sidecar_sha256 must be 64 hex chars or null")

            st = entry.get("state")
            if "state" in entry and st not in STATES_V1:
                bad(f"state {st!r} not in vocabulary "
                    f"{sorted(STATES_V1)}")

            if "activity" in entry:
                act = entry["activity"]
                if not (isinstance(act, str) and act.strip()):
                    bad("activity must be a non-empty string")
            if "entity_role" in entry:
                er = entry["entity_role"]
                if not (isinstance(er, str) and er.strip()):
                    bad("entity_role must be a non-empty string")

            # Duplicate logical path.
            if isinstance(rid, str) and isinstance(relpath, str):
                key = (rid, relpath)
                if key in seen:
                    bad(f"duplicate (root_id, relpath) pair {key!r}")
                seen.add(key)

            # Live verification — only when root_id and relpath are
            # structurally sane enough to resolve a target.
            if (isinstance(rid, str) and rid in resolved_roots
                    and rp_err is None):
                base = resolved_roots[rid]
                cand = base / relpath
                cand_r = _resolve(cand)
                try:
                    cand_r.relative_to(base)
                except ValueError:
                    bad(f"relpath {relpath!r} resolves outside root "
                        f"{rid!r}")
                # Self-reference: resolved path equality, plus samefile
                # (symlinks/hardlinks) when the basename matches.
                is_self = (cand_r == index_resolved)
                if not is_self and cand_r.name == index_resolved.name:
                    try:
                        is_self = cand_r.samefile(index_resolved)
                    except OSError:
                        pass
                if is_self:
                    bad("index lists itself (self-reference rejected)")
                if not cand.is_file():
                    bad(f"file missing on disk: {cand}")
                else:
                    disk_sha = _sha256_path(cand)
                    if sha_ok and disk_sha != sha:
                        bad(f"sha256 stale: index={sha[:16]}… "
                            f"disk={disk_sha[:16]}…")
                    disk_size = cand.stat().st_size
                    if sb_ok and disk_size != sb:
                        bad(f"size_bytes stale: index={sb} "
                            f"disk={disk_size}")
                # Sidecar verification: relpath + ".sha256", digest
                # recomputes, and payload's first whitespace token is
                # the file's sha256 (sha256sum format).
                if sc is not None and isinstance(sc, str) and _HEX64(sc):
                    scp = base / (relpath + ".sha256")
                    if not scp.is_file():
                        bad(f"sidecar missing on disk: {scp}")
                    else:
                        sc_bytes = scp.read_bytes()
                        if _sha256_bytes(sc_bytes) != sc:
                            bad(f"sidecar_sha256 stale for {scp.name}")
                        tokens = sc_bytes.split()
                        first = (tokens[0].decode("utf-8", "replace")
                                 if tokens else "")
                        if sha_ok and first != sha:
                            bad("sidecar payload first token does not "
                                "equal the file sha256")

            problems.extend(f"{ctx}: {m}" for m in entry_problems)
            if not entry_problems:
                files_ok += 1

    # ---- 4. supersedes digest ---------------------------------------
    sup = doc.get("supersedes")
    if sup is not None:
        if not isinstance(sup, dict):
            problems.append("supersedes must be an object "
                            "{relpath, sha256}")
        else:
            srel = sup.get("relpath")
            ssha = sup.get("sha256")
            sup_ok = True
            if not (isinstance(srel, str) and srel.strip()):
                problems.append("supersedes.relpath must be a "
                                "non-empty string")
                sup_ok = False
            if not (isinstance(ssha, str) and _HEX64(ssha)):
                problems.append("supersedes.sha256 must be 64 hex chars")
                sup_ok = False
            if sup_ok:
                if _is_absolute_string(srel):
                    candidates = [Path(srel)]
                elif (".." in PurePosixPath(srel).parts
                      or ".." in PureWindowsPath(srel).parts):
                    problems.append("supersedes.relpath must not "
                                    "contain '..'")
                    candidates = []
                else:
                    # Resolution order: alongside the index first,
                    # then each declared root.
                    candidates = [index_resolved.parent / srel]
                    candidates += [resolved_roots[r] / srel
                                   for r in resolved_roots]
                if candidates:
                    target = next((c for c in candidates if c.is_file()),
                                  None)
                    if target is None:
                        problems.append(f"supersedes target not found "
                                        f"on disk: {srel!r}")
                    elif _sha256_path(target) != ssha:
                        problems.append(f"supersedes.sha256 does not "
                                        f"match {srel!r} on disk")

    status = "INDEX_OK" if not problems else "INDEX_FAIL"
    return _report(status, problems, files_checked, files_ok,
                   index_path, schema=doc.get("schema"))


def _load_root_map(raw: str):
    """--root-map accepts an inline JSON object or a path to a JSON
    file containing one."""
    try:
        return json.loads(raw), None
    except json.JSONDecodeError:
        pass
    p = Path(raw)
    if p.is_file():
        try:
            return json.loads(p.read_text(encoding="utf-8")), None
        except (OSError, json.JSONDecodeError) as exc:
            return None, f"--root-map file unreadable: {exc}"
    return None, "--root-map is neither a JSON object nor a readable file"


def main(argv=None):
    ap = argparse.ArgumentParser(
        description="Validate a P5_EVIDENCE_INDEX_V1 cross-root "
                    "evidence index (read-only; JSON report to stdout).")
    ap.add_argument("index", help="path to the index JSON file")
    ap.add_argument("--root-map", default=None,
                    help="JSON object (or path to JSON file) mapping "
                         "root_id -> absolute path, overriding "
                         "roots.<id>.path for live verification")
    ap.add_argument("--report-out", default=None,
                    help="optional path to also write the JSON report")
    args = ap.parse_args(argv)

    root_map = None
    if args.root_map is not None:
        root_map, err = _load_root_map(args.root_map)
        if err is not None:
            report = _report("INDEX_FAIL", [err], 0, 0, args.index)
            text = json.dumps(report, indent=2, sort_keys=True)
            print(text)
            return 1

    report = validate_index(args.index, root_map=root_map)
    text = json.dumps(report, indent=2, sort_keys=True)
    if args.report_out:
        Path(args.report_out).write_text(text + "\n", encoding="utf-8")
    print(text)
    return 0 if report["status"] == "INDEX_OK" else 1


if __name__ == "__main__":
    sys.exit(main())
