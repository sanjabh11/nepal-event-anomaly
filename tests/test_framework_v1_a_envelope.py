"""Phase A authenticated-envelope tests: deterministic materialization, hash
binding, tamper rejection, forged-boolean rejection, and the frozen
source-supported claim boundary.

Every forged case re-binds the envelope self-hash, simulating a sophisticated
forger.  Verification must still reject; no caller boolean may authorize A.
"""
import json

from nepal.framework_v1 import contract as C
from nepal.framework_v1.catalog import (A_ARTIFACTS, A_ENVELOPE_FILENAME,
                                        A_ENVELOPE_TYPE, A_PRIMARY_ARTIFACTS,
                                        A_SOURCE_LIMITATIONS, build_catalog,
                                        load_source_catalog_records,
                                        materialize_phase_a,
                                        verify_phase_a_envelope)
from nepal.framework_v1.controls import ControlsConfig
from nepal.framework_v1.provenance import (bind_artifact_envelope,
                                           sha256_canonical, sha256_file,
                                           verify_manifest)

CONFIG = ControlsConfig()

DATA_HASH = "240a3615f89cdd9c49b6833623084d4dbdd5b5f85c5b50e1fe4ea7c84513ec0a"


def raw_row(**over):
    base = {"glacier_id": "G0001", "event_name": "Env Event",
            "lat": 33.5, "lon": 82.5, "glacier_name": "Env Glacier",
            "country": "China", "rgi_region_name": "Inner Tibet",
            "rgi_v7_id": "RGI2000-v7.0-G-13-00001",
            "day": 17, "month": "July", "year": 2016,
            "date_min": None, "date_max": None,
            "hazard_type": "Rock-Ice Avalanche",
            "total_volume": 68, "initial_volume": None, "large": 1,
            "slope_detachment_zone": 12.9,
            "triggers": "Precipitation trend; Weak bedrock",
            "impact": "Traveled 6 km; no fatalities",
            "references": "Kaeaeb et al. (2016)",
            "links": "https://doi.org/10.1038/s41561-017-0039-7",
            "comments": "Glacier instability; surge-like behavior",
            "uncertainties": ""}
    base.update(over)
    return base


def failing_raw_rows():
    """A catalog whose honest A_CATALOG gate is FAIL (below 5 eligible)."""
    return [raw_row(event_name="Solo Event", day=2, month="May", year=2018,
                    glacier_name="Solo Glacier", lat=30.1, lon=81.2)]


def passing_raw_rows(n=5):
    return [raw_row(event_name=f"Pass Event {i}", day=10 + i, month="June",
                    year=2020 + i, glacier_name=f"Pass Glacier {i}",
                    lat=33.0 + i, lon=80.0 + i,
                    impact=f"Traveled {i} km")
            for i in range(n)]


def write_source_catalog(path, records):
    path.write_text(json.dumps(records, sort_keys=True), encoding="utf-8")


def materialize(out_dir, raw, *, source_path, **kwargs):
    result = build_catalog(raw, controls=CONFIG)
    return materialize_phase_a(result, out_dir,
                               source_catalog_path=source_path, **kwargs)


def read_envelope(out_dir):
    return json.loads((out_dir / A_ENVELOPE_FILENAME).read_text())


def rebind_envelope(envelope, mutate):
    """Re-serialize an envelope after a mutation, recomputing the outer
    envelope self-hash.  This is what a competent forger would produce;
    verification must still reject."""
    mutated = mutate(dict(envelope))
    mutated.pop("artifact_sha256", None)
    return bind_artifact_envelope(mutated)


class TestDeterministicMaterialization:
    def test_full_artifact_set_written_and_manifest_verifies(self, tmp_path):
        out = tmp_path / "out"
        raw = passing_raw_rows()
        write_source_catalog(tmp_path / "raw.json", raw)
        paths = materialize(out, raw, source_path=tmp_path / "raw.json")
        assert sorted(p.name for p in out.iterdir()) == sorted(
            A_PRIMARY_ARTIFACTS + ("source_row_hashes.json",))
        assert paths["envelope"].name == A_ENVELOPE_FILENAME
        manifest = json.loads((out / "catalog_manifest.json").read_text())
        ok, problems = verify_manifest(out, manifest)
        assert ok is True and problems == []

    def test_identical_input_produces_byte_identical_artifacts(self, tmp_path):
        raw = passing_raw_rows()
        first, second = tmp_path / "a", tmp_path / "b"
        for out in (first, second):
            write_source_catalog(tmp_path / "raw.json", raw)
            materialize(out, raw, source_path=tmp_path / "raw.json")
        for name in A_PRIMARY_ARTIFACTS:
            assert (first / name).read_bytes() == (second / name).read_bytes(), name
        assert read_envelope(first)["artifact_sha256"] == \
            read_envelope(second)["artifact_sha256"]

    def test_envelope_carries_complete_inventory(self, tmp_path):
        raw = passing_raw_rows()
        write_source_catalog(tmp_path / "raw.json", raw)
        extra = tmp_path / "source_extra.json"
        write_source_catalog(extra, raw)
        materialize(out := tmp_path / "out", raw,
                    source_path=tmp_path / "raw.json",
                    source_artifacts={"adjudication_ledger": extra},
                    data_contract_sha256=DATA_HASH)
        env = read_envelope(out)
        assert env["envelope_type"] == A_ENVELOPE_TYPE
        assert env["provenance"]["source_catalog_sha256"] == sha256_file(
            tmp_path / "raw.json")
        assert env["provenance"]["data_contract_sha256"] == DATA_HASH
        assert set(env["output_artifact_hashes"]) == set(A_ARTIFACTS)
        for name, digest in env["output_artifact_hashes"].items():
            assert digest == sha256_file(out / name)
        for event in env["events"]:
            assert len(event["raw_row_sha256"]) == 64


class TestVerification:
    def test_pristine_envelope_fully_verifies(self, tmp_path):
        raw = passing_raw_rows()
        write_source_catalog(tmp_path / "raw.json", raw)
        extra = tmp_path / "source_extra.json"
        write_source_catalog(extra, raw)
        materialize(out := tmp_path / "out", raw,
                    source_path=tmp_path / "raw.json",
                    source_artifacts={"adjudication_ledger": extra},
                    data_contract_sha256=DATA_HASH)
        ok, problems = verify_phase_a_envelope(
            read_envelope(out), out_dir=out,
            source_catalog_path=tmp_path / "raw.json",
            source_artifacts={"adjudication_ledger": extra},
            data_contract_sha256=DATA_HASH)
        assert ok is True, problems
        assert problems == []

    def test_hash_level_verification_without_source(self, tmp_path):
        raw = passing_raw_rows()
        write_source_catalog(tmp_path / "raw.json", raw)
        materialize(out := tmp_path / "out", raw, source_path=tmp_path / "raw.json")
        ok, problems = verify_phase_a_envelope(read_envelope(out), out_dir=out)
        assert ok is True, problems

    def test_reported_counts_recomputed_from_artifacts(self, tmp_path):
        raw = passing_raw_rows(n=6)
        write_source_catalog(tmp_path / "raw.json", raw)
        materialize(out := tmp_path / "out", raw, source_path=tmp_path / "raw.json")
        env = read_envelope(out)
        gate_on_disk = json.loads((out / "catalog_gate.json").read_text())
        assert gate_on_disk == env["gate"]
        eligible = [e for e in env["events"]
                    if e["eligibility_status"] == "ELIGIBLE"]
        assert env["gate"]["n_eligible"] == len(eligible)
        ok, problems = verify_phase_a_envelope(
            env, out_dir=out, source_catalog_path=tmp_path / "raw.json")
        assert ok is True, problems

    def test_missing_envelope_hash_rejected(self, tmp_path):
        raw = passing_raw_rows()
        write_source_catalog(tmp_path / "raw.json", raw)
        materialize(out := tmp_path / "out", raw, source_path=tmp_path / "raw.json")
        env = read_envelope(out)
        env.pop("artifact_sha256")
        ok, problems = verify_phase_a_envelope(env, out_dir=out)
        assert ok is False
        assert any("artifact_sha256 is required" in p for p in problems)


class TestTamperRejection:
    def _materialized(self, tmp_path):
        raw = passing_raw_rows()
        write_source_catalog(tmp_path / "raw.json", raw)
        materialize(out := tmp_path / "out", raw, source_path=tmp_path / "raw.json")
        return out, read_envelope(out), raw

    def test_changed_catalog_output_rejected(self, tmp_path):
        out, env, _ = self._materialized(tmp_path)
        csv_bytes = (out / "catalog_normalized.csv").read_bytes()
        (out / "catalog_normalized.csv").write_bytes(
            csv_bytes.replace(b"Pass Glacier 0", b"Tampered Glacier 0"))
        ok, problems = verify_phase_a_envelope(env, out_dir=out)
        assert ok is False
        assert any("checksum mismatch: catalog_normalized.csv" in p
                   for p in problems)

    def test_changed_controls_output_rejected(self, tmp_path):
        out, env, _ = self._materialized(tmp_path)
        (out / "controls_lock.json").write_text(
            (out / "controls_lock.json").read_text().replace(
                '"dry_season_min_scenes":3', '"dry_season_min_scenes":4'))
        ok, problems = verify_phase_a_envelope(env, out_dir=out)
        assert ok is False
        assert any("checksum mismatch: controls_lock.json" in p
                   for p in problems)

    def test_changed_holdout_plan_output_rejected(self, tmp_path):
        out, env, _ = self._materialized(tmp_path)
        plan = json.loads((out / "holdout_plan.json").read_text())
        plan["rebalanced"] = True
        (out / "holdout_plan.json").write_text(
            json.dumps(plan, sort_keys=True, separators=(",", ":")) + "\n")
        ok, problems = verify_phase_a_envelope(env, out_dir=out)
        assert ok is False
        assert any("checksum mismatch: holdout_plan.json" in p
                   for p in problems)

    def test_source_catalog_tamper_rejected_by_deep_recompute(self, tmp_path):
        out, env, _ = self._materialized(tmp_path)
        source = tmp_path / "raw.json"
        records = json.loads(source.read_text())
        records[0]["hazard_type"] = "GLOF"
        write_source_catalog(source, records)
        ok, problems = verify_phase_a_envelope(
            env, out_dir=out, source_catalog_path=source)
        assert ok is False
        assert any("source catalog" in p.lower() for p in problems)

    def test_forged_passing_gate_boolean_rejected(self, tmp_path):
        """Set gate.passed=True on an honestly FAILING catalog, recompute both
        self-hashes, and confirm the verifier still refuses."""
        raw = failing_raw_rows()
        write_source_catalog(tmp_path / "raw.json", raw)
        materialize(out := tmp_path / "out", raw, source_path=tmp_path / "raw.json")
        env = read_envelope(out)
        assert env["gate"]["passed"] is False

        def forge(payload):
            payload["gate"]["passed"] = True
            payload["gate"].pop("gate_artifact_sha256", None)
            payload["gate"]["gate_artifact_sha256"] = sha256_canonical(
                payload["gate"])
            return payload

        forged = rebind_envelope(env, forge)
        ok, problems = verify_phase_a_envelope(forged, out_dir=out)
        assert ok is False
        assert any("passed" in p for p in problems)
        ok_deep, problems_deep = verify_phase_a_envelope(
            forged, out_dir=out, source_catalog_path=tmp_path / "raw.json")
        assert ok_deep is False
        assert problems_deep != []

    def test_forged_check_count_rejected(self, tmp_path):
        out, env, _ = self._materialized(tmp_path)

        def forge(payload):
            payload["gate"]["checks"]["min_eligible_events"]["observed"] = 999
            return payload

        forged = rebind_envelope(env, forge)
        ok, problems = verify_phase_a_envelope(forged, out_dir=out)
        assert ok is False

    def test_missing_artifact_rejected(self, tmp_path):
        out, env, _ = self._materialized(tmp_path)
        (out / "catalog_gate.json").unlink()
        ok, problems = verify_phase_a_envelope(env, out_dir=out)
        assert ok is False
        assert any("missing artifact: catalog_gate.json" in p for p in problems)

    def test_recorded_source_artifact_missing_rejected(self, tmp_path):
        out, env, _ = self._materialized(tmp_path)

        def forge(payload):
            payload["provenance"]["source_artifact_hashes"][
                "bashkova_rupper_db"] = "4" * 64
            return payload

        forged = rebind_envelope(env, forge)
        ok, problems = verify_phase_a_envelope(
            forged, out_dir=out, source_catalog_path=tmp_path / "raw.json")
        assert ok is False
        assert any("bashkova_rupper_db" in p for p in problems)

    def test_recorded_source_artifact_wrong_bytes_rejected(self, tmp_path):
        raw = passing_raw_rows()
        write_source_catalog(tmp_path / "raw.json", raw)
        extra = tmp_path / "source_extra.json"
        write_source_catalog(extra, raw)
        materialize(out := tmp_path / "out", raw,
                    source_path=tmp_path / "raw.json",
                    source_artifacts={"adjudication_ledger": extra})
        write_source_catalog(extra, failing_raw_rows())
        ok, problems = verify_phase_a_envelope(
            read_envelope(out), out_dir=out,
            source_catalog_path=tmp_path / "raw.json",
            source_artifacts={"adjudication_ledger": extra})
        assert ok is False
        assert any("adjudication_ledger" in p for p in problems)

    def test_unsupported_status_rejected(self, tmp_path):
        out, env, _ = self._materialized(tmp_path)

        def forge(payload):
            payload["events"][0]["eligibility_status"] = "PASS"
            return payload

        forged = rebind_envelope(env, forge)
        ok, problems = verify_phase_a_envelope(forged, out_dir=out)
        assert ok is False
        assert any("status" in p for p in problems)


class TestContractAndPreregistrationBinding:
    def _materialized(self, tmp_path):
        raw = passing_raw_rows()
        write_source_catalog(tmp_path / "raw.json", raw)
        materialize(out := tmp_path / "out", raw,
                    source_path=tmp_path / "raw.json",
                    data_contract_sha256=DATA_HASH)
        return out, read_envelope(out)

    def test_framework_contract_is_runtime_bound(self, tmp_path):
        out, env = self._materialized(tmp_path)
        assert env["provenance"]["framework_contract_sha256"] == C.contract_hash()
        ok, _ = verify_phase_a_envelope(env, out_dir=out)
        assert ok is True

    def test_changed_framework_contract_hash_rejected(self, tmp_path):
        out, env = self._materialized(tmp_path)

        def forge(payload):
            payload["provenance"]["framework_contract_sha256"] = "1" * 64
            return payload

        ok, problems = verify_phase_a_envelope(
            rebind_envelope(env, forge), out_dir=out)
        assert ok is False
        assert any("framework contract" in p.lower() for p in problems)

    def test_stale_expected_framework_hash_cannot_authorize_forged_envelope(
            self, tmp_path):
        out, env = self._materialized(tmp_path)

        def forge(payload):
            payload["provenance"]["framework_contract_sha256"] = "0" * 64
            return payload

        ok, problems = verify_phase_a_envelope(
            rebind_envelope(env, forge),
            out_dir=out,
            expected_framework_contract_sha256="0" * 64,
        )
        assert ok is False
        assert any("runtime framework contract" in p.lower()
                   for p in problems)

    def test_changed_preregistration_hash_rejected(self, tmp_path):
        out, env = self._materialized(tmp_path)

        def forge(payload):
            payload["provenance"]["preregistration_sha256"] = "2" * 64
            return payload

        ok, problems = verify_phase_a_envelope(
            rebind_envelope(env, forge), out_dir=out)
        assert ok is False
        assert any("preregistration" in p for p in problems)

    def test_data_contract_mismatch_rejected(self, tmp_path):
        out, env = self._materialized(tmp_path)
        ok, problems = verify_phase_a_envelope(
            env, out_dir=out, data_contract_sha256="3" * 64)
        assert ok is False
        assert any("data contract" in p.lower() for p in problems)

    def test_missing_data_contract_binding_rejected_when_expected(self, tmp_path):
        raw = passing_raw_rows()
        write_source_catalog(tmp_path / "raw.json", raw)
        materialize(out := tmp_path / "out", raw, source_path=tmp_path / "raw.json")
        ok, problems = verify_phase_a_envelope(
            read_envelope(out), out_dir=out, data_contract_sha256=DATA_HASH)
        assert ok is False
        assert any("data contract" in p.lower() for p in problems)


class TestSourceSupportedBoundary:
    def test_frozen_limitations_present_in_envelope_and_gate(self, tmp_path):
        out, env, _ = TestTamperRejection()._materialized(tmp_path)
        assert env["source_limitations"] == A_SOURCE_LIMITATIONS
        assert A_SOURCE_LIMITATIONS["mechanism_independently_adjudicated"] is False
        assert A_SOURCE_LIMITATIONS["claim_scope"] == (
            "source_supported_catalog_claims_only")
        assert A_SOURCE_LIMITATIONS["data_source_status"] == (
            "POST_HOC_DATA_SOURCE_CHANGE")
        assert env["gate"]["claim_scope"] == (
            "source_supported_catalog_claims_only")
        assert env["gate"]["mechanism_validation"][
            "mechanism_independently_adjudicated"] is False

    def test_loosened_limitation_rejected_even_after_rebinding(self, tmp_path):
        out, env, _ = TestTamperRejection()._materialized(tmp_path)

        def forge(payload):
            payload["source_limitations"] = dict(
                A_SOURCE_LIMITATIONS,
                mechanism_independently_adjudicated=True)
            return payload

        ok, problems = verify_phase_a_envelope(
            rebind_envelope(env, forge), out_dir=out)
        assert ok is False
        assert any("limitations" in p.lower() for p in problems)

    def test_mechanism_labels_stay_source_supported(self):
        """Eligible rows keep source-citation-only mechanism confirmation; the
        framework never claims independent adjudication."""
        from nepal.framework_v1.catalog import build_catalog
        result = build_catalog([raw_row()], controls=CONFIG)
        rec = result["rows"][0]
        assert rec["mechanism_confirmation_scope"] == (
            "SOURCE_CITATION_ONLY_NOT_INDEPENDENT_FIELD_ADJUDICATION")
        assert rec["mechanism_confirmed"] is True  # source citation present
        assert A_SOURCE_LIMITATIONS["mechanism_independently_adjudicated"] is False


class TestSourceCatalogLoader:
    def test_list_loader(self, tmp_path):
        path = tmp_path / "raw.json"
        write_source_catalog(path, [raw_row()])
        assert load_source_catalog_records(path) == [raw_row()]

    def test_events_key_loader(self, tmp_path):
        path = tmp_path / "raw.json"
        path.write_text(json.dumps({"events": [raw_row()]}), encoding="utf-8")
        assert load_source_catalog_records(path) == [raw_row()]

    def test_rejects_non_record_document(self, tmp_path):
        path = tmp_path / "raw.json"
        path.write_text(json.dumps({"unexpected": []}), encoding="utf-8")
        try:
            load_source_catalog_records(path)
        except ValueError:
            pass
        else:
            raise AssertionError("expected ValueError")
