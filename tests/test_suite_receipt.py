"""P5-RCPT: machine suite receipt (P5_SUITE_RECEIPT_V2, A4-03/13/15/17).

The receipt generator runs pytest itself and binds the tree under test:
live HEAD, the exact manifest bytes (``manifest_sha256``), and the
manifest's ``content_head``/``manifest_commit`` are all captured BEFORE
pytest runs.  These tests use a synthetic manifest under ``tmp_path``
and a monkeypatched ``subprocess.run`` — the real suite and real
evidence roots are never touched.
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                     / "scripts"))

import build_p5_suite_receipt as bsr


_FAKE_HEAD = "a" * 40
_MANIFEST_CONTENT_HEAD = "b" * 40
_MANIFEST_COMMIT = "c" * 40
_Z_UTC = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z").fullmatch

_COLLECT_OUT = "collecting ...\n6 tests collected in 0.10s\n"
_RUN_OUT = ("tests/test_fake.py .....\n"
            "===== 5 passed, 1 skipped, 2 warnings in 0.50s =====\n")


def _completed(argv, returncode, stdout="", stderr=""):
    return subprocess.CompletedProcess(argv, returncode,
                                       stdout=stdout, stderr=stderr)


def _fake_run(*, head=_FAKE_HEAD, head_rc=0,
              collect_out=_COLLECT_OUT, run_out=_RUN_OUT, run_rc=0,
              on_run=None):
    """Dispatch fake subprocess calls for git/pytest argv."""
    def fake(argv, cwd=None, capture_output=False, text=False,
             check=False):
        if argv[:2] == ["git", "rev-parse"]:
            return _completed(argv, head_rc,
                              stdout=(head + "\n") if head else "")
        if "--collect-only" in argv:
            return _completed(argv, 0, stdout=collect_out)
        if on_run is not None:
            on_run()
        return _completed(argv, run_rc, stdout=run_out)
    return fake


@pytest.fixture
def manifest(tmp_path):
    doc = {"content_head": _MANIFEST_CONTENT_HEAD,
           "manifest_commit": _MANIFEST_COMMIT,
           "files": {}}
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    return path


@pytest.fixture(autouse=True)
def _fast_environment(monkeypatch):
    monkeypatch.setattr(
        bsr, "_environment",
        lambda: {"python": "test", "package_count": 0,
                 "packages": {}, "environment_digest": "e" * 64})


def _run(tmp_path, manifest, monkeypatch, **fake_kwargs):
    monkeypatch.setattr(subprocess, "run", _fake_run(**fake_kwargs))
    return bsr.run_suite(repo=tmp_path, pytest_args="tests/ -q",
                         manifest_path=manifest)


# ------------------------------------------------------------------
# happy path / schema V2 surface
# ------------------------------------------------------------------

def test_schema_is_v2(tmp_path, manifest, monkeypatch):
    receipt = _run(tmp_path, manifest, monkeypatch)
    assert receipt["schema"] == "P5_SUITE_RECEIPT_V2"


def test_manifest_sha256_binds_live_bytes(tmp_path, manifest,
                                        monkeypatch):
    receipt = _run(tmp_path, manifest, monkeypatch)
    assert receipt["manifest_sha256"] == hashlib.sha256(
        manifest.read_bytes()).hexdigest()
    assert receipt["content_head"] == _MANIFEST_CONTENT_HEAD
    assert receipt["manifest_commit"] == _MANIFEST_COMMIT


def test_manifest_digest_captured_before_pytest(tmp_path, manifest,
                                                monkeypatch):
    """A manifest mutated DURING the suite must not change the bound
    digest — the binding is taken at run start."""
    original = hashlib.sha256(manifest.read_bytes()).hexdigest()

    def _mutate():
        manifest.write_text('{"content_head": "f" * 40}',
                            encoding="utf-8")

    receipt = _run(tmp_path, manifest, monkeypatch, on_run=_mutate)
    assert receipt["manifest_sha256"] == original
    assert receipt["content_head"] == _MANIFEST_CONTENT_HEAD


def test_execution_window_fields(tmp_path, manifest, monkeypatch):
    receipt = _run(tmp_path, manifest, monkeypatch)
    window = receipt["execution_window"]
    assert set(window) == {"collect_started_utc", "suite_started_utc",
                           "suite_completed_utc"}
    for value in window.values():
        assert _Z_UTC(value), value
    assert (window["collect_started_utc"] <= window["suite_started_utc"]
            <= window["suite_completed_utc"])
    # Execution timing is activity metadata, not a data-date field.
    assert "data maximum dates" in receipt["data_context"]


def test_repository_head_and_heads_not_conflated(tmp_path, manifest,
                                                 monkeypatch):
    """repository_head is the LIVE head; it may legitimately differ
    from the manifest's content_head after a manifest-only rebind."""
    receipt = _run(tmp_path, manifest, monkeypatch,
                   head="d" * 40)
    assert receipt["repository_head"] == "d" * 40
    assert receipt["content_head"] == _MANIFEST_CONTENT_HEAD
    assert receipt["manifest_commit"] == _MANIFEST_COMMIT


def test_counts_include_collected_and_warnings(tmp_path, manifest,
                                               monkeypatch):
    receipt = _run(tmp_path, manifest, monkeypatch)
    assert receipt["counts"] == {
        "passed": 5, "skipped": 1, "failed": 0, "errors": 0,
        "warnings": 2, "collected": 6}
    assert receipt["exit_code"] == 0
    assert isinstance(receipt["duration_s"], float)
    assert isinstance(receipt["command_digest"], str)
    assert len(receipt["command_digest"]) == 64


# ------------------------------------------------------------------
# fail-closed paths
# ------------------------------------------------------------------

def test_count_inconsistency_fails_closed(tmp_path, manifest,
                                         monkeypatch):
    with pytest.raises(bsr.ClosureError, match="!="):
        _run(tmp_path, manifest, monkeypatch,
             collect_out="10 tests collected in 0.10s\n")


def test_missing_summary_tail_fails_closed(tmp_path, manifest,
                                           monkeypatch):
    with pytest.raises(bsr.ClosureError, match="summary"):
        _run(tmp_path, manifest, monkeypatch,
             run_out="no summary tail here\n")


def test_missing_collected_count_fails_closed(tmp_path, manifest,
                                              monkeypatch):
    with pytest.raises(bsr.ClosureError, match="collection count"):
        _run(tmp_path, manifest, monkeypatch, collect_out="nothing\n")


def test_unparseable_manifest_fails_closed(tmp_path, monkeypatch):
    bad = tmp_path / "bad-manifest.json"
    bad.write_text("not json {{{", encoding="utf-8")
    with pytest.raises(bsr.ClosureError, match="parseable"):
        _run(tmp_path, bad, monkeypatch)


def test_manifest_missing_heads_fails_closed(tmp_path, monkeypatch):
    bad = tmp_path / "no-heads.json"
    bad.write_text('{"files": {}}', encoding="utf-8")
    with pytest.raises(bsr.ClosureError, match="content_head"):
        _run(tmp_path, bad, monkeypatch)


def test_missing_manifest_fails_closed(tmp_path, monkeypatch):
    with pytest.raises(bsr.ClosureError, match="unreadable"):
        _run(tmp_path, tmp_path / "ghost.json", monkeypatch)


def test_unresolvable_head_fails_closed(tmp_path, manifest,
                                        monkeypatch):
    with pytest.raises(bsr.ClosureError, match="HEAD"):
        _run(tmp_path, manifest, monkeypatch, head_rc=1, head="")


# ------------------------------------------------------------------
# CLI surface
# ------------------------------------------------------------------

def test_cli_dry_run_emits_v2_receipt(tmp_path, manifest, monkeypatch,
                                      capsys):
    monkeypatch.setattr(subprocess, "run", _fake_run())
    rc = bsr.main(["--repo", str(tmp_path),
                   "--manifest", str(manifest),
                   "--pytest-args", "tests/ -q",
                   "--out", str(tmp_path / "receipt.json"),
                   "--dry-run"])
    assert rc == 0
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "SUITE_RECEIPT_DRY_RUN_OK"
    assert out["receipt"]["schema"] == "P5_SUITE_RECEIPT_V2"
    assert not (tmp_path / "receipt.json").exists()


def test_cli_failure_returns_one(tmp_path, monkeypatch, capsys):
    missing = tmp_path / "ghost.json"
    monkeypatch.setattr(subprocess, "run", _fake_run())
    rc = bsr.main(["--repo", str(tmp_path),
                   "--manifest", str(missing),
                   "--out", str(tmp_path / "receipt.json"),
                   "--dry-run"])
    assert rc == 1
    assert "SUITE_RECEIPT_FAIL" in capsys.readouterr().out
