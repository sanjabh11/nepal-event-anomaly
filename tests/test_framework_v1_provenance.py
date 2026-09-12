"""Provenance tests: deterministic serialization, manifests, raw-SLC hygiene,
status guards, no-timestamp commitments."""
import json

import pytest

from nepal.framework_v1.provenance import (canonical_json, sha256_canonical,
                                           sha256_bytes, sha256_text,
                                           write_deterministic_json,
                                           write_deterministic_text,
                                           write_deterministic_csv,
                                           build_manifest, verify_manifest,
                                           check_no_raw_slc_paths,
                                           assert_accepted_manifest,
                                           bind_artifact_envelope,
                                           verify_artifact_envelope)
from nepal.framework_v1.catalog import CSV_FIELDS
from nepal.framework_v1.controls import ControlsConfig, create_controls_lock


class TestCanonicalJson:
    def test_sorted_keys_and_compact(self):
        assert canonical_json({"b": 1, "a": 2}) == \
            canonical_json({"a": 2, "b": 1}) == '{"a":2,"b":1}'

    def test_deterministic_for_nested(self):
        obj = {"z": [3, 1, 2], "m": {"k": None, "j": True}}
        assert canonical_json(obj) == canonical_json(
            json.loads(canonical_json(obj)))

    def test_sha256_canonical_matches_reference(self):
        assert sha256_canonical({"x": 1}) == sha256_text(
            canonical_json({"x": 1}))
        assert len(sha256_canonical({"x": 1})) == 64

    def test_sha256_bytes_matches_text(self):
        assert sha256_bytes(b"abc") == sha256_text("abc")

    def test_artifact_envelope_binds_all_content(self):
        envelope = bind_artifact_envelope({"status": "RANKED",
                                           "provenance": {"source": "x"}})
        assert verify_artifact_envelope(envelope) == (True, [])
        tampered = dict(envelope)
        tampered["provenance"] = {"source": "forged"}
        assert verify_artifact_envelope(tampered)[0] is False


class TestDeterministicWriters:
    def test_json_write_lf_and_trailing_newline(self, tmp_path):
        f = tmp_path / "out.json"
        write_deterministic_json(f, {"b": 1, "a": [1, 2]})
        raw = f.read_bytes()
        assert raw.endswith(b"\n") and b"\r" not in raw

    def test_json_deterministic_across_calls(self, tmp_path):
        f1, f2 = tmp_path / "a.json", tmp_path / "b.json"
        write_deterministic_json(f1, {"k": [3, 1]})
        write_deterministic_json(f2, {"k": [3, 1]})
        assert f1.read_bytes() == f2.read_bytes()

    def test_text_writer_normalizes_newlines_and_is_atomic_result(self, tmp_path):
        f = tmp_path / "briefing.md"
        assert write_deterministic_text(f, "a\r\nb\r") == "a\nb\n"
        assert f.read_bytes() == b"a\nb\n"

    def test_csv_lf_quoting_and_cell_semantics(self, tmp_path):
        f = tmp_path / "out.csv"
        rows = [{"event_id": "E1", "note": 'has "quote"', "flag": True,
                 "nums": [1, 2], "nothing": None}]
        write_deterministic_csv(f, ["event_id", "note", "flag", "nums",
                                    "nothing"], rows)
        raw = f.read_bytes()
        assert b"\r" not in raw and raw.endswith(b"\n")
        text = f.read_text()
        assert '"has ""quote"""' in text
        assert text.splitlines()[1].split(",")[-2:] == ["1|2", ""]

    def test_csv_column_order_fixed(self, tmp_path):
        f = tmp_path / "catalog.csv"
        write_deterministic_csv(f, list(CSV_FIELDS), [{}])
        assert f.read_text().splitlines()[0].split(",") == list(CSV_FIELDS)

    def test_csv_extras_raise(self, tmp_path):
        f = tmp_path / "x.csv"
class TestManifests:
    @pytest.mark.parametrize("bad_path", ["../escape.bin", "/absolute.bin",
                                           "nested/../../escape.bin"])
    def test_build_manifest_rejects_unsafe_relative_paths(self, bad_path):
        with pytest.raises(ValueError, match="unsafe"):
            build_manifest({bad_path: b"x"})

    def test_manifest_deterministic_no_timestamps(self, tmp_path):
        f = tmp_path / "payload.bin"
        f.write_bytes(b"payload")
        m1 = build_manifest({"payload.bin": f})
        m2 = build_manifest({"payload.bin": f})
        assert m1 == m2
        text = canonical_json(m1)
        for ts in ("2026", "time", "date"):
            assert ts not in text

    def test_manifest_sorted_files(self):
        assert list(build_manifest({"z.bin": b"1", "a.bin": b"2"})["files"]) \
            == ["a.bin", "z.bin"]

    def test_verify_manifest_detects_tamper(self, tmp_path):
        f = tmp_path / "payload.bin"
        f.write_bytes(b"payload")
        m = build_manifest({"payload.bin": f})
        f.write_bytes(b"tampered")
        ok, problems = verify_manifest(tmp_path, m)
        assert ok is False and any("mismatch" in p for p in problems)

    def test_verify_manifest_detects_missing(self, tmp_path):
        f = tmp_path / "gone.bin"
        f.write_bytes(b"x")
        m = build_manifest({"gone.bin": f})
        f.unlink()
        ok, problems = verify_manifest(tmp_path, m)
        assert ok is False and any("missing" in p for p in problems)

    def test_verify_manifest_detects_manifest_edit(self, tmp_path):
        f = tmp_path / "p.bin"
        f.write_bytes(b"p")
        m = build_manifest({"p.bin": f})
        m2 = dict(m)
        m2["files"] = {"p.bin": "0" * 64}
        ok, problems = verify_manifest(tmp_path, m2)
        assert ok is False and any("manifest_sha256" in p for p in problems)

    def test_verify_manifest_malformed_files_is_explicit_failure(self, tmp_path):
        ok, problems = verify_manifest(
            tmp_path, {"algorithm": "sha256", "files": []})
        assert ok is False
        assert any("files" in problem for problem in problems)


class TestRawSlcHygiene:
    def test_raw_slc_path_rejected(self):
        bad = {"s1": {"slc_path": "S1A_IW_SLC__1SDV_20250101T101010.SAFE"}}
        hits = check_no_raw_slc_paths(bad)
        assert hits and any("slc_path" in h for h in hits)

    def test_raw_slc_scene_string_rejected(self):
        bad = {"scene": "S1A_IW_SLC__1SDV_20250101T101010.SAFE"}
        assert check_no_raw_slc_paths(bad)

    def test_metadata_only_manifest_accepted(self):
        good = {"acquisitions": [{"platform": "S1A", "orbit": "D13",
                                  "frame": "42", "path": "013",
                                  "polarization": "VV VH",
                                  "date": "2025-12-01",
                                  "available": True}]}
        assert check_no_raw_slc_paths(good) == []
        assert_accepted_manifest(good)

    def test_accepted_manifest_rejects_slc(self):
        with pytest.raises(ValueError):
            assert_accepted_manifest({"slc_scene": "S1B_SLC.SAFE"})

    def test_accepted_manifest_rejects_non_mapping(self):
        with pytest.raises(ValueError, match="mapping"):
            assert_accepted_manifest([])

    def test_nested_slc_reference_found(self):
        bad = {"a": [{"b": {"c": "see S1A_IW_SLC__1SDV_20250101T101010.SAFE"}}]}
        assert check_no_raw_slc_paths(bad)


class TestControlsLockCommitments:
    def test_lock_deterministic_and_verifies(self):
        l1 = create_controls_lock(ControlsConfig())
        l2 = create_controls_lock(ControlsConfig())
        assert l1.sha256 == l2.sha256
        assert l1.verify() is True

    def test_load_lock_detects_modified_controls(self):
        from nepal.framework_v1.controls import load_controls_lock, \
            ControlsLockMismatch
        doc = create_controls_lock(ControlsConfig()).to_dict()
        load_controls_lock(doc)
        tampered = json.loads(json.dumps(doc))
        tampered["controls"]["min_detectable_size_m3"] = 123.0
        with pytest.raises(ControlsLockMismatch):
            load_controls_lock(tampered)

    def test_load_lock_detects_algorithm_or_framework_drift(self):
        from nepal.framework_v1.controls import load_controls_lock, ControlsLockMismatch
        doc = create_controls_lock(ControlsConfig()).to_dict()
        doc["algorithm"] = "md5"
        with pytest.raises(ControlsLockMismatch):
            load_controls_lock(doc)

    def test_lock_payload_membership(self):
        lock = create_controls_lock(ControlsConfig(
            expected_winter_acquisitions=25))
        assert lock.verify_payload(ControlsConfig(
            expected_winter_acquisitions=25).to_dict())
        assert not lock.verify_payload(ControlsConfig(
            expected_winter_acquisitions=30).to_dict())
