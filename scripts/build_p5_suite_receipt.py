#!/usr/bin/env python3
"""Machine-generated P5 test-suite receipt (P5_SUITE_RECEIPT_V1, A3-03).

The v4 release closure never accepts free-form suite counts.  It binds a
receipt produced by THIS generator, which runs the suite itself and
parses the pytest summary tail.  The generator is fail-closed: if the
summary cannot be parsed, or the collection count does not equal
passed+skipped+failed+errors, no receipt is emitted.

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

_SUMMARY_RE = re.compile(r"=+\s*(?P<body>.*?)\s*in\s+[\d.]+s\s*=+\s*$")
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


def run_suite(*, repo: Path, pytest_args: str) -> dict:
    """Run the suite under *repo* and return the receipt document."""
    repo = Path(repo).resolve()
    test_args = shlex.split(pytest_args)

    collect_argv = [sys.executable, "-B", "-m", "pytest",
                    "--collect-only", "-q", *test_args]
    collect = subprocess.run(collect_argv, cwd=repo, capture_output=True,
                             text=True, check=False)
    collected = _parse_collected(collect.stdout + "\n" + collect.stderr)

    run_argv = [sys.executable, "-B", "-m", "pytest", *test_args]
    command_digest = sha256_bytes(
        json.dumps(run_argv).encode("utf-8"))
    started_utc = _utc_now()
    start = time.monotonic()
    run = subprocess.run(run_argv, cwd=repo, capture_output=True,
                         text=True, check=False)
    duration_s = round(time.monotonic() - start, 3)
    completed_utc = _utc_now()

    counts = _parse_summary(run.stdout + "\n" + run.stderr)
    counts["collected"] = collected
    expected = (counts["passed"] + counts["skipped"]
                + counts["failed"] + counts["errors"])
    if collected != expected:
        raise ClosureError(
            f"collection count {collected} != passed+skipped+failed+"
            f"errors {expected}; refusing to emit an inconsistent "
            "receipt")

    return {
        "schema": "P5_SUITE_RECEIPT_V1",
        "activity_id": uuid.uuid4().hex,
        "command_digest": command_digest,
        "argv": run_argv,
        "repository_head": _head(repo),
        "counts": counts,
        "exit_code": run.returncode,
        "duration_s": duration_s,
        "started_utc": started_utc,
        "completed_utc": completed_utc,
        "environment": _environment(),
        "claim_scope": "research_only_no_operational_authorization",
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate the machine P5 suite receipt "
                    "(P5_SUITE_RECEIPT_V1).  Default is --dry-run: run "
                    "the suite and print the receipt, write nothing.  "
                    "--write publishes via exclusive create.")
    parser.add_argument("--repo", default=str(REPO),
                        help="repository root the suite runs under")
    parser.add_argument("--pytest-args", default="tests/ -q",
                        help="argument string appended to "
                             "'python -B -m pytest'")
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
                            pytest_args=args.pytest_args)
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
