"""Rebind-CLI incident regression tests (G-01..G-04, G-11, G-12).

Every rejection path must leave the manifest bytes AND mtime untouched;
every write path must bind a real commit object and re-verify from live
bytes.  Tests run against disposable git repositories so the canonical
manifest is never at risk.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rebind_manifest.py"
MANIFEST_REL = "docs/science/ARTIFACT_MANIFEST_V0.json"
BASELINE_REL = "docs/science/MANIFEST_SCOPE_EXCLUSIONS_V0.json"
# The scope baseline is the boundary record, not governed surface, so the
# fixture manifest binds only the two governed files (the script counts the
# baseline as recorded via baseline_self_rel).
BOUND_FILES = ("scripts/tool.py", "tests/test_fixture.py")


def _entry(repo: Path, rel: str) -> dict:
    data = (repo / rel).read_bytes()
    return {"relpath": rel, "sha256": hashlib.sha256(data).hexdigest(),
            "size_bytes": len(data)}


def write_baseline(repo: Path, excluded: tuple[str, ...] = ()) -> None:
    """Write the recorded scope-exclusion baseline (G-11 boundary)."""
    doc = {
        "schema": "MANIFEST_SCOPE_EXCLUSIONS_V0",
        "baseline_content_head": "0" * 40,
        "manifest_relpath": MANIFEST_REL,
        "exclusions": [{"relpath": rel,
                        "category": "legacy_pre_fmx",
                        "reason": "fixture legacy surface"}
                       for rel in excluded],
    }
    (repo / BASELINE_REL).write_text(
        json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")


def _write_manifest(repo: Path, files: tuple[str, ...]) -> Path:
    manifest = {
        "baseline_head": "0" * 40,
        "content_head": "0" * 40,
        "manifest_commit": "0" * 40,
        "test_results": {"collection_guard":
                         "repo-wide collection == tests/ (1234 nodes)"},
        "files": [_entry(repo, rel) for rel in files]}
    path = repo / MANIFEST_REL
    path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def _git(repo: Path, *args: str) -> str:
    proc = subprocess.run(["git", "-C", str(repo), *args],
                          capture_output=True, text=True, check=False)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def _init_repo(tmp_path: Path) -> tuple[Path, Path]:
    """Deterministic disposable repo: manifest, baseline, governed files.

    Re-asserts every fixture byte and re-commits, so it is idempotent even
    if the pytest tmp directory is reused for a colliding test name.
    """
    repo = tmp_path / "repo"
    (repo / "tests").mkdir(parents=True, exist_ok=True)
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    (repo / MANIFEST_REL).parent.mkdir(parents=True, exist_ok=True)
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "test@example.com")
    _git(repo, "config", "user.name", "test")
    (repo / "tests" / "test_fixture.py").write_text("def test_x(): pass\n")
    (repo / "scripts" / "tool.py").write_text("print('ok')\n")
    write_baseline(repo)
    manifest_path = _write_manifest(repo, BOUND_FILES)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "--allow-empty", "-m", "fixture")
    return repo, manifest_path



def _add_new_governed_file(repo: Path,
                           rel: str = "tests/test_new_escape.py") -> None:
    """Create and commit a new governed file (G-11 escape scenario)."""
    path = repo / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("def test_y(): pass\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", f"add {rel}")


def _run(repo: Path, manifest_path: Path, *args: str
         ) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-B", str(SCRIPT),
         "--repo-root", str(repo), "--manifest", str(manifest_path),
         *args],
        capture_output=True, text=True, env=env, check=False)


def _state(path: Path) -> tuple[bytes, int]:
    st = os.stat(path)
    return path.read_bytes(), st.st_mtime_ns


def test_help_exits_zero_without_writing(tmp_path):
    repo, mp = _init_repo(tmp_path)
    before = _state(mp)
    result = _run(repo, mp, "--help")
    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert _state(mp) == before, "--help must not touch manifest bytes/mtime"


def test_default_mode_is_dry_run(tmp_path):
    repo, mp = _init_repo(tmp_path)
    before = _state(mp)
    result = _run(repo, mp, "--content-commit", "HEAD")
    assert result.returncode == 0
    assert "REBIND_DRY_RUN_OK: no bytes written" in result.stdout
    assert _state(mp) == before


def test_rejects_option_like_revision(tmp_path):
    repo, mp = _init_repo(tmp_path)
    before = _state(mp)
    result = _run(repo, mp, "--content-commit", "--help")
    assert result.returncode != 0
    assert "REBIND_FAIL" in result.stdout or "error" in result.stderr
    assert _state(mp) == before


def test_rejects_empty_and_dangling_revisions(tmp_path):
    repo, mp = _init_repo(tmp_path)
    before = _state(mp)
    dangling = "1" * 40
    result = _run(repo, mp, "--content-commit", dangling)
    assert result.returncode != 0
    assert "does not resolve to a commit object" in result.stdout
    assert _state(mp) == before


def test_rejects_non_positive_collection_counts(tmp_path):
    repo, mp = _init_repo(tmp_path)
    before = _state(mp)
    for bad in ("0", "-3", "abc"):
        result = _run(repo, mp, "--content-commit", "HEAD",
                      "--collection-count", bad)
        assert result.returncode != 0, bad
        assert _state(mp) == before, bad


def test_rejects_extra_positional_arguments(tmp_path):
    repo, mp = _init_repo(tmp_path)
    before = _state(mp)
    result = _run(repo, mp, "--content-commit", "HEAD", "unexpected")
    assert result.returncode != 0
    assert _state(mp) == before


def test_rejects_write_and_dry_run_together(tmp_path):
    repo, mp = _init_repo(tmp_path)
    before = _state(mp)
    result = _run(repo, mp, "--content-commit", "HEAD",
                  "--write", "--dry-run")
    assert result.returncode == 2
    assert "mutually exclusive" in result.stdout
    assert _state(mp) == before


def test_write_binds_real_commit_and_reverifies(tmp_path):
    repo, mp = _init_repo(tmp_path)
    before_bytes, _ = _state(mp)
    result = _run(repo, mp, "--content-commit", "HEAD",
                  "--collection-count", "999", "--write")
    assert result.returncode == 0, result.stdout + result.stderr
    assert "REBIND_VERIFY_OK" in result.stdout
    after_bytes, _ = _state(mp)
    assert after_bytes != before_bytes
    doc = json.loads(after_bytes.decode("utf-8"))
    head = _git(repo, "rev-parse", "HEAD")
    assert doc["content_head"] == head
    assert doc["manifest_commit"] == head
    assert "(999 nodes)" in doc["test_results"]["collection_guard"]
    entries = {e["relpath"]: e for e in doc["files"]}
    for rel in ("tests/test_fixture.py", "scripts/tool.py"):
        data = (repo / rel).read_bytes()
        assert entries[rel]["sha256"] == hashlib.sha256(data).hexdigest()
        assert entries[rel]["size_bytes"] == len(data)


def test_write_fails_closed_on_missing_governed_file(tmp_path):
    repo, mp = _init_repo(tmp_path)
    assert _run(repo, mp, "--content-commit", "HEAD",
                "--write").returncode == 0
    (repo / "scripts" / "tool.py").unlink()
    before = _state(mp)
    result = _run(repo, mp, "--content-commit", "HEAD", "--write")
    assert result.returncode == 1
    assert "governed files missing from disk" in result.stdout
    assert _state(mp) == before


def test_dry_run_refuses_unbound_new_governed_file(tmp_path):
    """G-11 — a newly created governed file cannot silently escape."""
    repo, mp = _init_repo(tmp_path)
    _add_new_governed_file(repo)
    before = _state(mp)
    result = _run(repo, mp, "--content-commit", "HEAD")
    assert result.returncode == 1
    assert "not bound to the manifest" in result.stdout
    assert "tests/test_new_escape.py" in result.stdout
    assert _state(mp) == before


def test_write_refuses_unbound_new_governed_file(tmp_path):
    repo, mp = _init_repo(tmp_path)
    _add_new_governed_file(repo)
    before = _state(mp)
    result = _run(repo, mp, "--content-commit", "HEAD", "--write")
    assert result.returncode == 1
    assert "not bound to the manifest" in result.stdout
    assert _state(mp) == before, "refused rebind must not write"


def test_adopt_binds_new_governed_file_explicitly(tmp_path):
    repo, mp = _init_repo(tmp_path)
    _add_new_governed_file(repo)
    result = _run(repo, mp, "--content-commit", "HEAD",
                  "--adopt", "tests/test_new_escape.py", "--write")
    assert result.returncode == 0, result.stdout + result.stderr
    doc = json.loads(mp.read_text(encoding="utf-8"))
    relpaths = {e["relpath"] for e in doc["files"]}
    assert "tests/test_new_escape.py" in relpaths
    assert set(BOUND_FILES) <= relpaths


def test_recorded_scope_exclusion_satisfies_audit(tmp_path):
    repo, mp = _init_repo(tmp_path)
    _add_new_governed_file(repo, "tests/test_legacy.py")
    write_baseline(repo, ("tests/test_legacy.py",))
    result = _run(repo, mp, "--content-commit", "HEAD", "--write")
    assert result.returncode == 0, result.stdout + result.stderr
    doc = json.loads(mp.read_text(encoding="utf-8"))
    relpaths = {e["relpath"] for e in doc["files"]}
    assert "tests/test_legacy.py" not in relpaths, "exclusions stay unbound"
    assert set(BOUND_FILES) <= relpaths


def test_stale_scope_exclusion_baseline_fails_closed(tmp_path):
    repo, mp = _init_repo(tmp_path)
    write_baseline(repo, ("tests/test_never_existed.py",))
    before = _state(mp)
    result = _run(repo, mp, "--content-commit", "HEAD", "--write")
    assert result.returncode == 1
    assert "baseline lists untracked or deleted paths" in result.stdout
    assert _state(mp) == before


def test_audit_scope_passes_and_writes_nothing(tmp_path):
    repo, mp = _init_repo(tmp_path)
    before = _state(mp)
    result = _run(repo, mp, "--audit-scope")
    assert result.returncode == 0
    assert "SCOPE_AUDIT_OK" in result.stdout
    assert "SCOPE_AUDIT_NO_WRITE" in result.stdout
    assert _state(mp) == before


def test_audit_scope_detects_unbound_governed_file(tmp_path):
    repo, mp = _init_repo(tmp_path)
    _add_new_governed_file(repo)
    before = _state(mp)
    result = _run(repo, mp, "--audit-scope")
    assert result.returncode == 1
    assert "not bound to the manifest" in result.stdout
    assert _state(mp) == before


def test_audit_scope_refuses_write_flag(tmp_path):
    repo, mp = _init_repo(tmp_path)
    before = _state(mp)
    result = _run(repo, mp, "--audit-scope", "--write")
    assert result.returncode == 2
    assert "writes nothing" in result.stdout
    assert _state(mp) == before


def test_missing_scope_baseline_fails_closed(tmp_path):
    repo, mp = _init_repo(tmp_path)
    (repo / BASELINE_REL).unlink()
    before = _state(mp)
    result = _run(repo, mp, "--content-commit", "HEAD", "--write")
    assert result.returncode == 1
    assert "scope-exclusion baseline missing" in result.stdout
    assert _state(mp) == before


def test_wrong_scope_baseline_schema_fails_closed(tmp_path):
    repo, mp = _init_repo(tmp_path)
    (repo / BASELINE_REL).write_text(
        json.dumps({"schema": "WRONG", "exclusions": []}), encoding="utf-8")
    before = _state(mp)
    result = _run(repo, mp, "--content-commit", "HEAD", "--write")
    assert result.returncode == 1
    assert "schema must be MANIFEST_SCOPE_EXCLUSIONS_V0" in result.stdout
    assert _state(mp) == before


def test_rejects_tree_and_blob_objects(tmp_path):
    """G-02 — only commit objects may be bound, never trees or blobs."""
    repo, mp = _init_repo(tmp_path)
    before = _state(mp)
    tree_sha = _git(repo, "rev-parse", "HEAD^{tree}")
    result = _run(repo, mp, "--content-commit", tree_sha)
    assert result.returncode != 0
    assert "does not resolve to a commit object" in result.stdout
    assert _state(mp) == before


def test_rejects_missing_revision_argument(tmp_path):
    repo, mp = _init_repo(tmp_path)
    before = _state(mp)
    result = _run(repo, mp)
    assert result.returncode == 2
    assert "--content-commit is required" in result.stdout
    assert _state(mp) == before


def test_repeated_write_is_a_verified_noop(tmp_path):
    repo, mp = _init_repo(tmp_path)
    assert _run(repo, mp, "--content-commit", "HEAD",
                "--write").returncode == 0
    first = _state(mp)[0]
    result = _run(repo, mp, "--content-commit", "HEAD", "--write")
    assert result.returncode == 0
    assert "REBIND_NOOP_OK" in result.stdout
    assert _state(mp)[0] == first


def test_write_rejects_untracked_bound_entry(tmp_path):
    repo, mp = _init_repo(tmp_path)
    _git(repo, "rm", "--cached", "-q", "scripts/tool.py")
    before = _state(mp)
    result = _run(repo, mp, "--content-commit", "HEAD", "--write")
    assert result.returncode == 1
    assert "not tracked by git" in result.stdout
    assert _state(mp) == before


def test_adopt_rejects_invalid_paths(tmp_path):
    repo, mp = _init_repo(tmp_path)
    before = _state(mp)
    for bad, needle in (
            ("../outside.py", "invalid repository-relative path"),
            ("tests/untracked_new.py", "not tracked by git"),
            (MANIFEST_REL, "cannot adopt the manifest into itself"),
            (BOUND_FILES[0], "already bound")):
        result = _run(repo, mp, "--content-commit", "HEAD",
                      "--adopt", bad, "--write")
        assert result.returncode == 1, bad
        assert needle in result.stdout, (bad, result.stdout)
        assert _state(mp) == before, bad


def test_write_binds_exact_entry_set_and_live_digests(tmp_path):
    repo, mp = _init_repo(tmp_path)
    result = _run(repo, mp, "--content-commit", "HEAD", "--write")
    assert result.returncode == 0, result.stdout + result.stderr
    doc = json.loads(mp.read_text(encoding="utf-8"))
    entries = {e["relpath"]: e for e in doc["files"]}
    assert set(entries) == set(BOUND_FILES)
    assert MANIFEST_REL not in entries, "manifest must stay self-excluded"
    for rel in BOUND_FILES:
        data = (repo / rel).read_bytes()
        assert entries[rel]["sha256"] == hashlib.sha256(data).hexdigest()
        assert entries[rel]["size_bytes"] == len(data)
    head = _git(repo, "rev-parse", "HEAD")
    assert doc["content_head"] == head
    assert doc["manifest_commit"] == head


def test_repeated_rejections_are_idempotent(tmp_path):
    """Three repeated invalid runs must leave bytes/mtime untouched."""
    repo, mp = _init_repo(tmp_path)
    before = _state(mp)
    for _ in range(3):
        result = _run(repo, mp, "--content-commit", "--bogus")
        assert result.returncode != 0
    assert _state(mp) == before


def test_root_level_unbound_file_fails_closed(tmp_path):
    """Root-level scope is fail-closed too: no silent escape at the root."""
    repo, mp = _init_repo(tmp_path)
    (repo / "ROOT_NOTE.md").write_text("note\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "root note")
    before = _state(mp)
    result = _run(repo, mp, "--content-commit", "HEAD", "--write")
    assert result.returncode == 1
    assert "not bound to the manifest" in result.stdout
    assert "ROOT_NOTE.md" in result.stdout
    assert _state(mp) == before


def test_recorded_root_level_exclusion_satisfies_audit(tmp_path):
    repo, mp = _init_repo(tmp_path)
    (repo / "ROOT_NOTE.md").write_text("note\n")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "root note")
    doc = json.loads((repo / BASELINE_REL).read_text(encoding="utf-8"))
    doc["exclusions"].append({"relpath": "ROOT_NOTE.md",
                              "category": "root_level_non_governed_metadata",
                              "reason": "outside governed surface"})
    (repo / BASELINE_REL).write_text(
        json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    result = _run(repo, mp, "--content-commit", "HEAD", "--write")
    assert result.returncode == 0, result.stdout + result.stderr


def test_bound_path_also_excluded_fails_closed(tmp_path):
    """A path cannot be both bound and recorded as an exclusion."""
    repo, mp = _init_repo(tmp_path)
    doc = json.loads((repo / BASELINE_REL).read_text(encoding="utf-8"))
    doc["exclusions"].append({"relpath": "scripts/tool.py",
                              "category": "ambiguous",
                              "reason": "deliberately ambiguous"})
    (repo / BASELINE_REL).write_text(
        json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    before = _state(mp)
    result = _run(repo, mp, "--content-commit", "HEAD", "--write")
    assert result.returncode == 1
    assert "both bound and recorded as scope exclusions" in result.stdout
    assert _state(mp) == before


def test_manifest_may_not_be_a_scope_exclusion(tmp_path):
    repo, mp = _init_repo(tmp_path)
    doc = json.loads((repo / BASELINE_REL).read_text(encoding="utf-8"))
    doc["exclusions"].append({"relpath": MANIFEST_REL,
                              "category": "forbidden",
                              "reason": "must never exclude the manifest"})
    (repo / BASELINE_REL).write_text(
        json.dumps(doc, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    before = _state(mp)
    result = _run(repo, mp, "--content-commit", "HEAD", "--write")
    assert result.returncode == 1
    assert "manifest may not be a scope exclusion" in result.stdout
    assert _state(mp) == before
