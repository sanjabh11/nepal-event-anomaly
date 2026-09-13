"""W3 science-contract package tests — build + verify round-trip."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nepal.framework_v1 import science_contract_package as pkg


def _context(tmp_path):
    return {
        "package_dir": tmp_path / "pkg",
        "run_root_name": "test-run-root",
        "code_revision": "ce2a926d6c9b806dd282ae1f329be6a6b5fba3b8",
        "candidate_generation_id":
            "d1-remediated-provenance-recovery-v2-f1-20260913",
        "ranked_array_canonical_sha256":
            "a4e69e450cf87f46970ff38ceb74fc9d10036bd702d0e547a6e829a6"
            "ae058988",
        "ranked_payload_sha256":
            "d0561f07e2cbae545714ee690a9f9c5ca7e58e1af7ee60133240888f975"
            "2a0ec",
        "ranked_payload_sha256_domain_status":
            "HISTORICAL_ORPHAN_UNKNOWN_DOMAIN",
        "b_status": "B_TO_C_BLOCKED",
        "evidence_references": [],
    }


def test_package_builds_and_verifies(tmp_path):
    ctx = _context(tmp_path)
    result = pkg.build_science_contract_package(**ctx)
    assert result["package_status"] == \
        "SCIENCE_CONTRACT_SCAFFOLD_READY_WITH_FMX_BLOCKED"
    ok, problems = pkg.verify_science_contract_package(ctx["package_dir"])
    assert ok, problems


def test_atomic_checkpoint_states(tmp_path):
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    ckpt = json.loads(
        (ctx["package_dir"] / "checkpoint.json").read_text())
    assert ckpt["run_state"] == "PASS"
    # RUNNING/INCOMPLETE/BLOCKED must be representable states
    assert "RUNNING" in pkg.CHECKPOINT_STATES
    assert "INCOMPLETE" in pkg.CHECKPOINT_STATES
    assert "BLOCKED" in pkg.CHECKPOINT_STATES
    assert "PASS" in pkg.CHECKPOINT_STATES


def test_required_files_present(tmp_path):
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    d = ctx["package_dir"]
    for name in ("run_context.json", "mec_schema.json",
                 "fmx_blocked.json", "validation_scaffold.json",
                 "research_no_claims.json",
                 "science_contract_package_index.json",
                 "research_contract_handoff.json", "checkpoint.json"):
        assert (d / name).is_file(), name


def test_fmx_blocked_status_in_package(tmp_path):
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    fmx_env = json.loads(
        (ctx["package_dir"] / "fmx_blocked.json").read_text())
    assert fmx_env["fmx_status"] == "FMX_BLOCKED_PENDING_EXPLICIT_FREEZE"


def test_tamper_detected(tmp_path):
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    target = ctx["package_dir"] / "research_no_claims.json"
    doc = json.loads(target.read_text())
    doc["promotion_eligible"] = True
    target.write_text(json.dumps(doc))
    ok, problems = pkg.verify_science_contract_package(ctx["package_dir"])
    assert not ok


def test_index_tamper_detected(tmp_path):
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    idx_path = ctx["package_dir"] / "science_contract_package_index.json"
    idx = json.loads(idx_path.read_text())
    idx["files"][0]["sha256"] = "00" * 32
    idx_path.write_text(json.dumps(idx))
    ok, problems = pkg.verify_science_contract_package(
        ctx["package_dir"])
    assert not ok


def test_forbidden_status_rejected(tmp_path):
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    ctx_file = ctx["package_dir"] / "run_context.json"
    doc = json.loads(ctx_file.read_text())
    doc["status"] = "READY"
    ctx_file.write_text(json.dumps(doc))
    ok, _ = pkg.verify_science_contract_package(ctx["package_dir"])
    assert not ok


def test_incomplete_checkpoint_is_not_pass(tmp_path):
    """A checkpoint left in RUNNING must not verify as a complete pkg."""
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    ckpt_path = ctx["package_dir"] / "checkpoint.json"
    doc = json.loads(ckpt_path.read_text())
    doc["run_state"] = "RUNNING"
    ckpt_path.write_text(json.dumps(doc))
    ok, _ = pkg.verify_science_contract_package(ctx["package_dir"])
    assert not ok


def test_no_absolute_paths_in_index(tmp_path):
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    idx = json.loads(
        (ctx["package_dir"] / "science_contract_package_index.json")
        .read_text())
    for entry in idx["files"]:
        assert not entry["relative_path"].startswith("/")
        assert ".." not in entry["relative_path"].split("/")


def test_no_ef_artifacts(tmp_path):
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    names = [p.name for p in ctx["package_dir"].iterdir()]
    assert not any(n.startswith(("e_", "f_", "E_", "F_"))
                   for n in names)


def test_no_claims_and_flags(tmp_path):
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    nc = json.loads(
        (ctx["package_dir"] / "research_no_claims.json").read_text())
    assert nc["promotion_eligible"] is False
    assert nc["production_authorized"] is False
    assert nc["warning_path_authorized"] is False


def test_disk_reserve_guard(tmp_path, monkeypatch):
    ctx = _context(tmp_path)
    import shutil
    real = shutil.disk_usage

    class FakeUsage:
        total = 100
        used = 90
        free = 4 * (1024 ** 3)  # below the 8 GiB reserve

    monkeypatch.setattr(shutil, "disk_usage",
                        lambda *a, **k: FakeUsage())
    with pytest.raises(RuntimeError):
        pkg.build_science_contract_package(**ctx)
    monkeypatch.setattr(shutil, "disk_usage", real)


def test_no_execution_surface():
    for forbidden in ("run_validation", "fit", "predict", "rank_box",
                      "leave_one_layer_out_top5",
                      "mint_freeze_token"):
        assert not hasattr(pkg, forbidden)


def test_module_has_no_quarantined_imports():
    import inspect
    from nepal.framework_v1 import research_boundaries
    src = inspect.getsource(pkg)
    ok, hits = research_boundaries.scan_source_for_quarantined_imports(src)
    assert ok, hits
