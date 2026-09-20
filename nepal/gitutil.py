"""Git object resolution helper — lives OUTSIDE nepal.research_v0.

The frozen research package forbids subprocess/os imports
(tests/test_research_v0_isolation.py); manifest verification still
needs to prove recorded head SHAs resolve to real commit objects,
so the subprocess call lives here, in shared tooling, where the
isolation contract does not apply.
"""
import subprocess
from pathlib import Path


def is_git_worktree(root: Path) -> bool:
    try:
        res = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True, text=True, check=False)
        return res.returncode == 0 and res.stdout.strip() == "true"
    except OSError:
        return False


def resolves_to_commit(root: Path, sha: str) -> bool | None:
    """True if sha names a commit object, False if it resolves to a
    non-commit or nothing, None if git could not answer."""
    try:
        res = subprocess.run(
            ["git", "-C", str(root), "cat-file", "-t", sha],
            capture_output=True, text=True, check=False)
    except OSError:
        return None
    if res.returncode != 0:
        return False
    return res.stdout.strip() == "commit"
