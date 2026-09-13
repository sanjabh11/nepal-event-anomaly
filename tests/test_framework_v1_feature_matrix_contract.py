"""W2 Feature-Matrix Contract (FMX) tests.

The dirty-main feature CSV/NetCDF is treated as absent: this module can only
emit FMX_BLOCKED_PENDING_EXPLICIT_FREEZE today.  FMX_READY requires a future
externally supplied write-once freeze token bound to exact matrix, producer,
feature-contract, and preregistration digests — and this module provides NO
token-generation path.
"""
from __future__ import annotations

import copy

import pytest

from nepal.framework_v1 import feature_matrix_contract as fmx


def _column(name="t2m_mean", unit="K", role="feature"):
    return {"name": name, "unit": unit, "role": role,
            "aggregation": "seasonal_mean",
            "temporal_resolution": "daily",
            "availability_time": "2015-04-01"}


def _matrix_meta():
    return {
        "matrix_id": "fmx-synthetic-001",
        "candidate_generation_id": "synthetic-gen-0",
        "source_artifact_id": "synthetic-source-1",
        "source_sha256": "ab" * 32,
        "byte_count": 12345,
        "producer_sha256": "cd" * 32,
        "feature_contract_sha256": "ef" * 32,
        "preregistration_sha256": "01" * 32,
        "matrix_sha256": "23" * 32,
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
            "feature_contract_sha256": "ef" * 32,
            "preregistration_sha256": "01" * 32,
            "write_once": True,
            "issued_by": "external-freeze-authority",
            "issued_at": "2026-09-13T00:00:00Z"}


class TestBlockedBoundary:
    def test_current_status_is_blocked_pending_freeze(self):
        env = fmx.build_fmx_envelope(_matrix_meta(), freeze_token=None)
        assert env["fmx_status"] == fmx.FMX_BLOCKED_PENDING_EXPLICIT_FREEZE
        ok, problems = fmx.verify_fmx_envelope(env)
        assert ok, problems

    def test_no_token_generation_path(self):
        assert not hasattr(fmx, "mint_freeze_token")
        assert not hasattr(fmx, "generate_freeze_token")
        assert not hasattr(fmx, "create_freeze_token")

    def test_dirty_matrix_never_read(self):
        """The builder takes metadata mappings only — no path argument can
        ever point at the dirty checkout because none is accepted."""
        import inspect
        sig = inspect.signature(fmx.build_fmx_envelope)
        assert "path" not in sig.parameters
        assert "matrix_path" not in sig.parameters


class TestReadyGate:
    def test_ready_with_valid_external_token(self):
        env = fmx.build_fmx_envelope(
            _matrix_meta(), freeze_token=_token())
        assert env["fmx_status"] == fmx.FMX_READY
        ok, problems = fmx.verify_fmx_envelope(env)
        assert ok, problems

    def test_ready_rejected_when_token_matrix_mismatch(self):
        env = _matrix_meta()
        token = _token(matrix_sha="ff" * 32)
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(env, freeze_token=token)

    def test_ready_rejected_when_token_producer_mismatch(self):
        token = _token(producer_sha="ff" * 32)
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(_matrix_meta(), freeze_token=token)

    def test_ready_rejected_when_token_not_write_once(self):
        token = _token()
        token["write_once"] = False
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(_matrix_meta(), freeze_token=token)

    def test_ready_rejected_when_token_missing_fields(self):
        token = _token()
        del token["feature_contract_sha256"]
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(_matrix_meta(), freeze_token=token)

    def test_verify_rejects_ready_with_mutated_token(self):
        env = fmx.build_fmx_envelope(_matrix_meta(), freeze_token=_token())
        env["freeze_token"]["matrix_sha256"] = "ee" * 32
        env = fmx.bind_fmx_envelope(
            {k: v for k, v in env.items() if k != "artifact_sha256"})
        ok, problems = fmx.verify_fmx_envelope(env)
        assert not ok

    def test_forged_ready_status_rejected(self):
        env = fmx.build_fmx_envelope(_matrix_meta(), freeze_token=None)
        env["fmx_status"] = fmx.FMX_READY
        ok, problems = fmx.verify_fmx_envelope(env)
        assert not ok


class TestSchema:
    def test_missing_matrix_sha_rejected(self):
        m = _matrix_meta()
        del m["matrix_sha256"]
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(m, freeze_token=_token())

    def test_invalid_unit_rejected(self):
        m = _matrix_meta()
        m["columns"][0]["unit"] = "ranked_priority_score"
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(m, freeze_token=_token())

    def test_missing_missingness_rejected(self):
        m = _matrix_meta()
        del m["missingness"]
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(m, freeze_token=_token())

    def test_absolute_path_rejected(self):
        m = _matrix_meta()
        m["source_path"] = "/Users/sanjayb/nepal-event-anomaly/data/x.csv"
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(m, freeze_token=_token())

    def test_traversal_path_rejected(self):
        m = _matrix_meta()
        m["source_path"] = "../escape/x.csv"
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(m, freeze_token=_token())

    def test_b_priority_column_rejected(self):
        m = _matrix_meta()
        m["columns"].append({"name": "b_priority", "unit": "index",
                             "role": "feature",
                             "aggregation": "none",
                             "temporal_resolution": "static",
                             "availability_time": "2015-01-01"})
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(m, freeze_token=_token())

    def test_ranked_source_rejected(self):
        m = _matrix_meta()
        m["source_artifact_id"] = "b_screen_ranked"
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(m, freeze_token=_token())

    def test_post_event_column_rejected(self):
        m = _matrix_meta()
        m["columns"].append({"name": "post_event_sar", "unit": "dB",
                             "role": "feature",
                             "aggregation": "post_event",
                             "temporal_resolution": "daily",
                             "availability_time": "2015-05-01"})
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(m, freeze_token=_token())

    def test_envelope_self_hash(self):
        env = fmx.build_fmx_envelope(_matrix_meta(), freeze_token=None)
        env["matrix"]["matrix_id"] = "mutated"
        ok, _ = fmx.verify_fmx_envelope(env)
        assert not ok

    def test_status_constants(self):
        assert fmx.FMX_BLOCKED_PENDING_EXPLICIT_FREEZE == \
            "FMX_BLOCKED_PENDING_EXPLICIT_FREEZE"
        assert fmx.FMX_READY == "FMX_READY"
        assert fmx.FMX_SCHEMA_VALID == "FMX_SCHEMA_VALID"
