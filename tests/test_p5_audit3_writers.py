from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PYTHON = ROOT / ".venv/bin/python"


def _run(script: str, *args: str) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [str(PYTHON), "-B", str(ROOT / "scripts" / script), *args],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )


def test_daily_driver_requires_a_new_output_root(tmp_path):
    result = _run("run_daily_p5.py", "--daily-root", str(tmp_path))
    assert result.returncode == 2
    assert "--output-root is required" in result.stdout


def test_seasonal_driver_requires_a_new_output_root(tmp_path):
    result = _run("run_seasonal_p5.py", "--daily-root", str(tmp_path))
    assert result.returncode == 2
    assert "--output-root is required" in result.stdout


def test_daily_driver_refuses_existing_governed_output(tmp_path):
    artifact = tmp_path / "retrieval" / "p5_glof_regime_artifact_v1.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("historical\n")
    before = artifact.read_bytes()
    result = _run("run_daily_p5.py", "--daily-root", str(tmp_path),
                   "--output-root", str(tmp_path))
    assert result.returncode == 2
    assert "output already contains governed evidence" in result.stdout
    assert artifact.read_bytes() == before


def test_seasonal_driver_refuses_existing_governed_output(tmp_path):
    artifact = tmp_path / "run" / "seasonal_regime_artifact_v0.json"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("historical\n")
    before = artifact.read_bytes()
    result = _run("run_seasonal_p5.py", "--daily-root", str(tmp_path),
                   "--output-root", str(tmp_path))
    assert result.returncode == 2
    assert "output already contains governed evidence" in result.stdout
    assert artifact.read_bytes() == before
