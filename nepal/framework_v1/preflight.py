"""Read-only Phase B framework preflight and dirty-worktree baseline."""
from __future__ import annotations

import hashlib
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional

from . import contract as C
from .input_manifest import contract_source_inventory
from .provenance import check_no_raw_slc_tree, sha256_file


def _git(root: Path, *args: str) -> tuple[int, str, str]:
    try:
        result = subprocess.run(
            ["git", *args], cwd=root, check=False, capture_output=True,
            text=True, timeout=15,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 127, "", str(exc)
    return result.returncode, result.stdout, result.stderr


def _handoff_inventory(root: Path) -> dict[str, Any]:
    """Collect a complete stat-only inventory without a second bulk hash pass."""
    if not root.exists() or not root.is_dir():
        return {"root": str(root), "file_count": 0, "entries": [],
                "present": False}
    entries: list[dict[str, Any]] = []
    for path in sorted(root.rglob("*")):
        try:
            relative = path.relative_to(root).as_posix()
        except ValueError:
            continue
        entry: dict[str, Any] = {"relative_path": relative,
                                 "symlink": path.is_symlink()}
        if path.is_file() and not path.is_symlink():
            try:
                entry.update({"kind": "file", "bytes": path.stat().st_size})
            except OSError as exc:
                entry.update({"kind": "unreadable", "error": str(exc)})
        elif path.is_dir():
            entry["kind"] = "directory"
        else:
            entry["kind"] = "other"
        entries.append(entry)
    return {"root": str(root), "present": True,
            "file_count": sum(item.get("kind") == "file" for item in entries),
            "entries": entries}


def run_preflight(
    repo_root: str | Path,
    *,
    handoff_root: Optional[str | Path] = None,
    expected_authoritative_root: Optional[str | Path] = None,
    minimum_free_gib: float = 8.0,
) -> dict[str, Any]:
    """Capture a non-mutating, reproducible baseline before a rerun.

    The result is deliberately a diagnostic envelope: it never stages,
    resets, cleans, downloads, or rewrites the checkout/data lane.
    """
    root = Path(repo_root).resolve()
    handoff = Path(handoff_root).resolve() if handoff_root else root
    failures: list[str] = []
    checks: dict[str, Any] = {}

    expected = (Path(expected_authoritative_root).resolve()
                if expected_authoritative_root else None)
    checks["authoritative_root"] = {
        "actual": str(root),
        "expected": str(expected) if expected else None,
        "passed": expected is None or root == expected,
    }
    if not checks["authoritative_root"]["passed"]:
        failures.append("authoritative checkout root does not match the requested root")

    code, head, stderr = _git(root, "rev-parse", "HEAD")
    checks["git_head"] = head.strip() if code == 0 else None
    if code != 0:
        failures.append(f"git HEAD could not be captured: {stderr.strip()}")
    code, status, stderr = _git(root, "status", "--short", "--untracked-files=all")
    checks["git_status"] = status.splitlines() if code == 0 else []
    if code != 0:
        failures.append(f"git status could not be captured: {stderr.strip()}")
    code, diff, stderr = _git(root, "diff", "--binary", "HEAD")
    checks["git_diff_sha256"] = hashlib.sha256(diff.encode("utf-8")).hexdigest() \
        if code == 0 else None
    if code != 0:
        failures.append(f"git diff could not be captured: {stderr.strip()}")
    code, _, stderr = _git(root, "diff", "--check", "HEAD")
    checks["git_diff_check"] = {"passed": code == 0, "stderr": stderr.strip()}
    if code != 0:
        failures.append("git diff --check reported whitespace errors")
    code, diff_stat, stderr = _git(root, "diff", "--stat", "HEAD")
    checks["git_diff_stat"] = diff_stat.strip() if code == 0 else None
    if code != 0:
        failures.append(f"git diff stat could not be captured: {stderr.strip()}")

    usage = shutil.disk_usage(root)
    minimum_free_bytes = int(float(minimum_free_gib) * (1024 ** 3))
    checks["disk_space"] = {
        "free_bytes": usage.free,
        "free_gib": round(usage.free / (1024 ** 3), 3),
        "minimum_free_gib": float(minimum_free_gib),
        "passed": usage.free >= minimum_free_bytes,
    }
    if not checks["disk_space"]["passed"]:
        failures.append("free disk space is below the preflight minimum")

    preregistration = C.verify_preregistration(root / C.PREREGISTRATION_PATH)
    checks["preregistration"] = preregistration
    if not preregistration.get("ok"):
        failures.append("preregistration hash is not verified")

    checks["contract_source_inventory"] = contract_source_inventory(root)
    requirements = root / "requirements.txt"
    checks["environment"] = {
        "python_version": platform.python_version(),
        "python_implementation": platform.python_implementation(),
        "platform": platform.platform(),
        "requirements_path": "requirements.txt",
        "requirements_sha256": (sha256_file(requirements)
                                 if requirements.is_file() else None),
        "requirements_present": requirements.is_file(),
        "executable": sys.executable,
    }
    if not requirements.is_file():
        failures.append("pinned requirements.txt is missing")
    manifest_path = handoff / "manifest.json"
    checks["handoff_manifest"] = {
        "path": str(manifest_path),
        "present": manifest_path.is_file(),
        "sha256": sha256_file(manifest_path) if manifest_path.is_file() else None,
    }
    raw_slc_hits = check_no_raw_slc_tree(handoff)
    checks["raw_slc_scan"] = {"passed": not raw_slc_hits,
                              "hits": raw_slc_hits[:20],
                              "hit_count": len(raw_slc_hits)}
    if raw_slc_hits:
        failures.append("raw SLC content/path was found under the handoff root")

    code, worktrees, stderr = _git(root, "worktree", "list", "--porcelain")
    checks["worktrees"] = {
        "output": worktrees.splitlines() if code == 0 else [],
        "passed": code == 0,
    }
    if code != 0:
        failures.append(f"git worktree inventory could not be captured: {stderr.strip()}")

    try:
        process_result = subprocess.run(
            ["ps", "-axo", "pid=,ppid=,etime=,stat=,command="],
            check=False, capture_output=True, text=True, timeout=5,
        )
        checks["active_processes"] = {
            "output": process_result.stdout.splitlines(),
            "passed": process_result.returncode == 0,
        }
    except (OSError, subprocess.TimeoutExpired) as exc:
        checks["active_processes"] = {"output": [], "passed": False,
                                       "error": str(exc)}
        failures.append("active process inventory could not be captured")

    checks["handoff_inventory"] = _handoff_inventory(handoff)
    if any(entry.get("symlink") for entry in
           checks["handoff_inventory"].get("entries", [])):
        failures.append("handoff inventory contains a symlink")

    return {
        "status": "BASELINE_READY" if not failures else "BASELINE_BLOCKED",
        "ok": not failures,
        "data_source_status": C.PREREGISTRATION_DATA_SOURCE_STATUS,
        "failures": sorted(set(failures)),
        "checks": checks,
    }
