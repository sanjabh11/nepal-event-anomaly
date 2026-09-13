"""W2/N1 Feature-Matrix Contract (FMX) tests — hardened boundary.

The dirty-main feature CSV/NetCDF is treated as absent: the schema-only /
fixture path can only emit FMX_BLOCKED_PENDING_EXPLICIT_FREEZE.  FMX_READY
requires a future externally supplied write-once freeze token bound to real
files on disk (matrix + token file under an external artifact root), with
SHA-256 and byte_count recomputed from those bytes.  Pure in-memory
token/matrix pairs can never produce a verifiable READY.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from nepal.framework_v1 import feature_matrix_contract as fmx
from nepal.framework_v1.provenance import sha256_file

FC_FIELD = fmx.FEATURE_CONTRACT_SHA256_FIELD

DIRTY_MAIN = "/Users/sanjayb/nepal-event-anomaly"


def _column(name="t2m_mean", unit="K", role="feature"):
    return {"name": name, "unit": unit, "role": role,
            "aggregation": "seasonal_mean",
            "temporal_resolution": "daily",
            "availability_time": "2015-04-01"}


def _matrix_meta(matrix_sha="23" * 32, byte_count=12345):
    return {
        "matrix_id": "fmx-synthetic-001",
        "candidate_generation_id": "synthetic-gen-0",
        "source_artifact_id": "synthetic-source-1",
        "source_sha256": "ab" * 32,
        "byte_count": byte_count,
        "producer_sha256": "cd" * 32,
        FC_FIELD: "ef" * 32,
        "preregistration_sha256": "01" * 32,
        "matrix_sha256": matrix_sha,
        "columns": [_column()],
        "missingness": {"fraction": 0.0, "policy": "complete"},
        "date_range": {"start": "2001-06-01", "end": "2015-08-31"},
        "spatial_coverage": {"region_id": "synthetic-region",
                             "n_units": 4},
        "target_spec": {"name": "synthetic_target",
                        "definition": "fixture"},
        "data_source_status": "SYNTHETIC_FIXTURE",
    }


def _token(matrix_sha="23" * 32, producer_sha="cd" * 32):
    return {"token_type": fmx.FREEZE_TOKEN_TYPE,
            "matrix_sha256": matrix_sha,
            "producer_sha256": producer_sha,
            FC_FIELD: "ef" * 32,
            "preregistration_sha256": "01" * 32,
            "write_once": True,
            "issued_by": "external-freeze-authority",
            "issued_at": "2026-09-13T00:00:00Z"}


def _staged(tmp_path, matrix_bytes=b"matrix-bytes", token=None):
    """Stage a real matrix file + token file under an external root."""
    root = tmp_path / "ext-root"
    root.mkdir()
    mbytes = matrix_bytes
    (root / "matrix.bin").write_bytes(mbytes)
    meta = _matrix_meta(matrix_sha=sha256_file(root / "matrix.bin"),
                        byte_count=len(mbytes))
    if token is None:
        token = _token(matrix_sha=meta["matrix_sha256"],
                       producer_sha=meta["producer_sha256"])
    (root / "freeze_token.json").write_text(json.dumps(token))
    return root, meta, token


class TestBlockedBoundary:
    def test_blocked_envelope_has_absent_matrix(self):
        env = fmx.build_fmx_envelope()
        assert env["fmx_status"] == fmx.FMX_BLOCKED_PENDING_EXPLICIT_FREEZE
        assert env["freeze_reason"] == "NO_EXTERNAL_FREEZE"
        assert env["matrix"]["status"] == "ABSENT"
        ok, problems = fmx.verify_fmx_envelope(env)
        assert ok, problems

    def test_blocked_envelope_nulls_declared_digests(self):
        """Declared-but-unverified matrix metadata must not carry plausible
        64-hex digests into the blocked envelope."""
        env = fmx.build_fmx_envelope(_matrix_meta())
        assert env["fmx_status"] == fmx.FMX_BLOCKED_PENDING_EXPLICIT_FREEZE
        declared = env.get("declared_matrix_metadata")
        assert declared is not None
        for key, value in declared.items():
            if key.endswith("_sha256") or key == "byte_count":
                assert value is None, key
        assert declared.get("digest_status") in (
            "UNTRUSTED_UNVERIFIED", "ABSENT")
        # nothing in the envelope may contain a real-looking 64-hex digest
        import re
        text = json.dumps(env)
        assert not re.search(r'"(matrix_sha256|source_sha256|'
                             r'producer_sha256)":\s*"[0-9a-f]{64}"', text)

    def test_no_token_generation_path(self):
        for name in ("mint_freeze_token", "generate_freeze_token",
                     "create_freeze_token"):
            assert not hasattr(fmx, name)

    def test_no_path_parameters_on_fixture_path(self):
        """The builder never takes a path that could point at the dirty
        checkout — READY bindings are root-relative only."""
        import inspect
        sig = inspect.signature(fmx.build_fmx_envelope)
        assert "path" not in sig.parameters
        assert "matrix_path" not in sig.parameters


class TestReadyGate:
    def test_in_memory_token_cannot_be_ready(self):
        """G-N1-1: matching in-memory digests are NOT enough."""
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(_matrix_meta(), freeze_token=_token())

    def test_ready_with_file_bound_token(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        env = fmx.build_fmx_envelope(
            meta, freeze_token=token, artifact_root=root,
            matrix_relpath="matrix.bin", token_relpath="freeze_token.json")
        assert env["fmx_status"] == fmx.FMX_READY
        ok, problems = fmx.verify_fmx_envelope(env, artifact_root=root)
        assert ok, problems

    def test_ready_verify_without_root_fails(self, tmp_path):
        """A READY envelope cannot verify without on-disk evidence."""
        root, meta, token = _staged(tmp_path)
        env = fmx.build_fmx_envelope(
            meta, freeze_token=token, artifact_root=root,
            matrix_relpath="matrix.bin", token_relpath="freeze_token.json")
        ok, problems = fmx.verify_fmx_envelope(env)  # no artifact_root
        assert not ok
        assert any("file" in p or "root" in p for p in problems)

    def test_ready_rejected_matrix_mismatch(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        (root / "matrix.bin").write_bytes(b"tampered")
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(
                meta, freeze_token=token, artifact_root=root,
                matrix_relpath="matrix.bin",
                token_relpath="freeze_token.json")

    def test_ready_rejected_token_matrix_mismatch(self, tmp_path):
        root, meta, _ = _staged(tmp_path)
        bad = _token(matrix_sha="ff" * 32)
        (root / "freeze_token.json").write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(
                meta, freeze_token=bad, artifact_root=root,
                matrix_relpath="matrix.bin",
                token_relpath="freeze_token.json")

    def test_ready_rejected_when_token_not_write_once(self, tmp_path):
        root, meta, _ = _staged(tmp_path)
        token = _token(matrix_sha=meta["matrix_sha256"])
        token["write_once"] = False
        (root / "freeze_token.json").write_text(json.dumps(token))
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(
                meta, freeze_token=token, artifact_root=root,
                matrix_relpath="matrix.bin",
                token_relpath="freeze_token.json")

    def test_ready_rejected_token_file_differs(self, tmp_path):
        """The in-envelope token must equal the on-disk token bytes."""
        root, meta, token = _staged(tmp_path)
        other = dict(token, issued_by="someone-else")
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(
                meta, freeze_token=other, artifact_root=root,
                matrix_relpath="matrix.bin",
                token_relpath="freeze_token.json")

    def test_dirty_main_matrix_path_fails_closed(self, tmp_path):
        """A matrix path resolving under the dirty checkout is refused."""
        root, meta, token = _staged(tmp_path)
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(
                meta, freeze_token=token, artifact_root=Path(DIRTY_MAIN),
                matrix_relpath="data/features_nepal_jja_2001_2026.csv",
                token_relpath="freeze_token.json",
                dirty_checkout_root=DIRTY_MAIN)

    def test_symlink_matrix_rejected(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        real = tmp_path / "real_matrix.bin"
        real.write_bytes(b"matrix-bytes")
        (root / "matrix.bin").unlink()
        (root / "matrix.bin").symlink_to(real)
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(
                meta, freeze_token=token, artifact_root=root,
                matrix_relpath="matrix.bin",
                token_relpath="freeze_token.json")

    def test_symlinked_parent_dir_rejected(self, tmp_path):
        """A symlinked intermediate directory must not smuggle the matrix
        out of the artifact root."""
        root, meta, token = _staged(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        real = outside / "matrix.bin"
        real.write_bytes(b"matrix-bytes")
        (root / "matrix.bin").unlink()
        (root / "linkdir").symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(
                meta, freeze_token=token, artifact_root=root,
                matrix_relpath="linkdir/matrix.bin",
                token_relpath="freeze_token.json")

    def test_traversal_relpath_rejected(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(
                meta, freeze_token=token, artifact_root=root,
                matrix_relpath="../matrix.bin",
                token_relpath="freeze_token.json")

    def test_forged_ready_status_rejected(self):
        env = fmx.build_fmx_envelope()
        env["fmx_status"] = fmx.FMX_READY
        ok, problems = fmx.verify_fmx_envelope(env)
        assert not ok


class TestSchema:
    def test_missing_matrix_sha_rejected(self):
        m = _matrix_meta()
        del m["matrix_sha256"]
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(m)  # schema still validated in blocked mode

    def test_invalid_unit_rejected(self):
        m = _matrix_meta()
        m["columns"][0]["unit"] = "ranked_priority_score"
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(m)

    def test_missing_missingness_rejected(self):
        m = _matrix_meta()
        del m["missingness"]
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(m)

    def test_absolute_path_rejected(self):
        m = _matrix_meta()
        m["source_path"] = "/Users/sanjayb/nepal-event-anomaly/data/x.csv"
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(m)

    def test_traversal_path_rejected(self):
        m = _matrix_meta()
        m["source_path"] = "../escape/x.csv"
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(m)

    def test_b_priority_column_rejected(self):
        m = _matrix_meta()
        m["columns"].append({"name": "b_priority", "unit": "index",
                             "role": "feature",
                             "aggregation": "none",
                             "temporal_resolution": "static",
                             "availability_time": "2015-01-01"})
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(m)

    def test_ranked_source_rejected(self):
        m = _matrix_meta()
        m["source_artifact_id"] = "b_screen_ranked"
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(m)

    def test_post_event_column_rejected(self):
        m = _matrix_meta()
        m["columns"].append({"name": "post_event_sar", "unit": "dB",
                             "role": "feature",
                             "aggregation": "post_event",
                             "temporal_resolution": "daily",
                             "availability_time": "2015-05-01"})
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(m)

    def test_envelope_self_hash(self):
        env = fmx.build_fmx_envelope()
        env["fmx_status"] = fmx.FMX_SCHEMA_VALID
        ok, _ = fmx.verify_fmx_envelope(env)
        assert not ok

    def test_status_constants(self):
        assert fmx.FMX_BLOCKED_PENDING_EXPLICIT_FREEZE == \
            "FMX_BLOCKED_PENDING_EXPLICIT_FREEZE"
        assert fmx.FMX_READY == "FMX_READY"
        assert fmx.FMX_SCHEMA_VALID == "FMX_SCHEMA_VALID"

    def test_profile_and_auth_fields_required_on_verify(self):
        """FMX-FREEZE-04: strict profile/authorization fields."""
        env = fmx.build_fmx_envelope()
        env["promotion_eligible"] = True
        env = fmx.bind_fmx_envelope(env)
        ok, problems = fmx.verify_fmx_envelope(env)
        assert not ok
