#!/usr/bin/env python3
"""Run the read-only P5 Phase-6 verification ladder.

The ladder is an orchestration report, not a release publisher.  It invokes
only declared commands with argv lists (never a shell), never passes
report-out arguments to validators, and emits its report to stdout.  Missing
release artifacts are recorded as NOT_CONFIGURED rather than silently
removed from the ladder.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Mapping, Sequence


SCHEMA = "P5_VERIFICATION_LADDER_REPORT_V1"
DEFAULT_FOCUSED_TESTS = (
    "tests/test_export_release_bundle.py",
    "tests/test_promotion_protocol_consistency.py",
    "tests/test_verification_ladder.py",
    "tests/test_replay_never_reexecutes.py",
)
REPLAY_STATUS_CODE = (
    "import json, sys\n"
    "bad = []\n"
    "for raw in sys.argv[1:]:\n"
    "    try:\n"
    "        with open(raw, encoding='utf-8') as stream:\n"
    "            doc = json.load(stream)\n"
    "    except (OSError, ValueError) as exc:\n"
    "        bad.append(f'{raw}: unreadable JSON: {exc}')\n"
    "        continue\n"
    "    if doc.get('status') != 'REPLAY_OK':\n"
    "        bad.append(f'{raw}: status is {doc.get(\"status\")!r}')\n"
    "    if doc.get('replay_scope') != 'artifact_integrity_replay':\n"
    "        bad.append(f'{raw}: replay_scope is '\n"
        "f'{doc.get(\"replay_scope\")!r}')\n"
    "    model = doc.get('model_reexecution') or {}\n"
    "    if model.get('status') != 'NOT_RUN':\n"
    "        bad.append(f'{raw}: model_reexecution is not NOT_RUN')\n"
    "if bad:\n"
    "    print('\\n'.join(bad))\n"
    "    raise SystemExit(1)\n"
    "print(f'REPLAY_STATUS_OK: {len(sys.argv) - 1} reports')\n"
)
CLEAN_TREE_CODE = (
    "import subprocess, sys\n"
    "proc = subprocess.run(\n"
    "    ['git', 'status', '--porcelain', '--untracked-files=all'],\n"
    "    capture_output=True, text=True, check=False)\n"
    "if proc.returncode != 0:\n"
    "    print(proc.stderr.strip() or 'git status failed')\n"
    "    raise SystemExit(proc.returncode or 1)\n"
    "if proc.stdout:\n"
    "    print(proc.stdout, end='')\n"
    "    raise SystemExit(1)\n"
    "print('TREE_CLEAN')\n"
)


@dataclass(frozen=True)
class StepSpec:
    name: str
    argv: tuple[str, ...]
    cwd: Path
    env: Mapping[str, str]
    expected_exit: int = 0
    timeout_seconds: int = 300
    configured: bool = True
    reason: str | None = None

    def public(self) -> dict[str, object]:
        return {
            "name": self.name,
            "argv": list(self.argv),
            "cwd": str(self.cwd),
            "expected_exit": self.expected_exit,
            "timeout_seconds": self.timeout_seconds,
            "configured": self.configured,
            "reason": self.reason,
        }


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _clip(value: str, limit: int = 2000) -> str:
    if len(value) <= limit:
        return value
    return value[-limit:]


def _environment(repo_root: Path) -> dict[str, str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTEST_ADDOPTS"] = "-p no:cacheprovider"
    old_pythonpath = env.get("PYTHONPATH")
    env["PYTHONPATH"] = (
        str(repo_root)
        if not old_pythonpath
        else str(repo_root) + os.pathsep + old_pythonpath
    )
    return env


def _missing_step(
    name: str,
    repo_root: Path,
    env: Mapping[str, str],
    reason: str,
) -> StepSpec:
    return StepSpec(
        name=name,
        argv=(),
        cwd=repo_root,
        env=env,
        configured=False,
        reason=reason,
    )


def _path_exists(path: Path | None) -> bool:
    return path is not None and path.is_file()


def _resolve_optional(path: Path | None) -> Path | None:
    if path is None:
        return None
    return Path(path).expanduser().resolve()


def _require_path(
    name: str,
    path: Path | None,
    repo_root: Path,
    env: Mapping[str, str],
    argv: Sequence[str],
) -> StepSpec:
    if path is None:
        return _missing_step(name, repo_root, env, "path was not supplied")
    if not path.is_file():
        return _missing_step(
            name, repo_root, env, f"file is missing: {path}")
    return StepSpec(name=name, argv=tuple(argv), cwd=repo_root, env=env)


def make_plan(
    repo_root: Path,
    *,
    focused_tests: Sequence[str] = DEFAULT_FOCUSED_TESTS,
    index: Path | None = None,
    index_root_map: Path | None = None,
    closure: Path | None = None,
    incident_surface: Path | None = None,
    daily_replay: Path | None = None,
    seasonal_replay: Path | None = None,
) -> list[StepSpec]:
    """Build the complete ladder without executing any command."""

    repo_root = Path(repo_root).resolve()
    index = _resolve_optional(index)
    index_root_map = _resolve_optional(index_root_map)
    closure = _resolve_optional(closure)
    incident_surface = _resolve_optional(incident_surface)
    daily_replay = _resolve_optional(daily_replay)
    seasonal_replay = _resolve_optional(seasonal_replay)
    env = _environment(repo_root)
    python = sys.executable
    steps: list[StepSpec] = []

    focused_paths = [repo_root / rel for rel in focused_tests]
    missing_focused = [
        str(path) for path in focused_paths if not path.is_file()]
    if missing_focused:
        steps.append(_missing_step(
            "focused_tests", repo_root, env,
            "missing focused test files: " + ", ".join(missing_focused)))
    else:
        steps.append(StepSpec(
            name="focused_tests",
            argv=tuple([
                python, "-B", "-m", "pytest",
                *[str(path) for path in focused_paths],
                "-q", "-rs",
            ]),
            cwd=repo_root,
            env=env,
        ))

    steps.append(StepSpec(
        name="collection_only",
        argv=tuple([
            python, "-B", "-m", "pytest", "tests/",
            "--collect-only", "-q", "-rs",
        ]),
        cwd=repo_root,
        env=env,
    ))

    claim_targets = [repo_root / "docs/science", repo_root / "README.md"]
    for workflow in sorted((repo_root / ".github/workflows").glob("*.yml")):
        claim_targets.append(workflow)
    for target in claim_targets:
        if not target.exists():
            steps.append(_missing_step(
                f"claim_scan:{target.relative_to(repo_root)}",
                repo_root, env, f"target is missing: {target}"))
        else:
            steps.append(StepSpec(
                name=f"claim_scan:{target.relative_to(repo_root)}",
                argv=tuple([
                    python, "-B", "-m", "nepal.research_v0.cli",
                    "claim-scan", str(target),
                ]),
                cwd=repo_root,
                env=env,
            ))

    manifest = repo_root / "docs/science/ARTIFACT_MANIFEST_V0.json"
    steps.append(_require_path(
        "manifest_verify",
        manifest,
        repo_root,
        env,
        [
            python, "-B", "-m", "nepal.research_v0.cli",
            "verify-manifest", str(manifest),
        ],
    ))

    index_argv = [python, "-B", "scripts/validate_evidence_index.py"]
    if index is not None:
        index_argv.append(str(index))
        if index_root_map is not None:
            index_argv.extend(["--root-map", str(index_root_map)])
    steps.append(_require_path(
        "index_validate", index, repo_root, env, index_argv))

    closure_argv = [
        python, "-B", "scripts/validate_release_closure.py",
    ]
    if closure is not None:
        closure_argv.extend([
            str(closure), "--current-tree", "--repo-root", str(repo_root)])
    steps.append(_require_path(
        "closure_validate", closure, repo_root, env, closure_argv))

    incident_argv = [
        python, "-B", "scripts/validate_incident_surface.py",
        "--descriptor",
    ]
    if incident_surface is not None:
        incident_argv.append(str(incident_surface))
    steps.append(_require_path(
        "incident_surface_validate",
        incident_surface,
        repo_root,
        env,
        incident_argv,
    ))

    replay_reports = [daily_replay, seasonal_replay]
    if not all(_path_exists(path) for path in replay_reports):
        missing = [
            "daily" if daily_replay is None or not daily_replay.is_file()
            else "",
            "seasonal"
            if seasonal_replay is None or not seasonal_replay.is_file()
            else "",
        ]
        steps.append(_missing_step(
            "replay_status",
            repo_root,
            env,
            "missing replay report(s): " + ", ".join(x for x in missing if x),
        ))
    else:
        steps.append(StepSpec(
            name="replay_status",
            argv=tuple([
                python, "-B", "-c", REPLAY_STATUS_CODE,
                str(daily_replay), str(seasonal_replay),
            ]),
            cwd=repo_root,
            env=env,
        ))

    steps.append(StepSpec(
        name="clean_tree",
        argv=tuple([python, "-B", "-c", CLEAN_TREE_CODE]),
        cwd=repo_root,
        env=env,
    ))
    return steps


def _subprocess_runner(step: StepSpec) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(step.argv),
        cwd=step.cwd,
        env=dict(step.env),
        capture_output=True,
        text=True,
        check=False,
        shell=False,
        timeout=step.timeout_seconds,
    )


def execute_ladder(
    steps: Sequence[StepSpec],
    *,
    runner: Callable[[StepSpec], subprocess.CompletedProcess[str]]
    = _subprocess_runner,
    dry_run: bool = False,
) -> dict[str, object]:
    """Execute a plan or return a no-side-effect plan report."""

    started = _utc_now()
    records: list[dict[str, object]] = []
    if dry_run:
        for step in steps:
            record = step.public()
            record["status"] = "PLANNED"
            records.append(record)
        return {
            "schema": SCHEMA,
            "status": "PLAN_ONLY",
            "dry_run": True,
            "started_utc": started,
            "completed_utc": _utc_now(),
            "steps": records,
        }

    for step in steps:
        record = step.public()
        if not step.configured:
            record.update({
                "status": "NOT_CONFIGURED",
                "observed_exit": None,
            })
            records.append(record)
            continue
        began = time.monotonic()
        try:
            completed = runner(step)
            elapsed_ms = int((time.monotonic() - began) * 1000)
            record.update({
                "status": (
                    "PASS"
                    if completed.returncode == step.expected_exit
                    else "FAIL"
                ),
                "observed_exit": completed.returncode,
                "elapsed_ms": elapsed_ms,
                "stdout_tail": _clip(completed.stdout or ""),
                "stderr_tail": _clip(completed.stderr or ""),
            })
        except subprocess.TimeoutExpired as exc:
            record.update({
                "status": "FAIL",
                "observed_exit": None,
                "elapsed_ms": int((time.monotonic() - began) * 1000),
                "stdout_tail": _clip(str(exc.stdout or "")),
                "stderr_tail": _clip(str(exc.stderr or "")),
                "error": "timeout",
            })
        except OSError as exc:
            record.update({
                "status": "FAIL",
                "observed_exit": None,
                "elapsed_ms": int((time.monotonic() - began) * 1000),
                "stdout_tail": "",
                "stderr_tail": "",
                "error": str(exc),
            })
        records.append(record)

    statuses = [record["status"] for record in records]
    if "FAIL" in statuses:
        overall = "LADDER_FAIL"
    elif "NOT_CONFIGURED" in statuses:
        overall = "LADDER_INCOMPLETE"
    else:
        overall = "LADDER_OK"
    return {
        "schema": SCHEMA,
        "status": overall,
        "dry_run": False,
        "started_utc": started,
        "completed_utc": _utc_now(),
        "steps": records,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Run the declared read-only P5 verification ladder. "
            "The report is emitted to stdout and no report-out path "
            "is supported."))
    parser.add_argument("--repo-root", default=".")
    parser.add_argument("--index", type=Path, default=None)
    parser.add_argument("--index-root-map", type=Path, default=None)
    parser.add_argument("--closure", type=Path, default=None)
    parser.add_argument("--incident-surface", type=Path, default=None)
    parser.add_argument("--daily-replay", type=Path, default=None)
    parser.add_argument("--seasonal-replay", type=Path, default=None)
    parser.add_argument(
        "--focused-test", action="append", default=None,
        help="repository-relative focused test; repeat as needed")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    repo_root = Path(args.repo_root).expanduser().resolve()
    if not repo_root.is_dir():
        print(json.dumps({
            "schema": SCHEMA,
            "status": "LADDER_FAIL",
            "problems": [f"repository root is missing: {repo_root}"],
        }, indent=2, sort_keys=True))
        return 1
    focused = (
        tuple(args.focused_test)
        if args.focused_test is not None
        else DEFAULT_FOCUSED_TESTS
    )
    report = execute_ladder(
        make_plan(
            repo_root,
            focused_tests=focused,
            index=args.index,
            index_root_map=args.index_root_map,
            closure=args.closure,
            incident_surface=args.incident_surface,
            daily_replay=args.daily_replay,
            seasonal_replay=args.seasonal_replay,
        ),
        dry_run=args.dry_run,
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] in {"PLAN_ONLY", "LADDER_OK"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
