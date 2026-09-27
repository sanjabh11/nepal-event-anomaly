"""W3 science-contract package tests — build + verify round-trip."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nepal.framework_v1 import science_contract_package as pkg


_RANKED_DIGEST = ("a4e69e450cf87f46970ff38ceb74fc9d10036bd702d0e547a6"
                  "e829a6ae058988")


def _bound_b_evidence(ev_root: Path) -> None:
    """Write a self-hashed B envelope fixture under the evidence root —
    carries the ranked digest and a status consistent with
    B_TO_C_BLOCKED, satisfying PKG-11 file-bound evidence."""
    from nepal.framework_v1.provenance import bind_artifact_envelope
    b_doc = bind_artifact_envelope({
        "envelope_type": "B_SCREEN_V1",
        "b_status": "B_TO_C_BLOCKED",
        "gate_passed": False,
        "ranked_array_canonical_sha256": _RANKED_DIGEST,
        "disclaimer": "synthetic test B evidence — screening only"})
    (ev_root / "b_screen.json").write_text(
        json.dumps(b_doc, indent=1, sort_keys=True) + "\n")


def _context(tmp_path):
    ev_root = tmp_path / "test-run-root"
    ev_root.mkdir(parents=True, exist_ok=True)
    _bound_b_evidence(ev_root)
    return {
        "package_dir": tmp_path / "pkg",
        "run_root_name": "test-run-root",
        "code_revision": "ce2a926d6c9b806dd282ae1f329be6a6b5fba3b8",
        "candidate_generation_id":
            "d1-remediated-provenance-recovery-v2-f1-20260913",
        "ranked_array_canonical_sha256": _RANKED_DIGEST,
        "ranked_payload_sha256":
            "d0561f07e2cbae545714ee690a9f9c5ca7e58e1af7ee60133240888f975"
            "2a0ec",
        "ranked_payload_sha256_domain_status":
            "HISTORICAL_ORPHAN_UNKNOWN_DOMAIN",
        "b_status": "B_TO_C_BLOCKED",
        "b_evidence_relative_path": "b_screen.json",
        "evidence_root": ev_root,
        "evidence_references": [],
        "forbidden_roots": [tmp_path / "protected"],
    }


def test_package_builds_and_verifies(tmp_path):
    ctx = _context(tmp_path)
    result = pkg.build_science_contract_package(**ctx)
    assert result["package_status"] == \
        "SCIENCE_CONTRACT_SCAFFOLD_READY_WITH_FMX_BLOCKED"
    ok, problems = pkg.verify_science_contract_package(
        ctx["package_dir"], evidence_root=ctx["evidence_root"])
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
                 "research_contract_handoff.json", "checkpoint.json",
                 "package_seal.json"):
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


class TestN1Hardening:
    def test_verify_fails_on_extra_file(self, tmp_path):
        ctx = _context(tmp_path)
        pkg.build_science_contract_package(**ctx)
        (ctx["package_dir"] / "extra_file.json").write_text("{}")
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"])
        assert not ok
        assert any("extra" in p or "not indexed" in p for p in problems)

    def test_verify_fails_on_symlink(self, tmp_path):
        ctx = _context(tmp_path)
        pkg.build_science_contract_package(**ctx)
        target = ctx["package_dir"] / "run_context.json"
        (ctx["package_dir"] / "evil.json").symlink_to(target)
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"])
        assert not ok
        assert any("symlink" in p for p in problems)

    def test_verify_fails_on_symlinked_indexed_file(self, tmp_path):
        ctx = _context(tmp_path)
        pkg.build_science_contract_package(**ctx)
        target = ctx["package_dir"] / "run_context.json"
        real = target.read_bytes()
        target.unlink()
        target.symlink_to(ctx["package_dir"] / "mec_schema.json")
        ok, _ = pkg.verify_science_contract_package(ctx["package_dir"])
        assert not ok

    def test_build_rejects_nonempty_dir(self, tmp_path):
        ctx = _context(tmp_path)
        ctx["package_dir"].mkdir()
        (ctx["package_dir"] / "preexisting.json").write_text("{}")
        with pytest.raises((ValueError, RuntimeError)):
            pkg.build_science_contract_package(**ctx)

    def test_build_rejects_symlink_root(self, tmp_path):
        real = tmp_path / "real_dir"
        real.mkdir()
        link = tmp_path / "link_dir"
        link.symlink_to(real)
        ctx = _context(tmp_path)
        ctx["package_dir"] = link
        with pytest.raises((ValueError, RuntimeError)):
            pkg.build_science_contract_package(**ctx)

    def test_build_rejects_forbidden_root(self, tmp_path):
        forbidden = tmp_path / "protected"
        forbidden.mkdir()
        ctx = _context(tmp_path)
        ctx["package_dir"] = forbidden / "pkg"
        ctx["forbidden_roots"] = [forbidden]
        with pytest.raises((ValueError, RuntimeError)):
            pkg.build_science_contract_package(**ctx)

    def test_checkpoint_binds_index_digest(self, tmp_path):
        ctx = _context(tmp_path)
        pkg.build_science_contract_package(**ctx)
        ckpt = json.loads(
            (ctx["package_dir"] / "checkpoint.json").read_text())
        idx_sha = pkg_sha = None
        import hashlib
        idx_sha = hashlib.sha256(
            (ctx["package_dir"] / "science_contract_package_index.json")
            .read_bytes()).hexdigest()
        assert ckpt.get("index_file_sha256") == idx_sha
        # corrupt the linkage
        ckpt["index_file_sha256"] = "00" * 32
        (ctx["package_dir"] / "checkpoint.json").write_text(
            json.dumps(ckpt))
        ok, _ = pkg.verify_science_contract_package(ctx["package_dir"])
        assert not ok

    def test_typed_evidence_references(self, tmp_path):
        from nepal.framework_v1.provenance import sha256_file
        ev_root = tmp_path / "test-run-root"
        ev_root.mkdir()
        (ev_root / "b_screen.json").write_text('{"x": 1}')
        ctx = _context(tmp_path)
        ctx["evidence_root"] = ev_root
        ctx["evidence_references"] = [{
            "role": "f1_digest_closure_package",
            "relative_path": "b_screen.json",
            "sha256": sha256_file(ev_root / "b_screen.json")}]
        res = pkg.build_science_contract_package(**ctx)
        ok, _ = pkg.verify_science_contract_package(ctx["package_dir"],
                                                    evidence_root=ev_root)
        assert ok

    def test_absolute_evidence_path_rejected(self, tmp_path):
        ctx = _context(tmp_path)
        ctx["evidence_references"] = [{
            "role": "bad",
            "relative_path": "/Users/sanjayb/secret.json",
            "sha256": "ab" * 32}]
        with pytest.raises(ValueError):
            pkg.build_science_contract_package(**ctx)

    def test_untyped_evidence_reference_rejected(self, tmp_path):
        ctx = _context(tmp_path)
        ctx["evidence_references"] = [{"note": "no digest"}]
        with pytest.raises(ValueError):
            pkg.build_science_contract_package(**ctx)


# ---------- N3 package-integrity hardening ----------


def test_missing_index_digest_in_checkpoint_rejected(tmp_path):
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    ckpt_path = ctx["package_dir"] / "checkpoint.json"
    ckpt = json.loads(ckpt_path.read_text())
    del ckpt["index_file_sha256"]
    ckpt_path.write_text(json.dumps(ckpt))
    ok, problems = pkg.verify_science_contract_package(
        ctx["package_dir"])
    assert not ok and any("index_file_sha256" in p for p in problems)


def test_tampered_checkpoint_self_hash_rejected(tmp_path):
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    ckpt_path = ctx["package_dir"] / "checkpoint.json"
    ckpt = json.loads(ckpt_path.read_text())
    ckpt["run_state"] = "BLOCKED"
    ckpt_path.write_text(json.dumps(ckpt))
    ok, problems = pkg.verify_science_contract_package(
        ctx["package_dir"])
    assert not ok


def test_missing_seal_rejected(tmp_path):
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    (ctx["package_dir"] / "package_seal.json").unlink()
    ok, problems = pkg.verify_science_contract_package(
        ctx["package_dir"])
    assert not ok


def test_seal_omits_a_file_rejected(tmp_path):
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    seal_path = ctx["package_dir"] / "package_seal.json"
    seal = json.loads(seal_path.read_text())
    del seal["files"]["run_context.json"]
    seal_path.write_text(json.dumps(seal))
    ok, problems = pkg.verify_science_contract_package(
        ctx["package_dir"])
    assert not ok


def test_tmp_file_rejected(tmp_path):
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    (ctx["package_dir"] / ".tmp_stale.json").write_text("{}")
    ok, problems = pkg.verify_science_contract_package(
        ctx["package_dir"])
    assert not ok and any("extra" in p for p in problems)


def test_duplicate_index_path_rejected(tmp_path):
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    idx_path = (ctx["package_dir"] /
                "science_contract_package_index.json")
    idx = json.loads(idx_path.read_text())
    idx["files"].append(dict(idx["files"][0]))
    idx_path.write_text(json.dumps(idx))
    ok, problems = pkg.verify_science_contract_package(
        ctx["package_dir"])
    assert not ok and any("duplicate" in p for p in problems)


def test_missing_mandatory_flag_rejected(tmp_path):
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    target = ctx["package_dir"] / "research_no_claims.json"
    doc = json.loads(target.read_text())
    del doc["warning_path_authorized"]
    target.write_text(json.dumps(doc))
    ok, problems = pkg.verify_science_contract_package(
        ctx["package_dir"])
    assert not ok and any("warning_path_authorized" in p
                          for p in problems)


def test_build_requires_forbidden_roots(tmp_path):
    ctx = _context(tmp_path)
    ctx["forbidden_roots"] = None
    with pytest.raises(ValueError):
        pkg.build_science_contract_package(**ctx)


def test_build_under_forbidden_root_rejected(tmp_path):
    ctx = _context(tmp_path)
    ctx["package_dir"] = tmp_path / "protected" / "pkg"
    with pytest.raises(ValueError):
        pkg.build_science_contract_package(**ctx)


def test_evidence_refs_require_root(tmp_path):
    ev_root = tmp_path / "test-run-root"
    ev_root.mkdir()
    (ev_root / "ref.json").write_text('{"x": 1}')
    from nepal.framework_v1.provenance import sha256_file
    ctx = _context(tmp_path)
    del ctx["evidence_root"]
    del ctx["b_evidence_relative_path"]
    ctx["evidence_references"] = [
        {"role": "test", "relative_path": "ref.json",
         "sha256": sha256_file(ev_root / "ref.json")}]
    with pytest.raises(ValueError):
        pkg.build_science_contract_package(**ctx)  # no evidence_root


def test_evidence_ref_digest_mismatch_rejected(tmp_path):
    ev_root = tmp_path / "test-run-root"
    ev_root.mkdir()
    (ev_root / "ref.json").write_text('{"x": 1}')
    ctx = _context(tmp_path)
    ctx["evidence_root"] = ev_root
    ctx["evidence_references"] = [
        {"role": "test", "relative_path": "ref.json",
         "sha256": "ab" * 32}]
    with pytest.raises(ValueError):
        pkg.build_science_contract_package(**ctx)


def test_evidence_refs_verify_from_disk(tmp_path):
    ev_root = tmp_path / "test-run-root"
    ev_root.mkdir()
    (ev_root / "ref.json").write_text('{"x": 1}')
    from nepal.framework_v1.provenance import sha256_file
    ctx = _context(tmp_path)
    ctx["evidence_root"] = ev_root
    ctx["evidence_references"] = [
        {"role": "test", "relative_path": "ref.json",
         "sha256": sha256_file(ev_root / "ref.json")}]
    pkg.build_science_contract_package(**ctx)
    ok, problems = pkg.verify_science_contract_package(
        ctx["package_dir"], evidence_root=ev_root)
    assert ok, problems
    # without the root the declared refs are unverified -> fail closed
    ok2, problems2 = pkg.verify_science_contract_package(
        ctx["package_dir"])
    assert not ok2 and any("evidence" in p for p in problems2)


def test_evidence_ref_tampered_file_rejected(tmp_path):
    ev_root = tmp_path / "test-run-root"
    ev_root.mkdir()
    (ev_root / "ref.json").write_text('{"x": 1}')
    from nepal.framework_v1.provenance import sha256_file
    ctx = _context(tmp_path)
    ctx["evidence_root"] = ev_root
    ctx["evidence_references"] = [
        {"role": "test", "relative_path": "ref.json",
         "sha256": sha256_file(ev_root / "ref.json")}]
    pkg.build_science_contract_package(**ctx)
    (ev_root / "ref.json").write_text('{"tampered": true}')
    ok, problems = pkg.verify_science_contract_package(
        ctx["package_dir"], evidence_root=ev_root)
    assert not ok and any("evidence" in p for p in problems)


# ---------- N4 portability / identity hardening ----------


def _evidence_ctx(tmp_path, n_refs=1):
    """Context whose evidence root carries the bound run-root name."""
    from nepal.framework_v1.provenance import sha256_file
    ev_root = tmp_path / "test-run-root"
    ev_root.mkdir(exist_ok=True)
    refs = []
    for i in range(n_refs):
        f = ev_root / f"ref{i}.json"
        f.write_text('{"i": %d}' % i)
        refs.append({"role": f"ref{i}", "relative_path": f.name,
                     "sha256": sha256_file(f)})
    ctx = _context(tmp_path)
    ctx["evidence_root"] = ev_root
    ctx["evidence_references"] = refs
    return ctx, ev_root


def test_wrong_named_evidence_root_rejected(tmp_path):
    """PKG-07: a sibling root with identical files but a different
    directory name fails identity binding at build and verify."""
    ctx, ev_root = _evidence_ctx(tmp_path)
    pkg.build_science_contract_package(**ctx)
    ok, problems = pkg.verify_science_contract_package(
        ctx["package_dir"], evidence_root=ev_root)
    assert ok, problems
    # clone the evidence into a wrongly-named sibling root
    import shutil as _sh
    wrong = tmp_path / "evidence"
    _sh.copytree(ev_root, wrong)
    ok2, problems2 = pkg.verify_science_contract_package(
        ctx["package_dir"], evidence_root=wrong)
    assert not ok2 and any("identity" in p for p in problems2)
    ctx2 = _context(tmp_path / "b")
    ctx2["package_dir"] = tmp_path / "b" / "pkg"
    ctx2["evidence_root"] = wrong
    ctx2["evidence_references"] = ctx["evidence_references"]
    ctx2["forbidden_roots"] = [tmp_path / "protected"]
    with pytest.raises(ValueError):
        pkg.build_science_contract_package(**ctx2)


def test_environment_fingerprint_bound(tmp_path):
    """OPS-04: run_context carries a reproducible environment record."""
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    doc = json.loads(
        (ctx["package_dir"] / "run_context.json").read_text())
    env = doc["environment"]
    import platform as _pf
    assert env["python_version"] == _pf.python_version()
    assert env["tz_policy"] == "UTC"
    assert env["lock_file"] == "requirements.txt"
    assert isinstance(env["lock_sha256"], str) and \
        len(env["lock_sha256"]) == 64


def test_absolute_path_string_in_doc_rejected(tmp_path):
    """OPS-03: no absolute machine-local path may appear in a doc."""
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    target = ctx["package_dir"] / "research_no_claims.json"
    doc = json.loads(target.read_text())
    doc["note"] = "/Users/sanjayb/some/host/path"
    target.write_text(json.dumps(doc))
    ok, problems = pkg.verify_science_contract_package(
        ctx["package_dir"])
    assert not ok and any("absolute path" in p for p in problems)


def test_active_pointer_stale_sibling_rejected(tmp_path):
    """PKG-09: with an active-generation pointer at the root, a package
    under that root that is not the named generation fails."""
    ctx, ev_root = _evidence_ctx(tmp_path)
    # package must live under the root for the pointer to apply
    ctx["package_dir"] = ev_root / "gen-a" / "package"
    pkg.build_science_contract_package(**ctx)
    ok, problems = pkg.verify_science_contract_package(
        ctx["package_dir"], evidence_root=ev_root)
    assert ok, problems
    assert (ev_root / "active_generation.json").is_file()
    # build a successor under the same root -> pointer moves to gen-b
    ctx2 = _context(tmp_path)
    ctx2["package_dir"] = ev_root / "gen-b" / "package"
    ctx2["evidence_root"] = ev_root
    ctx2["evidence_references"] = ctx["evidence_references"]
    ctx2["forbidden_roots"] = ctx["forbidden_roots"]
    pkg.build_science_contract_package(**ctx2)
    ok_b, _ = pkg.verify_science_contract_package(
        ctx2["package_dir"], evidence_root=ev_root)
    assert ok_b
    # the gen-a package is now a stale sibling
    ok_a, problems_a = pkg.verify_science_contract_package(
        ctx["package_dir"], evidence_root=ev_root)
    assert not ok_a and any("stale" in p for p in problems_a)


def test_pointer_not_written_outside_root(tmp_path):
    """Packages outside the evidence root get no pointer write."""
    ctx, ev_root = _evidence_ctx(tmp_path)
    pkg.build_science_contract_package(**ctx)  # pkg at tmp_path/pkg
    assert not (ev_root / "active_generation.json").exists()


def test_scaffold_binds_generation_and_code(tmp_path):
    """T2S-06: the packaged scaffold carries generation/code identity
    and strict-verifies file-bound inside the package."""
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    sc = json.loads(
        (ctx["package_dir"] / "validation_scaffold.json").read_text())
    assert sc["candidate_generation_id"] == \
        ctx["candidate_generation_id"]
    assert sc["code_revision"] == ctx["code_revision"]
    assert sc["binding_status"] == "FILE_BOUND"


def test_mec_carries_row_serialization_and_identity(tmp_path):
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    mec = json.loads(
        (ctx["package_dir"] / "mec_schema.json").read_text())
    assert mec["row_serialization"] == "canonical-json-v1"
    assert mec["candidate_generation_id"] == \
        ctx["candidate_generation_id"]
    assert mec["code_revision"] == ctx["code_revision"]


def test_fmx_blocked_anti_confusion_fields(tmp_path):
    """PKG-08: the blocked FMX doc is visibly a schema fixture."""
    ctx = _context(tmp_path)
    pkg.build_science_contract_package(**ctx)
    fmx = json.loads(
        (ctx["package_dir"] / "fmx_blocked.json").read_text())
    assert fmx["schema_fixture"] is True
    assert fmx["external_freeze"] is False
    assert fmx["artifact_present"] is False
    assert fmx["not_a_real_matrix"] is True


# ---------- N5 audit-gap closure (PKG-10..13, HANDOFF-01) ----------


def _edit_doc(pkg_dir, name, mutate):
    """Apply an honest content edit to a package doc and rebind its
    envelope self-hash so the change is internally consistent."""
    from nepal.framework_v1.provenance import bind_artifact_envelope
    path = Path(pkg_dir) / name
    doc = json.loads(path.read_text())
    mutate(doc)
    doc.pop("artifact_sha256", None)
    doc = bind_artifact_envelope(doc)
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return doc


def _resign_package(pkg_dir):
    """Re-hash the index/checkpoint/seal chain after honest doc edits so
    verification isolates the targeted check rather than the hash
    chain."""
    from nepal.framework_v1.provenance import sha256_file
    pkg_dir = Path(pkg_dir)
    idx_path = pkg_dir / "science_contract_package_index.json"
    idx = json.loads(idx_path.read_text())
    for entry in idx["files"]:
        f = pkg_dir / entry["relative_path"]
        entry["sha256"] = sha256_file(f)
        entry["bytes"] = f.stat().st_size
    idx_path.write_text(json.dumps(idx, indent=1, sort_keys=True) + "\n")
    _edit_doc(pkg_dir, "science_contract_package_index.json",
              lambda d: None)
    _edit_doc(pkg_dir, "checkpoint.json",
              lambda d: d.__setitem__("index_file_sha256",
                                      sha256_file(idx_path)))
    _edit_doc(pkg_dir, "package_seal.json",
              lambda d: d.__setitem__(
                  "files",
                  {f.name: sha256_file(f) for f in sorted(
                      pkg_dir.iterdir())
                   if f.is_file() and not f.is_symlink()
                   and f.name != "package_seal.json"}))


def _edit_pointer(ev_root, mutate):
    """Rewrite the active-generation pointer with a valid self-hash."""
    from nepal.framework_v1.provenance import bind_artifact_envelope
    path = Path(ev_root) / "active_generation.json"
    doc = json.loads(path.read_text())
    mutate(doc)
    doc.pop("artifact_sha256", None)
    doc = bind_artifact_envelope(doc)
    path.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
    return doc


def _write_b_evidence(ev_root, ranked_sha, name="b_evidence.json",
                      **extra):
    """Write a well-formed bound B evidence envelope under a root."""
    from nepal.framework_v1.provenance import (bind_artifact_envelope,
                                               write_deterministic_json)
    doc = {"doc_type": "B_STAGE_EVIDENCE_V1",
           "ranked_array_canonical_sha256": ranked_sha,
           "status": "B_TO_C_BLOCKED",
           "research_diagnostic_only": True,
           "promotion_eligible": False,
           "production_authorized": False,
           "warning_path_authorized": False}
    doc.update(extra)
    write_deterministic_json(Path(ev_root) / name,
                             bind_artifact_envelope(doc))
    return Path(ev_root) / name


class TestMandatoryActivePointer:
    """PKG-10: a package resolving under an evidence root must be the
    generation named by a verified active_generation.json pointer."""

    def _pkg_under_root(self, tmp_path):
        ctx, ev_root = _evidence_ctx(tmp_path)
        ctx["package_dir"] = ev_root / "gen-a" / "package"
        pkg.build_science_contract_package(**ctx)
        assert (ev_root / "active_generation.json").is_file()
        return ctx, ev_root

    def test_missing_pointer_fails(self, tmp_path):
        ctx, ev_root = self._pkg_under_root(tmp_path)
        (ev_root / "active_generation.json").unlink()
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"], evidence_root=ev_root)
        assert not ok
        assert any("pointer" in p for p in problems)

    def test_pointer_wrong_relpath_fails(self, tmp_path):
        ctx, ev_root = self._pkg_under_root(tmp_path)
        _edit_pointer(ev_root, lambda d: d.__setitem__(
            "package_relpath", "gen-b/package"))
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"], evidence_root=ev_root)
        assert not ok and any("stale" in p for p in problems)

    def test_pointer_wrong_generation_fails(self, tmp_path):
        ctx, ev_root = self._pkg_under_root(tmp_path)
        _edit_pointer(ev_root, lambda d: d.__setitem__(
            "candidate_generation_id", "gen-other-9"))
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"], evidence_root=ev_root)
        assert not ok and any("pointer" in p for p in problems)

    def test_pointer_wrong_code_revision_fails(self, tmp_path):
        ctx, ev_root = self._pkg_under_root(tmp_path)
        _edit_pointer(ev_root, lambda d: d.__setitem__(
            "code_revision", "0" * 40))
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"], evidence_root=ev_root)
        assert not ok and any("pointer" in p for p in problems)

    def test_pointer_wrong_run_root_name_fails(self, tmp_path):
        ctx, ev_root = self._pkg_under_root(tmp_path)
        _edit_pointer(ev_root, lambda d: d.__setitem__(
            "run_root_name", "other-root"))
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"], evidence_root=ev_root)
        assert not ok and any("pointer" in p for p in problems)

    def test_pointer_wrong_seal_digest_fails(self, tmp_path):
        ctx, ev_root = self._pkg_under_root(tmp_path)
        _edit_pointer(ev_root, lambda d: d.__setitem__(
            "package_seal_sha256", "00" * 32))
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"], evidence_root=ev_root)
        assert not ok and any("pointer" in p for p in problems)

    def test_non_envelope_pointer_fails(self, tmp_path):
        ctx, ev_root = self._pkg_under_root(tmp_path)
        (ev_root / "active_generation.json").write_text(json.dumps({
            "pointer_type": "ACTIVE_GENERATION_POINTER_V1",
            "package_relpath": "gen-a/package"}))
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"], evidence_root=ev_root)
        assert not ok and any("pointer" in p for p in problems)

    def test_pointer_field_dropped_fails(self, tmp_path):
        ctx, ev_root = self._pkg_under_root(tmp_path)
        _edit_pointer(ev_root, lambda d: d.pop("index_file_sha256"))
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"], evidence_root=ev_root)
        assert not ok and any("pointer" in p for p in problems)


class TestBEvidenceBinding:
    """PKG-11: file-bound B evidence vs the UNBOUND_INFORMATIONAL
    marker."""

    def test_bound_b_evidence_round_trip(self, tmp_path):
        ctx, ev_root = _evidence_ctx(tmp_path)
        _write_b_evidence(ev_root, ctx["ranked_array_canonical_sha256"])
        ctx["b_evidence_relative_path"] = "b_evidence.json"
        pkg.build_science_contract_package(**ctx)
        rc = json.loads(
            (ctx["package_dir"] / "run_context.json").read_text())
        assert rc["b_evidence_binding"] == "FILE_BOUND"
        assert rc["b_evidence"]["relative_path"] == "b_evidence.json"
        assert len(rc["b_evidence"]["sha256"]) == 64
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"], evidence_root=ev_root)
        assert ok, problems

    def test_unbound_marker_written(self, tmp_path):
        ctx = _context(tmp_path)
        del ctx["b_evidence_relative_path"]
        pkg.build_science_contract_package(**ctx)
        rc = json.loads(
            (ctx["package_dir"] / "run_context.json").read_text())
        assert rc["b_evidence_binding"] == "UNBOUND_INFORMATIONAL"
        assert "b_evidence" not in rc

    def test_unbound_marker_required_at_verify(self, tmp_path):
        """Ranked digests without a bound file require the exact
        UNBOUND_INFORMATIONAL marker."""
        ctx = _context(tmp_path)
        del ctx["b_evidence_relative_path"]
        pkg.build_science_contract_package(**ctx)
        _edit_doc(ctx["package_dir"], "run_context.json",
                  lambda d: d.pop("b_evidence_binding"))
        _resign_package(ctx["package_dir"])
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"])
        assert not ok and any("UNBOUND_INFORMATIONAL" in p
                              for p in problems)

    def test_fake_bound_evidence_without_file_fails(self, tmp_path):
        """A declared b_evidence binding with no resolvable file fails
        closed."""
        ctx = _context(tmp_path)
        pkg.build_science_contract_package(**ctx)

        def forge(d):
            d["b_evidence"] = {"relative_path": "b_evidence.json",
                               "sha256": "ab" * 32}
            d["b_evidence_binding"] = "FILE_BOUND"
        _edit_doc(ctx["package_dir"], "run_context.json", forge)
        _resign_package(ctx["package_dir"])
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"])
        assert not ok and any("b_evidence" in p or "evidence_root" in p
                              for p in problems)

    def test_bound_b_evidence_missing_file_fails(self, tmp_path):
        ctx, ev_root = _evidence_ctx(tmp_path)
        target = _write_b_evidence(
            ev_root, ctx["ranked_array_canonical_sha256"])
        ctx["b_evidence_relative_path"] = "b_evidence.json"
        pkg.build_science_contract_package(**ctx)
        target.unlink()
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"], evidence_root=ev_root)
        assert not ok and any("b_evidence" in p for p in problems)

    def test_bound_b_evidence_tampered_file_fails(self, tmp_path):
        ctx, ev_root = _evidence_ctx(tmp_path)
        _write_b_evidence(ev_root, ctx["ranked_array_canonical_sha256"])
        ctx["b_evidence_relative_path"] = "b_evidence.json"
        pkg.build_science_contract_package(**ctx)
        _write_b_evidence(ev_root, "00" * 32)
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"], evidence_root=ev_root)
        assert not ok and any("b_evidence" in p for p in problems)

    def test_verify_gate_passed_consistency(self, tmp_path):
        """A bound B doc claiming gate_passed while b_status is blocked
        is rejected at verify."""
        ctx, ev_root = _evidence_ctx(tmp_path)
        _write_b_evidence(ev_root, ctx["ranked_array_canonical_sha256"])
        ctx["b_evidence_relative_path"] = "b_evidence.json"
        pkg.build_science_contract_package(**ctx)
        target = _write_b_evidence(
            ev_root, ctx["ranked_array_canonical_sha256"],
            gate_passed=True)
        from nepal.framework_v1.provenance import sha256_file
        new_sha = sha256_file(target)
        _edit_doc(ctx["package_dir"], "run_context.json",
                  lambda d: d["b_evidence"].__setitem__("sha256",
                                                        new_sha))
        _resign_package(ctx["package_dir"])
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"], evidence_root=ev_root)
        assert not ok and any("gate_passed" in p for p in problems)

    def test_build_rejects_b_evidence_without_root(self, tmp_path):
        ctx = _context(tmp_path)
        ctx["b_evidence_relative_path"] = "b_evidence.json"
        with pytest.raises(ValueError):
            pkg.build_science_contract_package(**ctx)

    def test_build_rejects_b_evidence_traversal(self, tmp_path):
        ctx, ev_root = _evidence_ctx(tmp_path)
        ctx["b_evidence_relative_path"] = "../escape.json"
        with pytest.raises(ValueError):
            pkg.build_science_contract_package(**ctx)

    def test_build_rejects_b_evidence_symlink(self, tmp_path):
        ctx, ev_root = _evidence_ctx(tmp_path)
        target = _write_b_evidence(
            ev_root, ctx["ranked_array_canonical_sha256"],
            name="real_b.json")
        (ev_root / "link_b.json").symlink_to(target)
        ctx["b_evidence_relative_path"] = "link_b.json"
        with pytest.raises(ValueError):
            pkg.build_science_contract_package(**ctx)

    def test_build_rejects_b_evidence_non_envelope(self, tmp_path):
        ctx, ev_root = _evidence_ctx(tmp_path)
        (ev_root / "b_evidence.json").write_text('{"x": 1}')
        ctx["b_evidence_relative_path"] = "b_evidence.json"
        with pytest.raises(ValueError):
            pkg.build_science_contract_package(**ctx)

    def test_build_rejects_b_evidence_missing_ranked_digest(
            self, tmp_path):
        ctx, ev_root = _evidence_ctx(tmp_path)
        _write_b_evidence(ev_root, "00" * 32)  # wrong digest carried
        ctx["b_evidence_relative_path"] = "b_evidence.json"
        with pytest.raises(ValueError):
            pkg.build_science_contract_package(**ctx)

    def test_build_rejects_gate_passed_while_blocked(self, tmp_path):
        ctx, ev_root = _evidence_ctx(tmp_path)
        _write_b_evidence(ev_root, ctx["ranked_array_canonical_sha256"],
                          gate_passed=True)
        ctx["b_evidence_relative_path"] = "b_evidence.json"
        with pytest.raises(ValueError):
            pkg.build_science_contract_package(**ctx)


class TestIdentityFormat:
    """PKG-12: candidate_generation_id / code_revision formats."""

    @pytest.mark.parametrize("bad", ["", "   ", "x", "ab", "bad id!",
                                     "-leading", ".leading",
                                     "a" * 129])
    def test_build_rejects_bad_generation_id(self, tmp_path, bad):
        ctx = _context(tmp_path)
        ctx["candidate_generation_id"] = bad
        with pytest.raises(ValueError):
            pkg.build_science_contract_package(**ctx)

    @pytest.mark.parametrize("bad", ["", "   ", "xyz", "ABCDEF0",
                                     "0" * 6, "g" * 40, "z" * 65])
    def test_build_rejects_bad_code_revision(self, tmp_path, bad):
        ctx = _context(tmp_path)
        ctx["code_revision"] = bad
        with pytest.raises(ValueError):
            pkg.build_science_contract_package(**ctx)

    def test_verify_flags_malformed_doc_identity(self, tmp_path):
        ctx = _context(tmp_path)
        pkg.build_science_contract_package(**ctx)
        _edit_doc(ctx["package_dir"], "research_contract_handoff.json",
                  lambda d: d.__setitem__("code_revision", "not-hex"))
        _resign_package(ctx["package_dir"])
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"])
        assert not ok and any("code_revision" in p for p in problems)


class TestCrossDocumentConsistency:
    """PKG-13: run_context.json is canonical for the identity fields."""

    def test_handoff_generation_drift_rejected(self, tmp_path):
        ctx = _context(tmp_path)
        pkg.build_science_contract_package(**ctx)
        _edit_doc(ctx["package_dir"], "research_contract_handoff.json",
                  lambda d: d.__setitem__("candidate_generation_id",
                                          "gen-drift-1"))
        _resign_package(ctx["package_dir"])
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"])
        assert not ok
        assert any("research_contract_handoff.json" in p and
                   "candidate_generation_id" in p for p in problems)

    def test_index_code_revision_drift_rejected(self, tmp_path):
        ctx = _context(tmp_path)
        pkg.build_science_contract_package(**ctx)
        _edit_doc(ctx["package_dir"],
                  "science_contract_package_index.json",
                  lambda d: d.__setitem__("code_revision", "1" * 40))
        _resign_package(ctx["package_dir"])
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"])
        assert not ok and any("code_revision" in p for p in problems)

    def test_seal_package_status_drift_rejected(self, tmp_path):
        ctx = _context(tmp_path)
        pkg.build_science_contract_package(**ctx)
        _edit_doc(ctx["package_dir"], "package_seal.json",
                  lambda d: d.__setitem__("package_status",
                                          "SOME_OTHER_STATUS"))
        _resign_package(ctx["package_dir"])
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"])
        assert not ok and any("package_status" in p for p in problems)

    def test_nested_b_status_drift_rejected(self, tmp_path):
        ctx = _context(tmp_path)
        pkg.build_science_contract_package(**ctx)
        _edit_doc(ctx["package_dir"], "research_contract_handoff.json",
                  lambda d: d["inherited_state"].__setitem__(
                      "b_status", "B_TO_C_PASSED_THROUGH"))
        _resign_package(ctx["package_dir"])
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"])
        assert not ok and any("b_status" in p for p in problems)


class TestResidualRegister:
    """HANDOFF-01: the handoff embeds a residual-positive register."""

    def test_residual_register_embedded_verbatim(self, tmp_path):
        ctx = _context(tmp_path)
        reg = [{"id": "X1", "status": "OPEN", "note": "merge gate"},
               {"id": "X2", "status": "DEFERRED"}]
        ctx["residual_register"] = reg
        pkg.build_science_contract_package(**ctx)
        ho = json.loads(
            (ctx["package_dir"] / "research_contract_handoff.json")
            .read_text())
        assert ho["residual_register"] == reg
        from nepal.framework_v1.provenance import sha256_canonical
        assert ho["residual_register_sha256"] == sha256_canonical(reg)
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"], evidence_root=ctx["evidence_root"])
        assert ok, problems

    def test_default_register_is_residual_positive(self, tmp_path):
        ctx = _context(tmp_path)
        pkg.build_science_contract_package(**ctx)
        ho = json.loads(
            (ctx["package_dir"] / "research_contract_handoff.json")
            .read_text())
        assert ho["residual_register"]
        assert any(e["status"] != "CLOSED"
                   for e in ho["residual_register"])

    def test_build_rejects_empty_register(self, tmp_path):
        ctx = _context(tmp_path)
        ctx["residual_register"] = []
        with pytest.raises(ValueError):
            pkg.build_science_contract_package(**ctx)

    def test_build_rejects_all_closed_register(self, tmp_path):
        ctx = _context(tmp_path)
        ctx["residual_register"] = [{"id": "X1", "status": "CLOSED"}]
        with pytest.raises(ValueError):
            pkg.build_science_contract_package(**ctx)

    def test_verify_rejects_tampered_register_sha(self, tmp_path):
        ctx = _context(tmp_path)
        pkg.build_science_contract_package(**ctx)
        _edit_doc(ctx["package_dir"], "research_contract_handoff.json",
                  lambda d: d["residual_register"].append(
                      {"id": "FORGED", "status": "OPEN"}))
        _resign_package(ctx["package_dir"])
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"])
        assert not ok and any("residual_register_sha256" in p
                              for p in problems)

    def test_verify_rejects_all_closed_register(self, tmp_path):
        ctx = _context(tmp_path)
        pkg.build_science_contract_package(**ctx)

        def close_all(d):
            for e in d["residual_register"]:
                e["status"] = "CLOSED"
            from nepal.framework_v1.provenance import sha256_canonical
            d["residual_register_sha256"] = sha256_canonical(
                d["residual_register"])
        _edit_doc(ctx["package_dir"], "research_contract_handoff.json",
                  close_all)
        _resign_package(ctx["package_dir"])
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"])
        assert not ok and any("residual" in p for p in problems)

    def test_verify_rejects_empty_register(self, tmp_path):
        ctx = _context(tmp_path)
        pkg.build_science_contract_package(**ctx)

        def empty(d):
            from nepal.framework_v1.provenance import sha256_canonical
            d["residual_register"] = []
            d["residual_register_sha256"] = sha256_canonical([])
        _edit_doc(ctx["package_dir"], "research_contract_handoff.json",
                  empty)
        _resign_package(ctx["package_dir"])
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"])
        assert not ok and any("residual_register" in p
                              for p in problems)

    def test_verify_rejects_gap_closure_claim(self, tmp_path):
        ctx = _context(tmp_path)
        pkg.build_science_contract_package(**ctx)
        _edit_doc(ctx["package_dir"], "research_no_claims.json",
                  lambda d: d["claims"].append("All gaps closed."))
        _resign_package(ctx["package_dir"])
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"])
        assert not ok and any("gaps" in p for p in problems)

    def test_verify_rejects_no_remaining_gaps_claim(self, tmp_path):
        ctx = _context(tmp_path)
        pkg.build_science_contract_package(**ctx)
        _edit_doc(ctx["package_dir"], "research_contract_handoff.json",
                  lambda d: d.__setitem__("note",
                                          "no-remaining-gaps"))
        _resign_package(ctx["package_dir"])
        ok, problems = pkg.verify_science_contract_package(
            ctx["package_dir"])
        assert not ok and any("gaps" in p for p in problems)


# ---------- N5 advisor-follow-up bindings ----------


def test_residual_register_source_bound(tmp_path):
    """HANDOFF-01b: register bound to a canonical file under the root —
    content equality enforced, tamper rejects."""
    ctx = _context(tmp_path)
    ev_root = ctx["evidence_root"]
    reg = [{"id": "FMX-01", "status": "BLOCKED"},
           {"id": "R04", "status": "OPEN"}]
    (ev_root / "residual-register-v1.json").write_text(json.dumps(reg))
    ctx["residual_register_relpath"] = "residual-register-v1.json"
    pkg.build_science_contract_package(**ctx)
    ho = json.loads(
        (ctx["package_dir"] / "research_contract_handoff.json")
        .read_text())
    assert ho["residual_register"] == reg
    assert ho["residual_register_source"]["relative_path"] == \
        "residual-register-v1.json"
    ok, problems = pkg.verify_science_contract_package(
        ctx["package_dir"], evidence_root=ev_root)
    assert ok, problems
    # tamper the source file
    (ev_root / "residual-register-v1.json").write_text(
        json.dumps(reg + [{"id": "X", "status": "OPEN"}]))
    ok, problems = pkg.verify_science_contract_package(
        ctx["package_dir"], evidence_root=ev_root)
    assert not ok and any("residual_register_source" in p
                          for p in problems)


def test_register_source_and_inline_register_conflict(tmp_path):
    ctx = _context(tmp_path)
    ctx["residual_register"] = [{"id": "X", "status": "OPEN"}]
    ctx["residual_register_relpath"] = "whatever.json"
    with pytest.raises(ValueError):
        pkg.build_science_contract_package(**ctx)


def test_strict_scaffold_rejects_informational_b(tmp_path):
    """Advisor catch: a bound B doc carrying UNBOUND_INFORMATIONAL
    digests must not satisfy a strict scaffold."""
    from nepal.framework_v1 import validation_scaffold as vs
    from nepal.framework_v1.provenance import (bind_artifact_envelope,
                                               sha256_file)
    root = tmp_path / "root"
    root.mkdir()
    b_doc = bind_artifact_envelope({
        "envelope_type": "B_SCREEN_V1",
        "b_status": "B_TO_C_BLOCKED",
        "ranked_array_canonical_sha256": _RANKED_DIGEST,
        "b_evidence_binding": "UNBOUND_INFORMATIONAL",
        "candidate_generation_id": "gen-x",
        "code_revision": "abcdef1"})
    (root / "b.json").write_text(json.dumps(b_doc))
    mec_doc = bind_artifact_envelope({
        "envelope_type": "MULTI_EVENT_CONTRACT_V1",
        "candidate_generation_id": "gen-x",
        "code_revision": "abcdef1"})
    (root / "mec.json").write_text(json.dumps(mec_doc))
    fmx_doc = bind_artifact_envelope({
        "envelope_type": "FMX_V1",
        "fmx_status": "FMX_BLOCKED_PENDING_EXPLICIT_FREEZE",
        "candidate_generation_id": "gen-x",
        "code_revision": "abcdef1"})
    (root / "fmx.json").write_text(json.dumps(fmx_doc))
    metrics = [{"metric_id": "m1", "class": "calibration"}]
    from nepal.framework_v1.provenance import sha256_canonical
    env = vs.build_scaffold_envelope({
        "mode": "VALIDATION_SCAFFOLD_ONLY",
        "research_diagnostic_only": True,
        "promotion_eligible": False,
        "candidate_generation_id": "gen-x",
        "code_revision": "abcdef1",
        "references": {
            "mec_reference": {
                "envelope_sha256": mec_doc["artifact_sha256"],
                "envelope_type": "MULTI_EVENT_CONTRACT_V1",
                "relative_path": "mec.json"},
            "fmx_reference": {
                "envelope_sha256": fmx_doc["artifact_sha256"],
                "fmx_status": "FMX_BLOCKED_PENDING_EXPLICIT_FREEZE",
                "relative_path": "fmx.json"},
            "b_reference": {
                "status": "B_TO_C_BLOCKED",
                "ranked_array_canonical_sha256": _RANKED_DIGEST,
                "envelope_sha256": b_doc["artifact_sha256"],
                "relative_path": "b.json"}},
        "split_spec": {"split_id": "S1",
                       "temporal_embargo_days": 30,
                       "geographic_holdout": {"min_separation_km": 50.0},
                       "event_separation": {"group_disjoint": True}},
        "metric_registry": metrics,
        "metric_registry_sha256": sha256_canonical(metrics),
        "inherited_state": {"b_status": "B_TO_C_BLOCKED",
                            "e_status": "E_BLOCKED",
                            "f_status": "F_BLOCKED",
                            "ranking_rerun": False}},
        reference_root=root)
    ok, problems = vs.verify_scaffold_envelope(
        env, reference_root=root, strict=True)
    assert not ok and any("informational" in p.lower()
                          for p in problems)
