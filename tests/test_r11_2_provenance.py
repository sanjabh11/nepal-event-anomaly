"""Round-11.2 provenance-repair probes — the audit's live findings:

1. package/runner must reject fake/rehashed/fixture manifests and
   require config digest-equality;
2. EVIDENCE_VERIFIED posture must verify real sidecar bytes
   (leaf AND intermediate symlinks rejected);
3. non-verified sources never invoke run_regimes;
4. malformed manifest shapes are bounded ValueError and outside-root
   lexical aliases are rejected;
5. opportunities cross-bind source/units and require unique
   identities;
6. malformed engine outputs are RUN_ERROR, never uncaught;
7. opportunity digest is order-invariant.
"""
from __future__ import annotations

import dataclasses
import hashlib
import json
import types

import pytest

from nepal.research_v0.source_intake import (
    build_source_manifest, load_hmaglofdb_rows)
from nepal.research_v0._hashing import sha256_canonical
from nepal.science_v0.glof_poc import (
    build_hmaglofdb_event_package, run_glof_descriptive_poc)

from tests.test_glof_poc_contract import (
    _BASE_ROWS, _COLUMN_MAP, _EMBARGO, _EVAL_REGIONS,
    _GROUP_OF_BASIN, _SPLIT_OF_GROUP, _manifest, _opp,
    _opportunity_frame, _package, _pkg_manifest, _source_record,
    _source_rows, _write_csv, _write_sidecar,
    descriptive_regime)


class TestR112ManifestBinding:
    """Finding 1: fake/rehashed/fixture manifests never enter."""

    def test_fixture_manifest_rejected(self):
        fake = {"fixture": True}
        with pytest.raises(ValueError, match="seven"):
            _package(source_manifest=fake)

    def test_fixture_plus_real_fields_rejected(self):
        man = dict(_pkg_manifest(), fixture=True)
        with pytest.raises(ValueError):
            _package(source_manifest=man)

    def test_extra_field_manifest_rejected(self):
        man = dict(_pkg_manifest(), unexpected="x")
        with pytest.raises(ValueError):
            _package(source_manifest=man)

    def test_missing_key_manifest_rejected(self):
        man = {k: v for k, v in _pkg_manifest().items()
               if k != "units"}
        with pytest.raises(ValueError):
            _package(source_manifest=man)

    def test_fake_evidence_root_rejected(self):
        man = dict(_pkg_manifest(),
                   evidence_root="/nonexistent-r11-2")
        with pytest.raises(ValueError):
            _package(source_manifest=man)

    def test_wrong_bytes_manifest_rejected(self):
        man = dict(_pkg_manifest())
        man["source_files"] = [{"relpath": "events.csv",
                                "sha256": "b" * 64}]
        man["source_digests"] = ["b" * 64]
        with pytest.raises(ValueError):
            _package(source_manifest=man)

    def test_rehashed_fake_manifest_rejected(self):
        """A manifest that self-consistently names a file that does
        not exist still fails — bytes, not hashes, are the bound."""
        man = dict(_pkg_manifest(),
                   source_digests=["c" * 64],
                   source_files=[{"relpath": "ghost.csv",
                                  "sha256": "c" * 64}])
        with pytest.raises(ValueError):
            _package(source_manifest=man)

    def test_config_without_manifest_run_error(self):
        cfg = types.SimpleNamespace(source_manifest=None)
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, _package())
        assert receipt["status"] == "RUN_ERROR"
        assert any("source_manifest" in p
                   for p in receipt["problems"])

    def test_config_manifest_absent_attr_run_error(self):
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, object(), _package())
        assert receipt["status"] == "RUN_ERROR"


class TestR112LoaderShape:
    """Finding 4: malformed shapes -> bounded ValueError."""

    def test_source_files_none_valueerror(self, tmp_path):
        path = _write_csv(tmp_path, _BASE_ROWS)
        man = dict(_manifest(tmp_path, path), source_files=None)
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(
                path, source_manifest=man,
                column_map=dict(_COLUMN_MAP))

    def test_source_files_string_valueerror(self, tmp_path):
        path = _write_csv(tmp_path, _BASE_ROWS)
        man = dict(_manifest(tmp_path, path),
                   source_files="events.csv")
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(
                path, source_manifest=man,
                column_map=dict(_COLUMN_MAP))

    def test_outside_root_alias_rejected(self, tmp_path):
        """A symlink OUTSIDE the root resolving inside it is a
        lexical alias — rejected, not resolved (R11.2-4)."""
        root = tmp_path / "evidence"
        root.mkdir()
        inner = _write_csv(root, _BASE_ROWS)
        man = _manifest(root, inner)
        alias = tmp_path / "alias.csv"
        alias.symlink_to(inner)
        with pytest.raises(ValueError, match="inside"):
            load_hmaglofdb_rows(
                alias, source_manifest=man,
                column_map=dict(_COLUMN_MAP))


class TestR112SidecarGate:
    """Finding 2: EVIDENCE_VERIFIED requires real sidecar bytes."""

    def _cfg_pkg(self, tmp_path, *, sidecar=None, root=None):
        root = root or tmp_path
        path = _write_csv(root, _BASE_ROWS)
        man = _manifest(root, path)
        rec = _source_record(sidecar=sidecar)
        pkg = _package(source_manifest=man, source_record=rec)
        cfg = types.SimpleNamespace(source_manifest=man)
        return cfg, pkg

    def test_missing_sidecar_candidate_only(self, tmp_path,
                                            descriptive_regime):
        cfg, pkg = self._cfg_pkg(tmp_path)
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, pkg)
        assert receipt["status"] == "CANDIDATE_ONLY"
        assert any("sidecar" in p for p in receipt["problems"])

    def test_wrong_sidecar_digest_candidate_only(
            self, tmp_path, descriptive_regime):
        sidecar = _write_sidecar(tmp_path)
        rec = _source_record(sidecar={
            "relpath": "sidecar.json", "sha256": "f" * 64})
        path = _write_csv(tmp_path, _BASE_ROWS)
        man = _manifest(tmp_path, path)
        pkg = _package(source_manifest=man, source_record=rec)
        cfg = types.SimpleNamespace(source_manifest=man)
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, pkg)
        assert receipt["status"] == "CANDIDATE_ONLY"
        assert any("sidecar" in p for p in receipt["problems"])

    def test_malformed_sidecar_json_candidate_only(
            self, tmp_path, descriptive_regime):
        (tmp_path / "sidecar.json").write_text("not-json{")
        digest = hashlib.sha256(
            (tmp_path / "sidecar.json").read_bytes()).hexdigest()
        rec = _source_record(sidecar={
            "relpath": "sidecar.json", "sha256": digest})
        path = _write_csv(tmp_path, _BASE_ROWS)
        man = _manifest(tmp_path, path)
        pkg = _package(source_manifest=man, source_record=rec)
        cfg = types.SimpleNamespace(source_manifest=man)
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, pkg)
        assert receipt["status"] == "CANDIDATE_ONLY"

    def test_leaf_symlink_sidecar_candidate_only(
            self, tmp_path, descriptive_regime):
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        sidecar = _write_sidecar(real_dir)
        link = tmp_path / "sidecar.json"
        link.symlink_to(real_dir / "sidecar.json")
        rec = _source_record(sidecar={
            "relpath": "sidecar.json",
            "sha256": sidecar["sha256"]})
        path = _write_csv(tmp_path, _BASE_ROWS)
        man = _manifest(tmp_path, path)
        pkg = _package(source_manifest=man, source_record=rec)
        cfg = types.SimpleNamespace(source_manifest=man)
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, pkg)
        assert receipt["status"] == "CANDIDATE_ONLY"

    def test_intermediate_symlink_sidecar_candidate_only(
            self, tmp_path, descriptive_regime):
        real_dir = tmp_path / "real"
        real_dir.mkdir()
        sidecar = _write_sidecar(real_dir)
        link_dir = tmp_path / "linkdir"
        link_dir.symlink_to("real")
        rec = _source_record(sidecar={
            "relpath": "linkdir/sidecar.json",
            "sha256": sidecar["sha256"]})
        path = _write_csv(tmp_path, _BASE_ROWS)
        man = _manifest(tmp_path, path)
        pkg = _package(source_manifest=man, source_record=rec)
        cfg = types.SimpleNamespace(source_manifest=man)
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, pkg)
        assert receipt["status"] == "CANDIDATE_ONLY"

    def test_valid_sidecar_passes_gate(self, tmp_path,
                                       descriptive_regime):
        sidecar = _write_sidecar(tmp_path)
        cfg, pkg = self._cfg_pkg(tmp_path, sidecar=sidecar)
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, pkg)
        assert not any("sidecar" in p
                       for p in receipt["problems"])


class TestR112PreFitGate:
    """Finding 3: unverified sources never invoke run_regimes."""

    @pytest.mark.parametrize("posture",
                             ("CANDIDATE_ONLY", "REJECTED"))
    def test_unverified_posture_never_fits(self, posture,
                                           monkeypatch, tmp_path):
        called = []
        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.run_regimes",
            lambda *a, **k: called.append(1) or
            {"status": "DESCRIPTIVE_REGIME_ONLY"})
        path = _write_csv(tmp_path, _BASE_ROWS)
        man = _manifest(tmp_path, path)
        pkg = _package(source_manifest=man,
                       source_record=_source_record(posture))
        cfg = types.SimpleNamespace(source_manifest=man)
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, pkg)
        assert receipt["status"] == "CANDIDATE_ONLY"
        assert called == [], "engine was invoked for an " \
            "unverified source"
        assert receipt["regime_artifact_digest"] == ""

    def test_bad_sidecar_never_fits(self, monkeypatch, tmp_path):
        called = []
        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.run_regimes",
            lambda *a, **k: called.append(1) or
            {"status": "DESCRIPTIVE_REGIME_ONLY"})
        path = _write_csv(tmp_path, _BASE_ROWS)
        man = _manifest(tmp_path, path)
        # fake sidecar binding -> sidecar gate fails -> CANDIDATE
        pkg = _package(source_manifest=man)
        cfg = types.SimpleNamespace(source_manifest=man)
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, pkg)
        assert receipt["status"] == "CANDIDATE_ONLY"
        assert called == []


class TestR112OpportunityBinding:
    """Finding 5: source/units binding + unique identities."""

    def test_foreign_source_opportunity_rejected(self):
        opps = list(_opportunity_frame())
        opps[0] = dataclasses.replace(opps[0],
                                      source_id="foreign")
        with pytest.raises(ValueError, match="source_id"):
            _package(opps=tuple(opps))

    def test_undeclared_unit_opportunity_rejected(self):
        man = dict(_pkg_manifest(), units=["koshi"])
        with pytest.raises(ValueError, match="units"):
            _package(source_manifest=man)

    def test_duplicate_opportunity_id_rejected(self):
        opps = list(_opportunity_frame())
        opps[0] = dataclasses.replace(
            opps[0], opportunity_id=opps[1].opportunity_id)
        with pytest.raises(ValueError, match="duplicated"):
            _package(opps=tuple(opps))

    def test_duplicate_window_identity_rejected(self):
        opps = list(_opportunity_frame())
        first = opps[1]
        opps[0] = dataclasses.replace(
            opps[0], unit_id=first.unit_id,
            window_start=first.window_start,
            window_end=first.window_end)
        with pytest.raises(ValueError, match="duplicate"):
            _package(opps=tuple(opps))

    def test_shared_frame_ids_rejected(self):
        opps = list(_opportunity_frame())
        opps[0] = dataclasses.replace(
            opps[0], frame_ids=opps[1].frame_ids)
        with pytest.raises(ValueError, match="frame_ids"):
            _package(opps=tuple(opps))

    def test_opportunity_permutation_identical_digests(self):
        """Finding 7: digest stability under frame permutation."""
        opps = _opportunity_frame()
        pkg_a = _package(opps=opps)
        pkg_b = _package(opps=tuple(reversed(opps)))
        assert pkg_a["opportunity_digest"] == \
            pkg_b["opportunity_digest"]
        assert pkg_a["control_digest"] == pkg_b["control_digest"]


class TestR112MalformedEngineOutput:
    """Finding 6: malformed engine outputs -> RUN_ERROR."""

    def _verified(self, tmp_path):
        path = _write_csv(tmp_path, _BASE_ROWS)
        man = _manifest(tmp_path, path)
        sidecar = _write_sidecar(tmp_path)
        pkg = _package(
            source_manifest=man,
            source_record=_source_record(sidecar=sidecar))
        cfg = types.SimpleNamespace(source_manifest=man)
        return cfg, pkg

    @pytest.mark.parametrize("artifact",
                             [None, ["x"], "str", 42])
    def test_nonmapping_artifact_run_error(self, tmp_path,
                                           monkeypatch, artifact):
        cfg, pkg = self._verified(tmp_path)
        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.run_regimes",
            lambda *a, **k: artifact)
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, pkg)
        assert receipt["status"] == "RUN_ERROR"
        assert any("non-mapping" in p for p in receipt["problems"])

    @pytest.mark.parametrize("frozen", [None, ["x"], "str"])
    def test_nonmapping_freeze_run_error(self, tmp_path,
                                         monkeypatch, frozen):
        cfg, pkg = self._verified(tmp_path)
        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.run_regimes",
            lambda *a, **k: {"status": "DESCRIPTIVE_REGIME_ONLY"})
        monkeypatch.setattr(
            "nepal.science_v0.glof_poc.freeze_regime_artifact",
            lambda a: frozen)
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, pkg)
        assert receipt["status"] == "RUN_ERROR"

    def test_malformed_holdout_problems_bounded(self, tmp_path):
        cfg, pkg = self._verified(tmp_path)
        mutated = dict(pkg)
        mutated["holdout_plan"] = {"rejected": True,
                                   "problems": "not-a-list"}
        mutated["holdout_digest"] = sha256_canonical(
            mutated["holdout_plan"])
        receipt = run_glof_descriptive_poc(
            None, ["f1"], None, cfg, mutated)
        # digest is consistent; the section is bounded — status is
        # a vocab member and the receipt stays non-promotable
        assert receipt["status"] in (
            "RUN_ERROR", "CANDIDATE_ONLY",
            "UNDERPOWERED_DESCRIPTIVE_ONLY")
        assert any("holdout" in p for p in receipt["problems"])
