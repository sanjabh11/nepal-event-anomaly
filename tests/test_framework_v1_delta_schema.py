"""Delta-closure schema tests (R01/R02/R03).

R01: the A envelope must carry a non-empty, recomputable source hash
inventory — the source catalog artifact plus a deterministic per-source-row
hash map keyed ``source_row:NNNN``.

R02: ``candidate_generation_id`` is canonical at the TOP LEVEL of every stage
envelope/checkpoint (A envelope, B result, B checkpoint, E artifact,
F envelope, pipeline report).  Nested compatibility copies must equal it;
missing or mismatched top-level identity is rejected.

R03: strict lineage requires one explicitly bound active audit packet whose
``candidate_generation_id`` and ``manifest_sha256`` match the verified
manifest.  A packet bound to a superseded generation is rejected as
historical/stale; no packet is an error, never a silent pass.
"""
import json

from nepal.framework_v1 import contract as C
from nepal.framework_v1.briefing import (LIABILITY, NOT_EVACUATION,
                                         build_f_envelope, verify_f_envelope,
                                         write_f_envelope)
from nepal.framework_v1.catalog import (A_ENVELOPE_FILENAME, build_catalog,
                                        load_source_catalog_records,
                                        materialize_phase_a, raw_record_hash,
                                        verify_phase_a_envelope)
from nepal.framework_v1.controls import ControlsConfig
from nepal.framework_v1.lineage import verify_candidate_lineage
from nepal.framework_v1.provenance import (bind_artifact_envelope,
                                           canonical_json, sha256_file)
from nepal.framework_v1.validation import (verify_validation_artifact,
                                           write_validation_artifact)

CONFIG = ControlsConfig()
GEN = "GEN-DELTA-V2"
GEN_STALE = "GEN-DELTA-V1"
DC_CONTRACT = "d" * 64
FC_CONTRACT = C.contract_hash()


def raw_row(**over):
    base = {"glacier_id": "G0001", "event_name": "Delta Event",
            "lat": 33.5, "lon": 82.5, "glacier_name": "Delta Glacier",
            "country": "China", "rgi_region_name": "Inner Tibet",
            "rgi_v7_id": "RGI2000-v7.0-G-13-00001",
            "day": 17, "month": "July", "year": 2016,
            "date_min": None, "date_max": None,
            "hazard_type": "Rock-Ice Avalanche",
            "total_volume": 68, "initial_volume": None, "large": 1,
            "slope_detachment_zone": 12.9,
            "triggers": "Precipitation trend",
            "impact": "Traveled 6 km",
            "references": "Kaeaeb et al. (2016)",
            "links": "https://doi.org/10.1038/x",
            "comments": "", "uncertainties": ""}
    base.update(over)
    return base


def _write_source(path, n=5):
    records = [raw_row(event_name=f"Event {i}", day=10 + i,
                       glacier_name=f"Glacier {i}", lat=33.0 + i,
                       lon=80.0 + i)
               for i in range(n)]
    path.write_text(json.dumps(records, sort_keys=True), encoding="utf-8")
    return records


def _materialize(tmp_path, n=5, **kwargs):
    source = tmp_path / "hma_events_all.json"
    records = _write_source(source, n)
    result = build_catalog(records, controls=CONFIG)
    out = tmp_path / "out"
    materialize_phase_a(result, out, source_catalog_path=source, **kwargs)
    return out, source, records


def _read_envelope(out):
    return json.loads((out / A_ENVELOPE_FILENAME).read_text())


def _rebind(env, mutate):
    mutated = mutate(dict(env))
    mutated.pop("artifact_sha256", None)
    return bind_artifact_envelope(mutated)


class TestASourceHashInventory:
    def test_source_artifact_hashes_nonempty(self, tmp_path):
        out, source, _ = _materialize(tmp_path)
        env = _read_envelope(out)
        recorded = env["provenance"]["source_artifact_hashes"]
        assert isinstance(recorded, dict) and recorded
        assert recorded[source.name] == sha256_file(source)

    def test_source_row_hashes_cover_every_raw_row(self, tmp_path):
        out, source, records = _materialize(tmp_path, n=7)
        env = _read_envelope(out)
        row_hashes = env["provenance"]["source_row_hashes"]
        assert len(row_hashes) == 7
        for i, rec in enumerate(records):
            assert row_hashes[f"source_row:{i:04d}"] == raw_record_hash(rec)

    def test_one_byte_source_mutation_detected(self, tmp_path):
        out, source, _ = _materialize(tmp_path)
        env = _read_envelope(out)
        records = json.loads(source.read_text())
        records[0]["comments"] = "x"
        source.write_text(json.dumps(records, sort_keys=True),
                          encoding="utf-8")
        ok, problems = verify_phase_a_envelope(
            env, out_dir=out, source_catalog_path=source)
        assert ok is False
        assert any("source_row" in p for p in problems)

    def test_empty_source_inventory_rejected(self, tmp_path):
        out, source, _ = _materialize(tmp_path)
        env = _read_envelope(out)

        def forge(payload):
            payload["provenance"]["source_artifact_hashes"] = {}
            return payload

        ok, problems = verify_phase_a_envelope(
            _rebind(env, forge), out_dir=out, source_catalog_path=source)
        assert ok is False
        assert any("source artifact" in p.lower() for p in problems)

    def test_missing_source_row_hash_rejected(self, tmp_path):
        out, source, _ = _materialize(tmp_path)
        env = _read_envelope(out)

        def forge(payload):
            payload["provenance"]["source_row_hashes"].pop("source_row:0000")
            return payload

        ok, problems = verify_phase_a_envelope(
            _rebind(env, forge), out_dir=out, source_catalog_path=source)
        assert ok is False
        assert any("source_row" in p for p in problems)


class TestCanonicalGenerationField:
    def test_a_envelope_top_level_generation(self, tmp_path):
        out, source, _ = _materialize(
            tmp_path, candidate_generation_id=GEN)
        env = _read_envelope(out)
        assert env["candidate_generation_id"] == GEN
        assert env["provenance"]["candidate_generation_id"] == GEN

    def test_a_verify_rejects_missing_top_level_generation(self, tmp_path):
        out, source, _ = _materialize(
            tmp_path, candidate_generation_id=GEN)
        env = _read_envelope(out)

        def forge(payload):
            payload.pop("candidate_generation_id", None)
            return payload

        ok, problems = verify_phase_a_envelope(
            _rebind(env, forge), out_dir=out, source_catalog_path=source,
            expected_candidate_generation_id=GEN)
        assert ok is False
        assert any("candidate_generation_id" in p for p in problems)

    def test_a_verify_rejects_top_level_nested_mismatch(self, tmp_path):
        out, source, _ = _materialize(
            tmp_path, candidate_generation_id=GEN)
        env = _read_envelope(out)

        def forge(payload):
            payload["candidate_generation_id"] = GEN_STALE
            return payload

        ok, problems = verify_phase_a_envelope(
            _rebind(env, forge), out_dir=out, source_catalog_path=source,
            expected_candidate_generation_id=GEN)
        assert ok is False

    def test_e_artifact_hoists_top_level_generation(self, tmp_path):
        summary = {"status": "INDETERMINATE",
                   "input_hashes": {"controls_lock": "a" * 64}}
        gate = {"gate_id": C.GateId.E_VALIDATION.value, "passed": False,
                "checks": {"required_inputs": {"passed": False}}}
        artifact = write_validation_artifact(
            tmp_path / "e.json", summary, gate,
            provenance={"candidate_generation_id": GEN,
                        "framework_contract_sha256": C.contract_hash(),
                        "input_hashes": summary["input_hashes"]})
        assert artifact["candidate_generation_id"] == GEN
        ok, problems = verify_validation_artifact(artifact)
        assert ok, problems

    def test_e_artifact_rejects_generation_mismatch(self, tmp_path):
        summary = {"status": "INDETERMINATE", "input_hashes": {}}
        gate = {"gate_id": C.GateId.E_VALIDATION.value, "passed": False,
                "checks": {}}
        artifact = write_validation_artifact(
            tmp_path / "e.json", summary, gate,
            provenance={"candidate_generation_id": GEN,
                        "framework_contract_sha256": C.contract_hash()})
        forged = dict(artifact)
        forged["candidate_generation_id"] = GEN_STALE
        forged.pop("artifact_sha256", None)
        forged = bind_artifact_envelope(forged)
        ok, problems = verify_validation_artifact(forged)
        assert ok is False
        assert any("candidate_generation_id" in p for p in problems)

    def test_f_envelope_requires_top_level_generation(self, tmp_path):
        summary = {"status": "INDETERMINATE", "input_hashes": {}}
        envelope = build_f_envelope(
            summary, contract_hash="a" * 64, candidate_generation_id=GEN,
            manifest_sha256="b" * 64,
            briefing_text="\n".join([NOT_EVACUATION, LIABILITY]))
        path = tmp_path / "f_envelope.json"
        write_f_envelope(path, envelope)
        ok, errors = verify_f_envelope(
            path, expected_candidate_generation_id=GEN)
        assert ok, errors
        ok, errors = verify_f_envelope(
            path, expected_candidate_generation_id=GEN_STALE)
        assert not ok
        assert any("candidate_generation_id" in e for e in errors)


class TestActiveAuditPacketBinding:
    """R03: strict lineage requires exactly one explicitly bound active
    audit packet; historical packets are rejected, never silently accepted."""

    def _package(self, tmp_path, generation=GEN):
        from tests.test_framework_v1_lineage import _build_package
        return _build_package(tmp_path, generation=generation)

    def test_missing_audit_packet_is_fatal_in_strict(self, tmp_path):
        root, manifest_path, expected, _ = self._package(tmp_path)
        result = verify_candidate_lineage(
            manifest_path, root,
            expected_manifest_sha256=expected,
            expected_data_contract_sha256=DC_CONTRACT,
            expected_framework_contract_sha256=FC_CONTRACT,
            candidate_generation_id=GEN)
        assert not result.ok
        assert any("audit packet" in e for e in result.errors)

    def test_historical_packet_rejected_as_stale(self, tmp_path):
        root, manifest_path, expected, _ = self._package(tmp_path)
        stale = tmp_path / "v1_audit.json"
        stale.write_text(canonical_json({
            "candidate_generation_id": GEN_STALE,
            "receipt_type": "historical"}), encoding="utf-8")
        result = verify_candidate_lineage(
            manifest_path, root,
            expected_manifest_sha256=expected,
            expected_data_contract_sha256=DC_CONTRACT,
            expected_framework_contract_sha256=FC_CONTRACT,
            candidate_generation_id=GEN,
            audit_packet_path=stale)
        assert not result.ok
        assert any("historical" in e or "stale" in e
                   for e in result.errors)

    def test_current_packet_classified_active(self, tmp_path):
        root, manifest_path, expected, _ = self._package(tmp_path)
        active = tmp_path / "audit.json"
        active.write_text(canonical_json({
            "candidate_generation_id": GEN,
            "manifest_sha256": expected}), encoding="utf-8")
        result = verify_candidate_lineage(
            manifest_path, root,
            expected_manifest_sha256=expected,
            expected_data_contract_sha256=DC_CONTRACT,
            expected_framework_contract_sha256=FC_CONTRACT,
            candidate_generation_id=GEN,
            audit_packet_path=active)
        assert result.ok, result.errors
        assert result.checks["audit_packet_identity"]["classification"] == \
            "active"
