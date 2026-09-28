#!/usr/bin/env python3
"""Machine-generated P5 test-suite receipt (P5_SUITE_RECEIPT_V2, A4-03).

The v4 release closure never accepts free-form suite counts.  It binds a
receipt produced by THIS generator, which runs the suite itself and
parses the pytest summary tail.  The generator is fail-closed: if the
summary cannot be parsed, or the collection count does not equal
passed+skipped+failed+errors, no receipt is emitted.

V2 freshness binding (A4-03/13/15/17): the receipt captures the live
manifest bytes (``manifest_sha256``), the manifest's ``content_head``
and ``manifest_commit``, and the live repository HEAD BEFORE pytest
runs, so the receipt binds the tree under test rather than a stale
head.  ``execution_window`` records explicit activity timing — test
execution timestamps are never data maximum dates (``data_context``
states this boundary in the emitted document).

Publication is exclusive-create only (write_once_json +
write_once_sidecar); an existing --out is fatal.  Default is --dry-run:
run the suite, print the receipt, write nothing.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import subprocess
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
from p5_safe_io import (  # noqa: E402
    sha256_bytes, write_once_json, write_once_sidecar)
from build_p5_release_closure_v4 import (  # noqa: E402
    ClosureError, _environment, _head, _utc_now)

MANIFEST_DEFAULT = REPO / "docs/science/ARTIFACT_MANIFEST_V0.json"

_HEX40 = re.compile(r"[0-9a-fA-F]{40}").fullmatch

_SUMMARY_RE = re.compile(
    r"^=*\s*(?P<body>(?:\d+\s+(?:passed|skipped|failed|errors?|warnings?|"
    r"deselected|xfailed|xpassed)[,\s]*)+)\s*in\s+[\d.]+s"
    r"(?:\s*\([\d:]+\))?\s*=*\s*$")
_COUNT_RE = re.compile(
    r"(\d+)\s+(passed|skipped|failed|errors?|warnings?|deselected|"
    r"xfailed|xpassed)")
_COLLECTED_RE = re.compile(r"(\d+)\s+tests?\s+collected")
_COLLECTED_ITEMS_RE = re.compile(r"collected\s+(\d+)\s+items?")


def _parse_summary(text: str) -> dict:
    """Parse the pytest tail summary into integer counts (fail-closed)."""
    body = None
    for line in reversed(text.splitlines()):
        match = _SUMMARY_RE.search(line.strip())
        if match:
            body = match.group("body")
            break
    if body is None:
        raise ClosureError("pytest summary tail line not found")
    counts = {"passed": 0, "skipped": 0, "failed": 0, "errors": 0,
              "warnings": 0}
    for amount, name in _COUNT_RE.findall(body):
        key = {"error": "errors", "warning": "warnings"}.get(name, name)
        if key in counts:
            counts[key] += int(amount)
    return counts


_SKIP_RE = re.compile(r"^SKIPPED \[\d+\]\s+(?P<node>[^:\s]+:[^\s]+)"
                      r"\s*-\s*(?P<reason>.*)$")


def _parse_skips(text: str) -> list[dict]:
    """Return skipped node ids + reasons from the -rs summary section."""
    skips = []
    for line in text.splitlines():
        m = _SKIP_RE.match(line.strip())
        if m:
            skips.append({"node": m.group("node"),
                          "reason": m.group("reason").strip()})
    return skips


def _parse_collected(text: str) -> int:
    """Parse the collection census (fail-closed)."""
    for line in reversed(text.splitlines()):
        match = _COLLECTED_RE.search(line)
        if match:
            return int(match.group(1))
    match = _COLLECTED_ITEMS_RE.search(text)
    if match:
        return int(match.group(1))
    raise ClosureError("pytest collection count not found")


def _load_manifest(manifest_path: Path, repo: Path) -> dict:
    """Fail-closed manifest load (A4-03): live bytes + bound heads.

    Returns ``{path, relpath, sha256, content_head, manifest_commit}``.
    The digest is taken over the exact bytes on disk at call time so the
    receipt binds the manifest under test, not a remembered digest.
    """
    manifest_path = Path(manifest_path).expanduser().resolve()
    try:
        raw = manifest_path.read_bytes()
    except OSError as exc:
        raise ClosureError(
            f"manifest unreadable: {manifest_path}: {exc}") from exc
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ClosureError(
            f"manifest is not parseable JSON: {manifest_path}: "
            f"{exc}") from exc
    if not isinstance(doc, dict):
        raise ClosureError("manifest root must be a JSON object")
    for field in ("content_head", "manifest_commit"):
        value = doc.get(field)
        if not (isinstance(value, str) and _HEX40(value)):
            raise ClosureError(
                f"manifest {field} must be a 40-hex commit "
                f"(got {value!r})")
    try:
        relpath = manifest_path.relative_to(
            Path(repo).resolve()).as_posix()
    except ValueError:
        relpath = str(manifest_path)
    return {
        "path": manifest_path,
        "relpath": relpath,
        "sha256": sha256_bytes(raw),
        "content_head": doc["content_head"],
        "manifest_commit": doc["manifest_commit"],
    }


def run_suite(*, repo: Path, pytest_args: str,
              manifest_path: Path = MANIFEST_DEFAULT) -> dict:
    """Run the suite under *repo* and return the receipt document."""
    repo = Path(repo).resolve()
    test_args = shlex.split(pytest_args)

    # Freshness bindings are captured BEFORE any pytest activity so the
    # receipt binds the tree under test (A4-03/13): a suite that runs
    # while HEAD or the manifest moves still records the pre-run state.
    repository_head = _head(repo)
    manifest = _load_manifest(manifest_path, repo)

    # The census pass must emit the "N tests collected" tail; strip quiet
    # flags from the caller's args so a doubled -q cannot collapse the
    # output to per-file counts.
    census_args = [a for a in test_args if a not in ("-q", "--quiet")]
    collect_argv = [sys.executable, "-B", "-m", "pytest",
                    "--collect-only", "-q", *census_args]
    collect_started_utc = _utc_now()
    collect = subprocess.run(collect_argv, cwd=repo, capture_output=True,
                             text=True, check=False)
    collected = _parse_collected(collect.stdout + "\n" + collect.stderr)

    run_argv = [sys.executable, "-B", "-m", "pytest", *test_args]
    if "-rs" not in test_args and "-rA" not in test_args:
        run_argv = [*run_argv, "-rs"]
    command_digest = sha256_bytes(
        json.dumps(run_argv).encode("utf-8"))
    suite_started_utc = _utc_now()
    start = time.monotonic()
    run = subprocess.run(run_argv, cwd=repo, capture_output=True,
                         text=True, check=False)
    duration_s = round(time.monotonic() - start, 3)
    suite_completed_utc = _utc_now()

    raw_output = run.stdout + "\n" + run.stderr
    counts = _parse_summary(raw_output)
    skipped_tests = _parse_skips(raw_output)
    counts["collected"] = collected
    expected = (counts["passed"] + counts["skipped"]
                + counts["failed"] + counts["errors"])
    if collected != expected:
        raise ClosureError(
            f"collection count {collected} != passed+skipped+failed+"
            f"errors {expected}; refusing to emit an inconsistent "
            "receipt")

    return {
        "schema": "P5_SUITE_RECEIPT_V2",
        "activity_id": uuid.uuid4().hex,
        "command_digest": command_digest,
        "argv": run_argv,
        "repository_head": repository_head,
        "manifest_relpath": manifest["relpath"],
        "manifest_sha256": manifest["sha256"],
        "content_head": manifest["content_head"],
        "manifest_commit": manifest["manifest_commit"],
        "execution_window": {
            "collect_started_utc": collect_started_utc,
            "suite_started_utc": suite_started_utc,
            "suite_completed_utc": suite_completed_utc,
        },
        "counts": counts,
        "skipped_tests": skipped_tests,
        "exit_code": run.returncode,
        "duration_s": duration_s,
        "started_utc": suite_started_utc,
        "completed_utc": suite_completed_utc,
        "environment": _environment(),
        "data_context": (
            "counts describe test execution on this tree; data maximum "
            "dates are recorded separately in evidence provenance"),
        "claim_scope": "research_only_no_operational_authorization",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the machine P5 suite receipt "
                    "(P5_SUITE_RECEIPT_V2).  Default is --dry-run: run "
                    "the suite and print the receipt, write nothing.  "
                    "--write publishes via exclusive create.")
    parser.add_argument("--repo", default=str(REPO),
                        help="repository root the suite runs under")
    parser.add_argument("--pytest-args", default="tests/ -q",
                        help="argument string appended to "
                             "'python -B -m pytest'")
    parser.add_argument("--manifest", default=str(MANIFEST_DEFAULT),
                        help="artifact manifest hashed and bound into "
                             "the receipt before the suite runs")
    parser.add_argument("--out", required=True,
                        help="receipt output path (exclusive create)")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--write", action="store_true",
                      help="publish the receipt via write_once_json + "
                           "write_once_sidecar (exclusive create)")
    mode.add_argument("--dry-run", action="store_true",
                      help="run and print only (default)")
    args = parser.parse_args(argv)

    try:
        receipt = run_suite(repo=Path(args.repo),
                            pytest_args=args.pytest_args,
                            manifest_path=Path(args.manifest))
    except (OSError, ValueError) as exc:
        print(f"SUITE_RECEIPT_FAIL: {exc}")
        return 1

    out = Path(args.out).resolve()
    if not args.write:
        print(json.dumps({"status": "SUITE_RECEIPT_DRY_RUN_OK",
                          "out": str(out), "receipt": receipt},
                         indent=2, sort_keys=True))
        return 0
    try:
        digest = write_once_json(out, receipt, indent=2)
        write_once_sidecar(out)
    except (OSError, ValueError) as exc:
        print(f"SUITE_RECEIPT_FAIL: {exc}")
        return 1
    print(json.dumps({"status": "SUITE_RECEIPT_PUBLISHED",
                      "out": str(out), "sha256": digest},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
