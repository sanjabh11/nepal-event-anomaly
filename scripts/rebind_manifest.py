#!/usr/bin/env python3
"""Rebind ARTIFACT_MANIFEST_V0.json to a content commit — hardened CLI.

Incident-driven hardening (2026-09-21): the previous argv-based interface
accepted any string (including ``--help``) as a commit argument, trusted a
caller-supplied collection count, and wrote the canonical manifest
unconditionally.  The hardened interface:

- is NON-MUTATING by default; writing requires explicit ``--write``;
- validates the revision with
  ``git rev-parse --verify --quiet --end-of-options REV^{commit}`` so
  only real commit objects can be bound (G-02);
- validates ``--collection-count`` as a strict positive integer (G-12);
- binds exactly the existing entry set plus explicitly adopted paths
  (``--adopt``).  It never silently adds or removes entries, so the
  governed release surface cannot drift as a side effect of a rebind;
- fails closed if any bound entry is missing from disk or untracked;
- runs the scope audit (G-11): the governed boundary is a total partition
  over ``git ls-files`` — every tracked file is either bound, recorded in
  the scope-exclusion baseline, or the manifest itself.  A newly created
  file anywhere in the tree cannot silently escape the released surface;
  the rebind fails and the file must be explicitly adopted or explicitly
  recorded as out-of-scope;
- can independently re-measure the live collection count with
  ``--verify-collection`` and fail closed unless it equals the declared
  ``--collection-count`` (G-12);
- writes atomically (exclusive temp file + fsync + os.replace) and
  re-verifies every bound digest after the write (G-03).

Usage::

    rebind_manifest.py --content-commit REV [--collection-count N]
                       [--adopt PATH ...]
    rebind_manifest.py --content-commit REV --write
    rebind_manifest.py --audit-scope [--scope-exclusions PATH]

``REV`` must resolve to a real commit object.  Dry-run (default) prints
the exact bind plan and changes nothing on disk.  ``--audit-scope``
validates the recorded scope boundary and writes nothing.  The manifest
itself is self-excluded; the result is committed in a separate
manifest-only commit.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
MANIFEST = REPO / "docs/science/ARTIFACT_MANIFEST_V0.json"

MANIFEST_RELPATH = "docs/science/ARTIFACT_MANIFEST_V0.json"
SCOPE_EXCLUSIONS_RELPATH = "docs/science/MANIFEST_SCOPE_EXCLUSIONS_V0.json"
SCOPE_EXCLUSIONS_SCHEMA = "MANIFEST_SCOPE_EXCLUSIONS_V0"

# Root-level scope is fail-closed as well: every tracked root-level file must
# be bound, recorded as a scope exclusion, or be one of these declared root
# entries.  Anything else stops the rebind.
WATCH_ROOT_FILES = (".gitignore", "README.md", "conftest.py")

# The scope audit is a total partition over ``git ls-files``: every tracked
# file is either bound in the manifest, recorded in the scope-exclusion
# baseline, or the manifest itself.  Anything else fails the rebind, so a
# newly created file anywhere in the tree cannot silently escape the
# released surface (G-11).


class RebindError(Exception):
    """A fail-closed rebind validation or verification error."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _positive_int(raw: str) -> int:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise argparse.ArgumentTypeError(
            f"collection count must be a strict positive integer: {raw!r}")
    if value <= 0:
        raise argparse.ArgumentTypeError(
            f"collection count must be a strict positive integer: {raw!r}")
    return value


def resolve_commit(repo_root: Path, rev: str) -> str:
    """Return the full SHA of the commit object named by ``rev``.

    Uses ``git rev-parse --verify --quiet --end-of-options REV^{commit}``
    so that option-like strings, tree/blob objects, and dangling SHAs all
    fail closed (G-01/G-02).
    """
    rev = (rev or "").strip()
    if not rev:
        raise RebindError("content commit revision is empty")
    if rev.startswith("-"):
        raise RebindError(
            f"content commit revision must not look like an option: {rev!r}")
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "rev-parse", "--verify", "--quiet",
         "--end-of-options", f"{rev}^{{commit}}"],
        capture_output=True, text=True, check=False)
    sha = proc.stdout.strip()
    if proc.returncode != 0 or not re.fullmatch(r"[0-9a-f]{40}", sha):
        raise RebindError(
            f"content commit {rev!r} does not resolve to a commit object "
            f"(git rev-parse --verify rc={proc.returncode})")
    return sha


def tracked_files(repo_root: Path) -> list[str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), "ls-files", "-z"],
        capture_output=True, text=True, check=False)
    if proc.returncode != 0:
        raise RebindError(f"git ls-files failed: {proc.stderr.strip()}")
    return [p for p in proc.stdout.split("\0") if p]


def scope_audit(repo_root: Path, bound: set[str],
                exclusions: set[str], *,
                tracked: list[str] | None = None) -> dict:
    """Fail-closed governed-surface audit (G-11).

    Every tracked file under a watched governed prefix must be either bound
    in the manifest or named in the recorded scope-exclusion baseline.  A
    newly created governed file therefore cannot silently escape the
    manifest: it raises ``RebindError`` instead.  Returns a summary dict.
    """
    tracked = tracked if tracked is not None else tracked_files(repo_root)
    tracked_set = set(tracked)
    # Root-level scope is checked first so a newly created root file gets a
    # specific diagnostic; the repository-wide check below subsumes it.
    root_unbound = sorted(
        rel for rel in tracked
        if "/" not in rel and rel not in bound and rel not in exclusions
        and rel not in WATCH_ROOT_FILES)
    if root_unbound:
        raise RebindError(
            "root-level tracked files not bound to the manifest and not "
            "recorded as scope exclusions: " + ", ".join(root_unbound))
    # Fail-closed boundary over every tracked file: tracked == bound +
    # recorded exclusions + the self-excluded manifest.
    unbound = sorted(rel for rel in tracked
                     if rel not in bound and rel not in exclusions
                     and rel != MANIFEST_RELPATH)
    if unbound:
        raise RebindError(
            "tracked files not bound to the manifest and not recorded as "
            "scope exclusions: " + ", ".join(unbound))
    stale = sorted(rel for rel in exclusions if rel not in tracked_set)
    if stale:
        raise RebindError(
            "recorded scope-exclusion baseline lists untracked or deleted "
            "paths: " + ", ".join(stale))
    if MANIFEST_RELPATH in exclusions:
        raise RebindError(
            "manifest may not be a scope exclusion: " + MANIFEST_RELPATH)
    bound_also_excluded = sorted(exclusions & bound)
    if bound_also_excluded:
        raise RebindError(
            "paths are both bound and recorded as scope exclusions "
            "(ambiguous boundary): " + ", ".join(bound_also_excluded))
    return {
        "status": "SCOPE_AUDIT_OK",
        "tracked_files": len(tracked),
        "bound_files": len(bound),
        "scope_exclusions": len(exclusions),
        "stale_scope_exclusions": stale,
        "bound_also_excluded": bound_also_excluded,
        "unbound_governed_files": unbound,
        "root_level_unbound": root_unbound,
        "watched_root_files": list(WATCH_ROOT_FILES),
        "manifest_self_excluded": MANIFEST_RELPATH,
        "manifest_relpath": MANIFEST_RELPATH,
        "scope_exclusions_relpath": SCOPE_EXCLUSIONS_RELPATH,
    }


def load_scope_exclusions(path: Path) -> set[str]:
    """Load the recorded scope-exclusion baseline (logical relpaths only)."""
    if not path.is_file():
        raise RebindError(f"scope-exclusion baseline missing: {path}")
    doc = json.loads(path.read_text(encoding="utf-8"))
    if doc.get("schema") != SCOPE_EXCLUSIONS_SCHEMA:
        raise RebindError(
            f"scope-exclusion schema must be {SCOPE_EXCLUSIONS_SCHEMA}")
    entries = doc.get("exclusions")
    if not isinstance(entries, list):
        raise RebindError("scope-exclusion baseline needs an 'exclusions' list")
    out = set()
    for entry in entries:
        if not isinstance(entry, dict) or "relpath" not in entry \
                or "reason" not in entry:
            raise RebindError(
                "each scope exclusion needs 'relpath' and 'reason'")
        out.add(_normalize_relpath(str(entry["relpath"])))
    if MANIFEST_RELPATH in out:
        raise RebindError("manifest may not be a scope exclusion")
    return out



def live_collection_count(repo_root: Path) -> int:
    """Return the live ``pytest --collect-only`` count for ``repo_root``.

    Used by ``--verify-collection`` so a caller-supplied count is bound to
    the actual collection result rather than trusted (G-12).
    """
    proc = subprocess.run(
        [sys.executable, "-B", "-m", "pytest", "tests/", "--collect-only",
         "-q"],
        cwd=str(repo_root), capture_output=True, text=True, check=False,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"})
    match = re.search(r"(\d+) tests? collected", proc.stdout)
    if proc.returncode != 0 or not match:
        raise RebindError("could not determine live collection count: "
                          + (proc.stderr.strip()[-400:] or "no count found"))
    return int(match.group(1))


def _normalize_relpath(rel: str) -> str:
    rel = (rel or "").replace(os.sep, "/").strip()
    if not rel or rel.startswith("/") or ".." in Path(rel).parts:
        raise RebindError(f"invalid repository-relative path: {rel!r}")
    return rel


def baseline_self_rel(repo_root: Path, scope_path: Path,
                      manifest_path: Path) -> set[str]:
    """Return {scope-baseline relpath} when it is not the manifest itself.

    The scope-exclusion baseline is the boundary record, not governed
    surface; the self-excluded manifest is handled by ``scope_audit``
    explicitly.
    """
    if scope_path.resolve() == manifest_path.resolve():
        return set()
    try:
        return {scope_path.resolve().relative_to(repo_root).as_posix()}
    except ValueError:
        return set()


def compute_rebind(manifest: dict, repo_root: Path, content_commit: str,
                   collection_count: int | None,
                   *, adopt: tuple[str, ...] = (),
                   scope_exclusions: set[str] | None = None
                   ) -> tuple[dict, dict]:
    """Return (rebound_manifest, report) without touching the filesystem.

    Binds exactly the existing entry set plus explicit ``adopt`` paths.
    Fails closed when a bound entry is missing from disk or untracked, and
    when the scope audit finds a watched file that is neither bound nor
    recorded as excluded (G-11).
    """
    manifest_rel = MANIFEST_RELPATH
    entries = {e["relpath"]: dict(e) for e in manifest["files"]}
    if manifest_rel in entries:
        raise RebindError("manifest lists itself; self-exclusion violated")

    tracked = tracked_files(repo_root)
    tracked_set = set(tracked)

    missing = [rel for rel in sorted(entries)
               if not (repo_root / rel).is_file()]
    if missing:
        raise RebindError(
            "governed files missing from disk: " + ", ".join(missing))
    untracked_bound = [rel for rel in sorted(entries)
                       if rel not in tracked_set]
    if untracked_bound:
        raise RebindError("bound entries are not tracked by git: "
                          + ", ".join(untracked_bound))

    added = []
    for raw in adopt:
        rel = _normalize_relpath(raw)
        if rel == manifest_rel:
            raise RebindError("cannot adopt the manifest into itself")
        if rel in entries:
            raise RebindError(f"already bound: {rel}")
        if rel not in tracked_set:
            raise RebindError(f"adopted path is not tracked by git: {rel}")
        if not (repo_root / rel).is_file():
            raise RebindError(f"adopted path is missing from disk: {rel}")
        entries[rel] = {"relpath": rel}
        added.append(rel)

    files = []
    for rel in sorted(entries):
        p = repo_root / rel
        data = p.read_bytes()
        files.append({"relpath": rel, "sha256": sha256_bytes(data),
                      "size_bytes": len(data)})

    rebound = json.loads(json.dumps(manifest))  # deep copy
    rebound["files"] = files
    rebound["content_head"] = content_commit
    rebound["manifest_commit"] = content_commit
    if collection_count is not None:
        guard = rebound.get("test_results", {}).get("collection_guard")
        if not guard or not re.search(r"\(\d+ nodes?\)", guard):
            raise RebindError(
                f"collection_guard missing or unrecognized: {guard!r}")
        rebound["test_results"]["collection_guard"] = re.sub(
            r"\(\d+ nodes?\)", f"({collection_count} nodes)", guard)

    audit = scope_audit(repo_root, {e["relpath"] for e in files},
                        scope_exclusions or set(), tracked=tracked)
    report = {"added": added, "file_count": len(files), "audit": audit}
    return rebound, report



def write_manifest_atomic(path: Path, doc: dict) -> None:
    """Exclusive temp file + fsync + atomic replace (G-03)."""
    payload = json.dumps(doc, indent=1, sort_keys=True) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(path.parent), prefix=".rebind-", suffix=".tmp")
    tmp = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload.encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        if tmp.exists():
            tmp.unlink()
        raise
    dir_fd = os.open(str(path.parent), os.O_RDONLY)
    try:
        os.fsync(dir_fd)
    finally:
        os.close(dir_fd)


def verify_manifest(path: Path, repo_root: Path,
                    expected_head: str) -> list[str]:
    """Recompute every bound entry from live bytes (G-03 post-write)."""
    problems = []
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("content_head") != expected_head:
        problems.append(f"content_head != {expected_head}")
    if manifest.get("manifest_commit") != expected_head:
        problems.append(f"manifest_commit != {expected_head}")
    seen = set()
    for entry in manifest["files"]:
        rel = entry["relpath"]
        if rel in seen:
            problems.append(f"duplicate manifest entry: {rel}")
            continue
        seen.add(rel)
        p = repo_root / rel
        if not p.is_file():
            problems.append(f"missing governed file: {rel}")
            continue
        data = p.read_bytes()
        if sha256_bytes(data) != entry["sha256"]:
            problems.append(f"digest mismatch: {rel}")
        if len(data) != entry["size_bytes"]:
            problems.append(f"size mismatch: {rel}")
    if len(manifest["files"]) != len(seen):
        problems.append("file count mismatch")
    return problems


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebind the governed artifact manifest to a content "
                    "commit. Non-mutating unless --write is given.")
    parser.add_argument("--content-commit", default=None, metavar="REV",
                        help="revision that must resolve to a commit object "
                             "(required unless --audit-scope)")
    parser.add_argument("--collection-count", type=_positive_int,
                        default=None, metavar="N",
                        help="measured repo-wide collection count (strict "
                             "positive integer)")
    parser.add_argument("--adopt", action="append", default=[], metavar="PATH",
                        help="explicitly bind an additional tracked path "
                             "(repeatable); rebind never auto-adds entries")
    parser.add_argument("--audit-scope", action="store_true",
                        help="validate the governed scope boundary only; "
                             "writes nothing and requires no revision")
    parser.add_argument("--verify-collection", action="store_true",
                        help="independently re-run pytest --collect-only and "
                             "fail closed unless it equals --collection-count "
                             "(G-12 binding)")
    parser.add_argument("--scope-exclusions", default=None, metavar="PATH",
                        help="recorded scope-exclusion baseline (JSON); "
                             "defaults to <repo-root>/" +
                             SCOPE_EXCLUSIONS_RELPATH)
    parser.add_argument("--write", action="store_true",
                        help="write the rebound manifest (atomic); without "
                             "this flag the command is a dry-run")
    parser.add_argument("--dry-run", action="store_true",
                        help="explicit dry-run (the default); mutually "
                             "exclusive with --write")
    parser.add_argument("--repo-root", default=str(REPO),
                        help="repository root (override for tests)")
    parser.add_argument("--manifest", default=str(MANIFEST),
                        help="manifest JSON path (override for tests)")
    args = parser.parse_args(argv)
    if args.write and args.dry_run:
        print("REBIND_REFUSED: --write and --dry-run are mutually exclusive")
        return 2
    if args.audit_scope and args.write:
        print("REBIND_REFUSED: --audit-scope writes nothing; drop --write")
        return 2
    try:
        repo_root = Path(args.repo_root).resolve()
        manifest_path = Path(args.manifest).resolve()
        scope_path = (Path(args.scope_exclusions).resolve()
                      if args.scope_exclusions else
                      repo_root / SCOPE_EXCLUSIONS_RELPATH)
        scopes = load_scope_exclusions(scope_path)
        if args.audit_scope:
            bound = {e["relpath"] for e in
                     json.loads(manifest_path.read_text(
                         encoding="utf-8"))["files"]}
            scopes = set(scopes)
            bound |= {_normalize_relpath(rel) for rel in args.adopt}
            # The baseline is the boundary record, not governed surface
            # (see baseline_self_rel); the manifest stays self-excluded.
            scopes |= baseline_self_rel(repo_root, scope_path, manifest_path)
            audit = scope_audit(repo_root, bound, scopes)
            audit = scope_audit(repo_root, bound, scopes)
            print(f"SCOPE_AUDIT_OK: {audit['tracked_files']} tracked files, "
                  f"{audit['bound_files']} bound, "
                  f"{audit['scope_exclusions']} recorded exclusions")
            print("SCOPE_AUDIT_NO_WRITE: nothing was written")
            return 0
        if args.content_commit is None:
            print("REBIND_REFUSED: --content-commit is required for a rebind "
                  "(or use --audit-scope)")
            return 2
        before = manifest_path.read_bytes()
        head = resolve_commit(repo_root, args.content_commit)
        if args.verify_collection:
            if args.collection_count is None:
                print("REBIND_REFUSED: --verify-collection requires "
                      "--collection-count")
                return 2
            measured = live_collection_count(repo_root)
            if measured != args.collection_count:
                print(f"REBIND_VERIFY_FAIL: collection count mismatch: "
                      f"declared {args.collection_count}, measured {measured}")
                return 1
            print(f"REBIND_COLLECTION_OK: {measured} nodes measured "
                  "independently of the caller")
        manifest = json.loads(before.decode("utf-8"))
        rebound, report = compute_rebind(
            manifest, repo_root, head, args.collection_count,
            adopt=tuple(args.adopt),
            scope_exclusions=scopes | baseline_self_rel(
                repo_root, scope_path, manifest_path))
        audit = report["audit"]
        mode = "WRITE" if args.write else "DRY-RUN"
        print(f"REBIND_{mode}: bind head {head}")
        print(f"  governed files: {report['file_count']}")
        print(f"  adopted this run: {len(report['added'])}"
              + (f" -> {', '.join(report['added'])}" if report["added"]
                 else ""))
        print(f"  scope audit: {audit['status']} "
              f"({audit['tracked_files']} tracked, "
              f"{audit['scope_exclusions']} recorded exclusions)")
        if args.write:
            write_manifest_atomic(manifest_path, rebound)
            after = manifest_path.read_bytes()
            problems = verify_manifest(manifest_path, repo_root, head)
            if problems:
                print("REBIND_VERIFY_FAIL: " + "; ".join(problems))
                return 1
            if after == before:
                print(f"REBIND_NOOP_OK: manifest already bound to {head}; "
                      f"{len(rebound['files'])} entries re-verified, no "
                      "content change")
                return 0
            print(f"REBIND_VERIFY_OK: {len(rebound['files'])} entries "
                  "recomputed from live bytes")
        else:
            print("REBIND_DRY_RUN_OK: no bytes written")
        return 0
    except (RebindError, OSError, json.JSONDecodeError, KeyError) as exc:
        print(f"REBIND_FAIL: {exc}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
