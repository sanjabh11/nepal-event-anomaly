"""Bounded, machine-readable framework verification runner."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional


def verification_commands(root: str | Path) -> list[list[str]]:
    """Return the canonical ordered verification commands."""
    repo = Path(root)
    python = str(repo / ".venv" / "bin" / "python")
    if not Path(python).is_file():
        python = sys.executable
    framework_tests = [
        str(path.relative_to(repo))
        for path in sorted((repo / "tests").glob("test_framework_v1_*.py"))
    ]
    return [
        [python, "-B", "-m", "pytest", "-q", *framework_tests],
        [python, "-B", "-m", "pytest", "-q",
         "data/framework_inputs_v1_reconciled/tests_reconciled/"],
        ["pyright", "nepal/framework_v1"],
        [python, "-B", "-m", "compileall", "-q", "nepal", "tests"],
        # The focused lanes above give fast diagnosis; this final check is
        # the acceptance gate for repository-wide regressions.  A timeout is
        # reported as INCOMPLETE by run_verification, never as a pass.
        [python, "-B", "-m", "pytest", "-q"],
    ]


def run_verification(root: str | Path, *,
                     timeout_seconds: Optional[float] = None) -> dict[str, Any]:
    """Run ordered checks and distinguish failures from incomplete runs."""
    repo = Path(root).resolve()
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    results: list[dict[str, Any]] = []
    for command in verification_commands(repo):
        try:
            completed = subprocess.run(
                command, cwd=repo, env=env, check=False,
                capture_output=True, text=True,
                timeout=timeout_seconds,
            )
        except subprocess.TimeoutExpired as exc:
            results.append({
                "command": command,
                "status": "INCOMPLETE",
                "returncode": None,
                "stdout_tail": str(exc.stdout or "")[-4000:],
                "stderr_tail": str(exc.stderr or "")[-4000:],
            })
            return {"status": "INCOMPLETE", "ok": False, "checks": results}
        except OSError as exc:
            results.append({
                "command": command,
                "status": "FAIL",
                "returncode": None,
                "stdout_tail": "",
                "stderr_tail": str(exc),
            })
            return {"status": "FAIL", "ok": False, "checks": results}
        results.append({
            "command": command,
            "status": "PASS" if completed.returncode == 0 else "FAIL",
            "returncode": completed.returncode,
            "stdout_tail": completed.stdout[-4000:],
            "stderr_tail": completed.stderr[-4000:],
        })
        if completed.returncode != 0:
            return {"status": "FAIL", "ok": False, "checks": results}
    return {"status": "PASS", "ok": True, "checks": results}
