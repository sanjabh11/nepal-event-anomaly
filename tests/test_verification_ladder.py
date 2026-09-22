"""Tests for the read-only verification-ladder orchestrator."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from scripts.run_verification_ladder import (
    StepSpec,
    execute_ladder,
    make_plan,
)


ROOT = Path(__file__).resolve().parents[1]


def _repo_fixture(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    (repo / "docs/science").mkdir(parents=True)
    (repo / ".github/workflows").mkdir(parents=True)
    (repo / "tests").mkdir()
    (repo / "README.md").write_text("fixture\n", encoding="utf-8")
    (repo / "docs/science/ARTIFACT_MANIFEST_V0.json").write_text(
        '{"test_results": {"research_v0": true}}\n',
        encoding="utf-8",
    )
    (repo / "docs/science/one.md").write_text("fixture\n", encoding="utf-8")
    (repo / ".github/workflows/verify.yml").write_text(
        "name: fixture\n", encoding="utf-8")
    for name in (
        "focused_a.py",
        "focused_b.py",
        "test_verification_ladder.py",
        "test_replay_never_reexecutes.py",
    ):
        (repo / "tests" / name).write_text("# fixture\n", encoding="utf-8")
    return repo


def _all_release_paths(tmp_path: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    for name in ("index", "closure", "incident", "daily", "seasonal"):
        path = tmp_path / f"{name}.json"
        if name in {"daily", "seasonal"}:
            payload = {
                "status": "REPLAY_OK",
                "replay_scope": "artifact_integrity_replay",
                "model_reexecution": {"status": "NOT_RUN"},
            }
        else:
            payload = {}
        path.write_text(json.dumps(payload), encoding="utf-8")
        paths[name] = path
    return paths


def test_dry_run_is_a_plan_only_report(tmp_path: Path) -> None:
    repo = _repo_fixture(tmp_path)
    steps = make_plan(
        repo,
        focused_tests=(
            "tests/focused_a.py",
            "tests/focused_b.py",
        ),
    )
    called = False

    def unexpected_runner(step: StepSpec) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        raise AssertionError(f"dry-run executed {step.name}")

    report = execute_ladder(
        steps,
        runner=unexpected_runner,
        dry_run=True,
    )
    assert report["status"] == "PLAN_ONLY"
    assert report["dry_run"] is True
    assert called is False
    names = [record["name"] for record in report["steps"]]
    assert names[:2] == ["focused_tests", "collection_only"]
    assert "manifest_verify" in names
    assert "clean_tree" in names
    assert all(record["status"] == "PLANNED"
               for record in report["steps"])


def test_failure_is_recorded_and_later_steps_are_not_hidden(
    tmp_path: Path,
) -> None:
    repo = _repo_fixture(tmp_path)
    steps = [
        StepSpec("first", ("first",), repo, {}),
        StepSpec("fails", ("fails",), repo, {}),
        StepSpec("later", ("later",), repo, {}),
    ]

    def fake_runner(step: StepSpec) -> subprocess.CompletedProcess[str]:
        code = 9 if step.name == "fails" else 0
        return subprocess.CompletedProcess(
            list(step.argv), code, stdout=f"{step.name} stdout",
            stderr=f"{step.name} stderr")

    report = execute_ladder(steps, runner=fake_runner)
    assert report["status"] == "LADDER_FAIL"
    assert [record["name"] for record in report["steps"]] == [
        "first", "fails", "later"]
    failed = report["steps"][1]
    assert failed["status"] == "FAIL"
    assert failed["observed_exit"] == 9
    assert report["steps"][2]["status"] == "PASS"


def test_missing_release_artifacts_are_incomplete_not_passed(
    tmp_path: Path,
) -> None:
    repo = _repo_fixture(tmp_path)
    steps = make_plan(
        repo,
        focused_tests=("tests/focused_a.py",),
    )
    calls: list[str] = []

    def fake_runner(step: StepSpec) -> subprocess.CompletedProcess[str]:
        calls.append(step.name)
        return subprocess.CompletedProcess(
            list(step.argv), 0, stdout="", stderr="")

    report = execute_ladder(steps, runner=fake_runner)
    assert report["status"] == "LADDER_INCOMPLETE"
    not_configured = {
        record["name"]
        for record in report["steps"]
        if record["status"] == "NOT_CONFIGURED"
    }
    assert {
        "index_validate",
        "closure_validate",
        "incident_surface_validate",
        "replay_status",
    } <= not_configured
    assert "index_validate" not in calls


def test_declared_commands_are_read_only_and_use_actual_validator_interfaces(
    tmp_path: Path,
) -> None:
    repo = _repo_fixture(tmp_path)
    release = _all_release_paths(tmp_path)
    steps = make_plan(
        repo,
        focused_tests=("tests/focused_a.py",),
        index=release["index"],
        closure=release["closure"],
        incident_surface=release["incident"],
        daily_replay=release["daily"],
        seasonal_replay=release["seasonal"],
    )
    by_name = {step.name: step for step in steps}
    assert "--report-out" not in " ".join(
        arg for step in steps for arg in step.argv)
    assert by_name["index_validate"].argv[-1] == str(release["index"])
    assert "--current-tree" in by_name["closure_validate"].argv
    assert "--descriptor" in by_name["incident_surface_validate"].argv
    assert by_name["replay_status"].argv[2] == "-c"
    assert by_name["clean_tree"].argv[2] == "-c"
    assert all(step.env.get("PYTHONDONTWRITEBYTECODE") == "1"
               for step in steps)
