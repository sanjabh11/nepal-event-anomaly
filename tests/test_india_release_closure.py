"""Adversarial tests for detached India release-closure evidence."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import build_india_release_closure as closure  # noqa: E402


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sidecar(path: Path) -> None:
    Path(str(path) + ".sha256").write_text(
        f"{_sha(path)}  {path.name}\n", encoding="utf-8")


def _git(repo: Path, *args: str) -> str:
    run = subprocess.run(["git", *args], cwd=repo, text=True,
                         capture_output=True, check=True)
    return run.stdout.strip()


def _commit(repo: Path, message: str) -> str:
    _git(repo, "add", "-A")
    _git(repo, "-c", "user.name=Test", "-c", "user.email=test@example.org",
         "commit", "-m", message)
    return _git(repo, "rev-parse", "HEAD")


def _fixture(tmp_path: Path, monkeypatch, *, bad_counts: bool = False,
             include_payload: bool = False,
             extra_release_path: bool = False) -> dict:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "remote", "add", "origin", "https://example.org/repo.git")
    (repo / "baseline.txt").write_text("baseline\n", encoding="utf-8")
    baseline = _commit(repo, "baseline")

    science = repo / "docs/science"
    science.mkdir(parents=True)
    (science / "ARTIFACT_MANIFEST_V0.json").write_text(json.dumps({
        "manifest_version": closure.MANIFEST_VERSION,
        "baseline_head": baseline,
        "self_excluded": True,
        "content_head": baseline,
        "manifest_commit": baseline,
    }), encoding="utf-8")
    (science / "MANIFEST_SCOPE_EXCLUSIONS_V0.json").write_text(
        json.dumps({"schema": "MANIFEST_SCOPE_EXCLUSIONS_V0"}),
        encoding="utf-8")
    (repo / "content.txt").write_text("tested content\n", encoding="utf-8")
    if include_payload:
        payload = repo / closure.PAYLOAD_PATHS[0]
        payload.parent.mkdir(parents=True)
        payload.write_bytes(b"fixture payload")
    tested = _commit(repo, "tested content")

    manifest_sha = _sha(science / "ARTIFACT_MANIFEST_V0.json")
    receipt = {
        "schema": "P5_SUITE_RECEIPT_V2",
        "repository_head": tested,
        "content_head": baseline,
        "manifest_commit": baseline,
        "manifest_sha256": manifest_sha,
        "exit_code": 0,
        "claim_scope": "research_only_no_operational_authorization",
        "counts": {"passed": 2, "skipped": 0, "failed": 0,
                   "errors": 0, "collected": 2},
    }
    if bad_counts:
        receipt["counts"]["collected"] = 3
    receipt_path = science / "SUITE_RECEIPT_20260928_TEST.json"
    receipt_path.write_text(json.dumps(receipt), encoding="utf-8")
    _sidecar(receipt_path)
    _commit(repo, "suite receipt evidence")

    if extra_release_path:
        (repo / "scripts").mkdir()
        (repo / "scripts/unapproved.py").write_text("pass\n", encoding="utf-8")
        _commit(repo, "unapproved code change")
    release = _git(repo, "rev-parse", "HEAD")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    predecessor = evidence / "INDIA_PHASE0_RELEASE_CLOSURE_V2.json"
    predecessor.write_text(json.dumps({
        "schema": "INDIA_PHASE0_RELEASE_CLOSURE_V0", "version": 0,
    }), encoding="utf-8")
    _sidecar(predecessor)
    monkeypatch.setattr(closure, "EVIDENCE_DIR", evidence)
    evidence_root = tmp_path / "evidence-root"
    restricted = evidence_root / "restricted-backups"
    restricted.mkdir(parents=True, mode=0o700)
    os.chmod(restricted, 0o700)
    bundle = restricted / "nepal-event-anomaly-prewrite-20260927.bundle"
    _git(repo, "bundle", "create", str(bundle), "--all")
    os.chmod(bundle, 0o600)
    custody_v0 = restricted / "NEPAL_BACKUP_CUSTODY_V0.json"
    custody_v0.write_text(json.dumps({
        "schema": "NEPAL_BACKUP_CUSTODY_V0", "version": 0,
    }), encoding="utf-8")
    _sidecar(custody_v0)
    os.chmod(custody_v0, 0o600)
    os.chmod(Path(str(custody_v0) + ".sha256"), 0o600)
    custody_path = restricted / "NEPAL_BACKUP_CUSTODY_V1.json"
    custody_path.write_text(json.dumps({
        "schema": "NEPAL_BACKUP_CUSTODY_V1", "version": 1,
        "supersedes": {"file": custody_v0.name, "sha256": _sha(custody_v0)},
        "bundle_relpath": "restricted-backups/nepal-event-anomaly-prewrite-20260927.bundle",
        "bundle_sha256": _sha(bundle),
        "bundle_size_bytes": bundle.stat().st_size,
        "bundle_file_mode_octal": "0600",
        "custody_directory_mode_octal": "0700",
        "retained_outside_git": True,
        "verification": {"git_bundle_verify": "PASS",
                         "verified_utc": "2026-09-28T00:00:00Z"},
    }), encoding="utf-8")
    _sidecar(custody_path)
    os.chmod(custody_path, 0o600)
    os.chmod(Path(str(custody_path) + ".sha256"), 0o600)
    monkeypatch.setattr(closure, "EVIDENCE_ROOT", evidence_root)
    monkeypatch.setattr(closure, "BACKUP_CUSTODY_PATH", custody_path)
    monkeypatch.setattr(closure, "BACKUP_PATH", bundle)
    inventory = {
        "remote_name": "origin",
        "remote_url": "https://example.org/repo.git",
        "inventory_scope": "all_advertised_refs",
        "captured_utc": "2026-09-28T00:00:00Z",
        "head_oid": release,
        "refs": {"refs/heads/main": release,
                 "refs/heads/review": tested,
                 "refs/notes/review": tested},
        "symrefs": {"HEAD": "refs/heads/main"},
    }
    allowed = ["docs/science/SUITE_RECEIPT_20260928_TEST.json",
               "docs/science/SUITE_RECEIPT_20260928_TEST.json.sha256"]
    monkeypatch.setattr(closure, "BASELINE_HEAD", baseline)
    return {"repo": repo, "evidence": evidence, "evidence_root": evidence_root,
            "receipt": receipt_path, "backup_bundle": bundle,
            "backup_custody": custody_path,
            "tested": tested, "release": release, "inventory": inventory,
            "allowed": allowed}


def _build(fixture: dict) -> tuple[Path, dict]:
    doc = closure.build(
        fixture["repo"], fixture["receipt"], fixture["release"],
        fixture["tested"], fixture["allowed"], fixture["inventory"],
        "INDIA_PHASE0_RELEASE_CLOSURE_V2.json")
    path = fixture["evidence"] / "INDIA_PHASE0_RELEASE_CLOSURE_V3.json"
    path.write_text(json.dumps(doc, sort_keys=True, indent=2) + "\n",
                    encoding="utf-8")
    _sidecar(path)
    return path, doc


def test_valid_detached_closure_recomputes_all_bindings(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    path, _ = _build(fixture)
    result = closure.validate_closure(path, fixture["repo"],
                                     fixture["inventory"])
    assert result["status"] == "CLOSURE_OK", result["problems"]


def test_live_remote_branch_drift_is_rejected(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    path, _ = _build(fixture)
    changed = dict(fixture["inventory"])
    changed["refs"] = dict(changed["refs"], **{"refs/heads/review": "0" * 40})
    result = closure.validate_closure(path, fixture["repo"], changed)
    assert result["status"] == "CLOSURE_FAIL"
    assert any("remote refs differs" in problem for problem in result["problems"])


def test_recorded_stale_main_ref_is_rejected(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    path, doc = _build(fixture)
    doc["remote_ref_inventory"]["refs"]["refs/heads/main"] = "0" * 40
    path.write_text(json.dumps(doc), encoding="utf-8")
    _sidecar(path)
    result = closure.validate_closure(path, fixture["repo"])
    assert result["status"] == "CLOSURE_FAIL"
    assert any("refs/heads/main" in problem for problem in result["problems"])


def test_incomplete_history_scan_is_rejected(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    path, doc = _build(fixture)
    doc["clean_history_scan"]["checked_payload_paths"].pop()
    path.write_text(json.dumps(doc), encoding="utf-8")
    _sidecar(path)
    result = closure.validate_closure(path, fixture["repo"],
                                     fixture["inventory"])
    assert result["status"] == "CLOSURE_FAIL"
    assert any("clean_history_scan" in problem
               for problem in result["problems"])


def test_closure_sidecar_is_required_and_verified(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    path, _ = _build(fixture)
    Path(str(path) + ".sha256").unlink()
    assert "missing sidecar" in " ".join(
        closure.validate_closure(path, fixture["repo"],
                                 fixture["inventory"])["problems"])


@pytest.mark.parametrize("sidecar_action", ["missing", "tampered"])
def test_suite_receipt_sidecar_is_required_and_verified(
        tmp_path, monkeypatch, sidecar_action):
    fixture = _fixture(tmp_path, monkeypatch)
    path, _ = _build(fixture)
    sidecar = Path(str(fixture["receipt"]) + ".sha256")
    if sidecar_action == "missing":
        sidecar.unlink()
    else:
        sidecar.write_text("0" * 64 + "  bad.json\n", encoding="utf-8")
    result = closure.validate_closure(path, fixture["repo"],
                                     fixture["inventory"])
    assert result["status"] == "CLOSURE_FAIL"
    assert any("sidecar" in problem for problem in result["problems"])


def test_backup_custody_directory_mode_is_checked_on_disk(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    path, _ = _build(fixture)
    os.chmod(fixture["backup_bundle"].parent, 0o755)
    result = closure.validate_closure(path, fixture["repo"],
                                     fixture["inventory"])
    assert result["status"] == "CLOSURE_FAIL"
    assert any("actual backup custody directory mode" in problem
               for problem in result["problems"])


def test_build_rejects_inventory_for_another_remote(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch)
    inventory = dict(fixture["inventory"],
                     remote_url="https://other.example/repo.git")
    with pytest.raises(closure.ClosureError, match="configured origin"):
        closure.build(fixture["repo"], fixture["receipt"],
                      fixture["release"], fixture["tested"],
                      fixture["allowed"], inventory,
                      "INDIA_PHASE0_RELEASE_CLOSURE_V2.json")


def test_bad_receipt_counts_cannot_build_closure(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch, bad_counts=True)
    with pytest.raises(closure.ClosureError, match="receipt is not closure-ready"):
        closure.build(fixture["repo"], fixture["receipt"], fixture["release"],
                      fixture["tested"], fixture["allowed"],
                      fixture["inventory"],
                      "INDIA_PHASE0_RELEASE_CLOSURE_V2.json")


def test_reachable_payload_path_blocks_closure(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch, include_payload=True)
    with pytest.raises(closure.ClosureError,
                       match="reachable history contains identified payload paths"):
        closure.build(fixture["repo"], fixture["receipt"], fixture["release"],
                      fixture["tested"], fixture["allowed"],
                      fixture["inventory"],
                      "INDIA_PHASE0_RELEASE_CLOSURE_V2.json")


def test_non_evidence_release_diff_is_rejected(tmp_path, monkeypatch):
    fixture = _fixture(tmp_path, monkeypatch, extra_release_path=True)
    actual = _git(fixture["repo"], "diff", "--name-only",
                  f"{fixture['tested']}..{fixture['release']}").splitlines()
    with pytest.raises(closure.ClosureError,
                       match="release-only diff contains paths outside"):
        closure.build(fixture["repo"], fixture["receipt"], fixture["release"],
                      fixture["tested"], actual, fixture["inventory"],
                      "INDIA_PHASE0_RELEASE_CLOSURE_V2.json")


def test_remote_url_sanitization_drops_credentials_and_query():
    assert closure._clean_remote_url(
        "https://user:secret@example.org/repo.git?token=secret") == (
            "https://example.org/repo.git")
    assert closure._clean_remote_url(
        "git@example.org:team/repo.git") == "ssh://example.org/team/repo.git"


def test_capture_remote_inventory_includes_every_advertised_ref(
        tmp_path, monkeypatch):
    main = "1" * 40
    extra = "2" * 40
    monkeypatch.setattr(closure, "_git", lambda _root, *args: (
        "https://example.org/repo.git"))

    def fake_run(args, **kwargs):
        assert args == ["git", "ls-remote", "--symref", "origin"]
        return SimpleNamespace(
            returncode=0,
            stdout=(f"ref: refs/heads/main\tHEAD\n{main}\tHEAD\n"
                    f"{main}\trefs/heads/main\n{extra}\trefs/custom/review\n"),
            stderr="")

    monkeypatch.setattr(closure.subprocess, "run", fake_run)
    inventory = closure.capture_remote_inventory(tmp_path)
    assert inventory["inventory_scope"] == "all_advertised_refs"
    assert inventory["refs"] == {
        "refs/heads/main": main,
        "refs/custom/review": extra,
    }


def test_external_current_closure_if_present():
    matching = []
    for path in closure.EVIDENCE_DIR.glob(
            "INDIA_PHASE0_RELEASE_CLOSURE_V*.json"):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if doc.get("schema") == closure.SCHEMA:
            version = doc.get("version")
            if isinstance(version, int) and not isinstance(version, bool):
                matching.append((version, path, doc))
    if not matching:
        pytest.skip("no V1 detached release closure is published here")

    _, path, doc = max(matching, key=lambda item: (item[0], item[1].name))
    head = _git(ROOT, "rev-parse", "HEAD")
    if doc.get("release_head") != head:
        pytest.skip("latest detached closure is for a prior release HEAD")
    if _git(ROOT, "status", "--porcelain"):
        pytest.skip("detached closure validates only an exact clean checkout")

    result = closure.validate_closure(path, ROOT)
    assert result["status"] == "CLOSURE_OK", result["problems"]
