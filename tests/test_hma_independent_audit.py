"""Tests for scripts/hma_independent_audit_v0.py — the truly independent
reimplementation verifier for the sealed HMA lake-trajectory PoC V2 lane.
"""
from __future__ import annotations

import ast
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "hma_independent_audit_v0.py"
ARTIFACT = Path(
    "/Users/sanjayb/nepal-event-anomaly-evidence/india-phase0-source-intake/"
    "hma-posthoc-audit-v0/HMA_INDEPENDENT_AUDIT_V0.json")

FORBIDDEN_IMPORTS = {
    "hma_lake_trajectory_poc",
    "hma_lake_trajectory_poc_v2",
    "india_lake_epoch_linkage",
    "verify_india_lake_epoch_linkage",
}
ALLOWED_THIRD_PARTY = {"numpy", "sklearn", "dateutil", "p5_safe_io"}


def _imported_modules(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                modules.add(node.module.split(".")[0])
    return modules


def test_no_forbidden_imports():
    modules = _imported_modules(SCRIPT)
    assert not (modules & FORBIDDEN_IMPORTS), (
        f"forbidden engine imports found: {modules & FORBIDDEN_IMPORTS}")
    non_stdlib = {m for m in modules if m not in sys.stdlib_module_names}
    assert non_stdlib <= ALLOWED_THIRD_PARTY, (
        f"unexpected third-party imports: {non_stdlib - ALLOWED_THIRD_PARTY}")


@pytest.fixture(scope="module")
def audit_run():
    """Run the audit once per test module (JSON load + 200 resamples cached
    behind a single subprocess invocation)."""
    proc = subprocess.run(
        [sys.executable, "-B", str(SCRIPT)],
        capture_output=True, text=True, timeout=1800)
    return proc


def test_audit_runs_and_artifact_is_bound(audit_run):
    assert audit_run.returncode == 0, (
        f"audit failed:\n{audit_run.stdout}\n{audit_run.stderr}")
    assert ARTIFACT.is_file()
    sidecar = Path(str(ARTIFACT) + ".sha256")
    assert sidecar.is_file()
    digest = hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()
    assert sidecar.read_text(encoding="utf-8") == f"{digest}  {ARTIFACT.name}\n"

    doc = json.loads(ARTIFACT.read_text(encoding="utf-8"))
    assert doc["schema"] == "HMA_INDEPENDENT_AUDIT_V0"
    assert doc["verdict"] in ("INDEPENDENT_MATCH", "DISCREPANCY_REPORT")
    # All authority flags must remain false.
    assert not any(doc["authority"].values())
    # Every input artifact must be sha256-bound.
    assert len(doc["input_artifacts"]) == 6
    assert all(v["sidecar_verified"] for v in doc["input_artifacts"].values())
    # Every check records expected, observed and a match verdict; any
    # non-match must carry the observed value verbatim (no fudging).
    def _walk(checks):
        for name, row in checks.items():
            if isinstance(row, dict) and "checks" in row:
                yield from _walk(row["checks"])
            elif isinstance(row, dict) and "match" in row:
                yield name, row
    seen = list(_walk(doc["checks"]))
    assert seen
    for name, row in seen:
        assert "expected" in row and "observed" in row
        assert row["match"] in ("MATCH", "MISMATCH",
                                "APPROXIMATE_MATCH_WITH_DIFF", "INFO")
    if doc["verdict"] == "DISCREPANCY_REPORT":
        assert doc["mismatched_checks"], (
            "discrepancy verdict must name the failing checks")
        for name, row in seen:
            if row["match"] == "MISMATCH":
                assert row["observed"] is not None, (
                    f"{name}: mismatch must record the observed value")


def test_audit_is_write_once(audit_run, tmp_path):
    """A second run must not overwrite the sealed artifact: either it
    replays an identical document (_publish_or_match equivalent, exit 0,
    bytes unchanged) or it refuses with ExistingEvidenceError."""
    assert audit_run.returncode == 0
    before = hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()
    sidecar_before = Path(str(ARTIFACT) + ".sha256").read_bytes()

    proc = subprocess.run(
        [sys.executable, "-B", str(SCRIPT)],
        capture_output=True, text=True, timeout=1800)
    assert proc.returncode in (0, 1)
    after = hashlib.sha256(ARTIFACT.read_bytes()).hexdigest()
    assert before == after, "sealed artifact bytes changed on re-run"
    assert Path(str(ARTIFACT) + ".sha256").read_bytes() == sidecar_before
    if proc.returncode != 0:
        assert "write-once" in proc.stdout + proc.stderr or \
               "ExistingEvidenceError" in proc.stdout + proc.stderr or \
               "refusing to replace" in proc.stdout + proc.stderr
