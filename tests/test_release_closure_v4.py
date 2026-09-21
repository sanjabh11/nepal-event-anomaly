"""V4 release-closure unit tests on fully synthetic fixtures.

Every fixture lives under ``tmp_path`` — the governed evidence roots are
never touched.  ``validate_index``/``validate_surface``/``_head``/
``_environment`` are monkeypatched at the module level so the tests are
fast and free of git/network side effects; the fail-closed construction
logic in ``build_closure`` runs for real.
"""
from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import build_p5_release_closure_v4 as closure_mod  # noqa: E402
import build_p5_suite_receipt as receipt_mod  # noqa: E402
from build_p5_release_closure_v4 import ClosureError  # noqa: E402

_LIVE_HEAD = "a" * 40
_CONTENT_HEAD = "b" * 40
_INDEX_REL = "retrieval/p5_evidence_index_v2.json"
_OUT_REL = "retrieval/p5_release_closure_v4.json"
_OWNER_REL = "retrieval/p5_d_owner_disposition_v2.json"
_RECEIPT_REL = "retrieval/p5_suite_receipt_v2.json"
_DAILY_REPORT_REL = "retrieval/p5_replay_report_v5.json"
_SEASONAL_REPORT_REL = "run/seasonal_replay_report_v5.json"


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write_json(path: Path, doc: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8")
    return path


def _write_sidecar(path: Path) -> None:
    digest = _sha(path.read_bytes())
    Path(str(path) + ".sha256").write_text(
        f"{digest}  {path.name}\n", encoding="utf-8")


def _receipt_doc(**overrides) -> dict:
    doc = {
        "schema": "P5_SUITE_RECEIPT_V2",
        "activity_id": "f" * 32,
        "command_digest": "e" * 64,
        "repository_head": _LIVE_HEAD,
        "counts": {"collected": 4, "passed": 3, "skipped": 1,
                   "failed": 0, "errors": 0, "warnings": 2},
        "exit_code": 0,
        "duration_s": 1.25,
        "started_utc": "2026-09-22T00:00:00Z",
        "completed_utc": "2026-09-22T00:00:01Z",
        "environment": {},
        "claim_scope": "research_only_no_operational_authorization",
    }
    doc.update(overrides)
    return doc


def _write_receipt(world: SimpleNamespace, **overrides) -> None:
    doc = _receipt_doc(**overrides)
    # A V2 receipt binds the manifest bytes; default to the live
    # manifest sha unless the test overrides it.
    doc.setdefault("manifest_sha256", _sha(world.manifest.read_bytes()))
    _write_json(world.receipt, doc)
    _write_sidecar(world.receipt)


@pytest.fixture
def world(tmp_path, monkeypatch) -> SimpleNamespace:
    daily = tmp_path / "daily"
    seasonal = tmp_path / "seasonal"
    repo = tmp_path / "repo"
    audit = tmp_path / "audit"

    manifest = _write_json(repo / "manifest.json", {
        "content_head": _CONTENT_HEAD,
        "manifest_commit": _CONTENT_HEAD,
        "files": []})

    index = _write_json(daily / _INDEX_REL, {
        "schema": "P5_EVIDENCE_INDEX_V2",
        "title": "synthetic exhaustive index",
        "manifest": {"relpath": "manifest.json", "sha256": "0" * 64,
                     "content_head": _CONTENT_HEAD,
                     "manifest_commit": _CONTENT_HEAD},
        "roots": {
            "daily_p5a2": {"path": str(daily), "role": "daily"},
            "seasonal_v1_current": {"path": str(seasonal),
                                    "role": "seasonal"}},
        "files": [],
        "exclusions": [
            {"root_id": "daily_p5a2", "relpath": _INDEX_REL,
             "state": "present", "reason": "index output"},
            {"root_id": "daily_p5a2", "relpath": _OUT_REL,
             "state": "planned", "owner": "SWE-CLOSURE-V4",
             "reason": "v4 closure output slot"}]})

    owner = _write_json(daily / _OWNER_REL, {
        "schema": "P5_D_OWNER_DISPOSITION_V2",
        "approval_status": "NOT_REQUESTED",
        "approved_by": None,
        "approval_utc": None,
        "disposition": "RECOMMENDED-DEFAULT"})

    receipt = _write_json(daily / _RECEIPT_REL, _receipt_doc(
        manifest_sha256=_sha(manifest.read_bytes())))
    _write_sidecar(receipt)

    daily_report = _write_json(daily / _DAILY_REPORT_REL, {
        "status": "REPLAY_OK",
        "replay_scope": "artifact_integrity_replay"})
    seasonal_report = _write_json(seasonal / _SEASONAL_REPORT_REL, {
        "status": "REPLAY_OK",
        "replay_scope": "artifact_integrity_replay"})

    surface = _write_json(audit / "INCIDENT_SURFACE_V1.json", {
        "schema": "INCIDENT_SURFACE_V1",
        "main_release_excluded": True,
        "exclusion_reason": "audit artifacts",
        "generated_utc": "2026-09-22T00:00:00Z",
        "entries": []})

    monkeypatch.setattr(closure_mod, "REPO", repo)
    monkeypatch.setattr(
        closure_mod, "validate_index",
        lambda path: {"status": "INDEX_OK", "problems": []})
    monkeypatch.setattr(
        closure_mod, "validate_surface",
        lambda path: {"status": "INCIDENT_SURFACE_OK", "problems": [],
                      "artifact_count": 0})
    monkeypatch.setattr(closure_mod, "_head", lambda root: _LIVE_HEAD)
    monkeypatch.setattr(
        closure_mod, "_environment",
        lambda: {"python": "3.14.7", "packages": {},
                 "package_count": 0, "environment_digest": "0" * 64})

    return SimpleNamespace(
        daily=daily, seasonal=seasonal, repo=repo, audit=audit,
        manifest=manifest, index=index, owner=owner, receipt=receipt,
        daily_report=daily_report, seasonal_report=seasonal_report,
        surface=surface, out=daily / _OUT_REL)


def _kwargs(world: SimpleNamespace) -> dict:
    return dict(index=world.index, daily_root=world.daily,
                seasonal_root=world.seasonal,
                owner_disposition=world.owner,
                incident_surface=world.surface,
                suite_receipt=world.receipt, output=world.out)


def _cli_args(world: SimpleNamespace) -> list:
    return ["--index", str(world.index),
            "--daily-root", str(world.daily),
            "--seasonal-root", str(world.seasonal),
            "--owner-disposition", str(world.owner),
            "--incident-surface", str(world.surface),
            "--suite-receipt", str(world.receipt),
            "--out", str(world.out)]


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

def test_build_closure_assembles_v4_document(world):
    closure = closure_mod.build_closure(**_kwargs(world))
    assert closure["schema"] == "P5_RELEASE_CLOSURE_V4"
    assert closure["release_version"] == "v5"
    assert closure["claim_scope"] == (
        "research_only_no_operational_authorization")
    assert closure["started_utc"] <= closure["completed_utc"]
    assert len(closure["activity_id"]) == 32
    assert all(value is False
               for value in closure["authority"].values())


def test_release_version_is_recorded_from_argument(world):
    closure = closure_mod.build_closure(release_version="v9",
                                        **_kwargs(world))
    assert closure["release_version"] == "v9"


def test_bundle_section_records_terminal_state(world):
    closure = closure_mod.build_closure(**_kwargs(world))
    bundle = closure["bundle"]
    assert bundle["index_relpath"] == _INDEX_REL
    assert bundle["index_sha256"] == _sha(world.index.read_bytes())
    assert bundle["closure_relpath"] == _OUT_REL
    assert bundle["terminal_state"] == "CURRENT_TREE_RELEASE"


def test_repository_section_binds_the_live_tree(world):
    closure = closure_mod.build_closure(**_kwargs(world))
    repo = closure["repository"]
    assert repo["head"] == _LIVE_HEAD
    assert repo["content_head"] == _CONTENT_HEAD
    assert repo["manifest_commit"] == _CONTENT_HEAD
    assert repo["manifest_relpath"] == "manifest.json"
    assert repo["manifest_sha256"] == _sha(world.manifest.read_bytes())


def test_every_embedded_digest_is_a_real_sha256(world):
    closure = closure_mod.build_closure(**_kwargs(world))
    assert closure["root_of_trust"]["sha256"] == _sha(
        world.index.read_bytes())
    assert closure["suite"]["receipt_sha256"] == _sha(
        world.receipt.read_bytes())
    assert closure["incident_surface"]["sha256"] == _sha(
        world.surface.read_bytes())
    assert closure["owner_disposition"]["sha256"] == _sha(
        world.owner.read_bytes())
    assert closure["repository"]["manifest_sha256"] == _sha(
        world.manifest.read_bytes())
    assert closure["replays"]["daily"]["sha256"] == _sha(
        world.daily_report.read_bytes())
    assert closure["replays"]["seasonal"]["sha256"] == _sha(
        world.seasonal_report.read_bytes())


def test_root_of_trust_relpath_is_derived(world):
    closure = closure_mod.build_closure(**_kwargs(world))
    rot = closure["root_of_trust"]
    assert rot["root_id"] == "daily_p5a2"
    assert rot["relpath"] == _INDEX_REL
    assert rot["index_schema"] == "P5_EVIDENCE_INDEX_V2"
    assert rot["validator_status"] == "INDEX_OK"


def test_owner_gated_falls_back_to_static_list(world):
    closure = closure_mod.build_closure(**_kwargs(world))
    assert closure["owner_gated"] == [
        "option3", "obspy_admission", "arm_c", "publication"]


def test_owner_gated_read_from_owner_doc_when_present(world):
    doc = json.loads(world.owner.read_text(encoding="utf-8"))
    doc["owner_gated"] = ["option3", "publication"]
    _write_json(world.owner, doc)
    closure = closure_mod.build_closure(**_kwargs(world))
    assert closure["owner_gated"] == ["option3", "publication"]


# ---------------------------------------------------------------------------
# A3-01/A3-02: path/SHA binding and exclusive-create
# ---------------------------------------------------------------------------

def test_missing_index_fails_closed(world):
    world.index.unlink()
    with pytest.raises(ClosureError, match="index missing"):
        closure_mod.build_closure(**_kwargs(world))


def test_existing_output_is_fatal(world):
    world.out.write_text("historical\n", encoding="utf-8")
    with pytest.raises(ClosureError, match="exclusive-create"):
        closure_mod.build_closure(**_kwargs(world))
    assert world.out.read_text() == "historical\n"


def test_root_of_trust_path_sha_mismatch_fails(world, monkeypatch):
    real_sha = closure_mod._sha
    calls = []

    def flaky(path):
        calls.append(str(path))
        if len(calls) == 2:
            return "f" * 64
        return real_sha(path)

    monkeypatch.setattr(closure_mod, "_sha", flaky)
    with pytest.raises(ClosureError, match="path/SHA mismatch"):
        closure_mod.build_closure(**_kwargs(world))


def test_non_index_ok_validation_fails(world, monkeypatch):
    monkeypatch.setattr(
        closure_mod, "validate_index",
        lambda path: {"status": "INDEX_FAIL",
                      "problems": ["stale digest"]})
    with pytest.raises(ClosureError, match="index validation failed"):
        closure_mod.build_closure(**_kwargs(world))


def test_output_must_be_a_planned_exclusion(world):
    kwargs = _kwargs(world)
    kwargs["output"] = world.daily / "retrieval" / "other_slot.json"
    with pytest.raises(ClosureError, match="planned exclusion"):
        closure_mod.build_closure(**kwargs)


# ---------------------------------------------------------------------------
# A3-03/A3-04: suite receipt binding
# ---------------------------------------------------------------------------

def test_missing_receipt_fails_closed(world):
    world.receipt.unlink()
    Path(str(world.receipt) + ".sha256").unlink()
    with pytest.raises((ClosureError, OSError)):
        closure_mod.build_closure(**_kwargs(world))


def test_receipt_without_sidecar_fails(world):
    Path(str(world.receipt) + ".sha256").unlink()
    with pytest.raises(ClosureError, match="sidecar missing"):
        closure_mod.build_closure(**_kwargs(world))


def test_receipt_sidecar_digest_mismatch_fails(world):
    Path(str(world.receipt) + ".sha256").write_text(
        "0" * 64 + f"  {world.receipt.name}\n", encoding="utf-8")
    with pytest.raises(ClosureError, match="sidecar digest mismatch"):
        closure_mod.build_closure(**_kwargs(world))


def test_receipt_wrong_schema_fails(world):
    _write_receipt(world, schema="P5_SUITE_RECEIPT_V1")
    with pytest.raises(ClosureError, match="P5_SUITE_RECEIPT_V2"):
        closure_mod.build_closure(**_kwargs(world))


def test_receipt_missing_manifest_sha256_fails(world):
    """A V2 receipt must bind the manifest bytes; a missing field is
    fatal, not skipped."""
    doc = _receipt_doc()  # no manifest_sha256 key at all
    _write_json(world.receipt, doc)
    _write_sidecar(world.receipt)
    with pytest.raises(ClosureError, match="manifest_sha256"):
        closure_mod.build_closure(**_kwargs(world))


def test_receipt_manifest_sha256_mismatch_fails(world):
    _write_receipt(world, manifest_sha256="d" * 64)
    with pytest.raises(ClosureError, match="manifest_sha256"):
        closure_mod.build_closure(**_kwargs(world))


def test_receipt_nonzero_exit_code_fails(world):
    _write_receipt(world, exit_code=1)
    with pytest.raises(ClosureError, match="exit_code"):
        closure_mod.build_closure(**_kwargs(world))


def test_receipt_failure_counts_fail(world):
    counts = {"collected": 5, "passed": 3, "skipped": 1, "failed": 1,
              "errors": 0, "warnings": 0}
    _write_receipt(world, counts=counts)
    with pytest.raises(ClosureError, match="zero failures"):
        closure_mod.build_closure(**_kwargs(world))


def test_receipt_count_inconsistency_fails(world):
    counts = {"collected": 99, "passed": 3, "skipped": 1, "failed": 0,
              "errors": 0, "warnings": 0}
    _write_receipt(world, counts=counts)
    with pytest.raises(ClosureError, match="collected"):
        closure_mod.build_closure(**_kwargs(world))


def test_receipt_head_may_match_manifest_content_head(world):
    """A manifest-only rebind is content-inert: the receipt head may be
    the recorded content_head rather than live HEAD."""
    _write_receipt(world, repository_head=_CONTENT_HEAD)
    closure = closure_mod.build_closure(**_kwargs(world))
    assert closure["repository"]["head"] == _LIVE_HEAD
    assert closure["repository"]["content_head"] == _CONTENT_HEAD


def test_receipt_head_matching_neither_fails(world):
    _write_receipt(world, repository_head="c" * 40)
    with pytest.raises(ClosureError, match="repository_head"):
        closure_mod.build_closure(**_kwargs(world))


# ---------------------------------------------------------------------------
# Owner approval surface (G-05)
# ---------------------------------------------------------------------------

def _rewrite_owner(world: SimpleNamespace, **overrides) -> None:
    doc = json.loads(world.owner.read_text(encoding="utf-8"))
    doc.update(overrides)
    _write_json(world.owner, doc)


def test_owner_approved_without_identity_fails(world):
    _rewrite_owner(world, approval_status="APPROVED")
    with pytest.raises(ClosureError, match="APPROVED requires"):
        closure_mod.build_closure(**_kwargs(world))


def test_owner_identity_without_approved_status_fails(world):
    _rewrite_owner(world, approved_by="owner@example",
                   approval_utc="2026-09-22T00:00:00Z")
    with pytest.raises(ClosureError, match="not APPROVED"):
        closure_mod.build_closure(**_kwargs(world))


def test_owner_approved_with_identity_and_timestamp_passes(world):
    _rewrite_owner(world, approval_status="APPROVED",
                   approved_by="owner@example",
                   approval_utc="2026-09-22T00:00:00Z")
    closure = closure_mod.build_closure(**_kwargs(world))
    assert closure["owner_disposition"]["option3_approved_by"] == (
        "owner@example")
    assert closure["owner_disposition"]["option3_approval_status"] == (
        "APPROVED")


def test_owner_wrong_schema_fails(world):
    _rewrite_owner(world, schema="P5_D_OWNER_DISPOSITION_V1")
    with pytest.raises(ClosureError, match="P5_D_OWNER_DISPOSITION_V2"):
        closure_mod.build_closure(**_kwargs(world))


# ---------------------------------------------------------------------------
# Replay reports and incident surface
# ---------------------------------------------------------------------------

def test_missing_daily_replay_report_fails(world):
    world.daily_report.unlink()
    with pytest.raises(ClosureError, match="replay report missing"):
        closure_mod.build_closure(**_kwargs(world))


def test_replay_wrong_scope_fails(world):
    _write_json(world.daily_report, {
        "status": "REPLAY_OK", "replay_scope": "full_pipeline_replay"})
    with pytest.raises(ClosureError, match="artifact_integrity_replay"):
        closure_mod.build_closure(**_kwargs(world))


def test_replay_not_ok_fails(world):
    _write_json(world.seasonal_report, {
        "status": "REPLAY_FAIL",
        "replay_scope": "artifact_integrity_replay"})
    with pytest.raises(ClosureError, match="REPLAY_OK"):
        closure_mod.build_closure(**_kwargs(world))


def test_invalid_incident_surface_fails(world, monkeypatch):
    monkeypatch.setattr(
        closure_mod, "validate_surface",
        lambda path: {"status": "INCIDENT_SURFACE_FAIL",
                      "problems": ["digest mismatch"],
                      "artifact_count": 0})
    with pytest.raises(ClosureError, match="incident surface invalid"):
        closure_mod.build_closure(**_kwargs(world))


# ---------------------------------------------------------------------------
# CLI: dry-run writes nothing; --write is exclusive-create
# ---------------------------------------------------------------------------

def test_dry_run_writes_nothing(world, capsys):
    rc = closure_mod.main(_cli_args(world))
    assert rc == 0
    assert not world.out.exists()
    assert not Path(str(world.out) + ".sha256").exists()
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "CLOSURE_V4_DRY_RUN_OK"
    assert printed["closure"]["schema"] == "P5_RELEASE_CLOSURE_V4"


def test_cli_repo_root_and_release_version_flags(world, capsys):
    rc = closure_mod.main(_cli_args(world) + [
        "--repo-root", str(world.repo), "--release-version", "v6"])
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    closure = printed["closure"]
    assert closure["release_version"] == "v6"
    assert closure["repository"]["head"] == _LIVE_HEAD
    assert closure["repository"]["manifest_sha256"] == _sha(
        world.manifest.read_bytes())


def test_write_publishes_closure_and_sidecar(world, capsys):
    rc = closure_mod.main(_cli_args(world) + ["--write"])
    assert rc == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["status"] == "CLOSURE_V4_PUBLISHED"
    assert world.out.is_file()
    digest = _sha(world.out.read_bytes())
    assert printed["sha256"] == digest
    sidecar = Path(str(world.out) + ".sha256")
    assert sidecar.read_text().split()[0] == digest
    assert json.loads(world.out.read_text())["schema"] == (
        "P5_RELEASE_CLOSURE_V4")


def test_second_write_refuses_to_replace(world, capsys):
    assert closure_mod.main(_cli_args(world) + ["--write"]) == 0
    capsys.readouterr()
    before = world.out.read_bytes()
    rc = closure_mod.main(_cli_args(world) + ["--write"])
    assert rc == 1
    assert "RELEASE_CLOSURE_FAIL" in capsys.readouterr().out
    assert world.out.read_bytes() == before


# ---------------------------------------------------------------------------
# Suite-receipt summary parsing (pure functions; no real pytest runs)
# ---------------------------------------------------------------------------

def test_parse_summary_extracts_all_counts():
    stdout = ("....ss..xF\n"
              "===== 8 passed, 2 skipped, 1 failed, 1 error, "
              "3 warnings in 4.56s =====\n")
    counts = receipt_mod._parse_summary(stdout)
    assert counts == {"passed": 8, "skipped": 2, "failed": 1,
                      "errors": 1, "warnings": 3}


def test_parse_collected_from_collect_only_output():
    stdout = ("tests/test_a.py::test_one\n"
              "tests/test_a.py::test_two\n"
              "\n2 tests collected in 0.03s\n")
    assert receipt_mod._parse_collected(stdout) == 2


def test_parse_summary_fails_closed_without_tail():
    with pytest.raises(ClosureError, match="summary tail"):
        receipt_mod._parse_summary("no pytest output here\n")
