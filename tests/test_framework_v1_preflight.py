"""Read-only baseline/preflight tests."""
from __future__ import annotations

from pathlib import Path

from nepal.framework_v1.preflight import run_preflight
from nepal.framework_v1.verification import verification_commands


def test_preflight_wrong_authoritative_root_is_blocked(tmp_path):
    result = run_preflight(
        tmp_path,
        expected_authoritative_root=Path("/definitely/not/this/checkout"),
    )
    assert result["status"] == "BASELINE_BLOCKED"
    assert any("root" in failure for failure in result["failures"])


def test_preflight_captures_current_repo_state_without_mutation():
    root = Path(__file__).resolve().parents[1]
    result = run_preflight(root, handoff_root=root / "data" /
                           "framework_inputs_v1_reconciled")
    assert result["checks"]["git_head"]
    assert len(result["checks"]["git_diff_sha256"]) == 64
    assert result["data_source_status"] == "POST_HOC_DATA_SOURCE_CHANGE"


def test_canonical_verification_includes_full_repository_acceptance_check():
    commands = verification_commands(Path(__file__).resolve().parents[1])
    assert commands[-1][1:] == ["-B", "-m", "pytest", "-q"]
    assert commands[-1][0].endswith("/python")
