"""W2/N1 Feature-Matrix Contract (FMX) tests — hardened boundary.

The dirty-main feature CSV/NetCDF is treated as absent: the schema-only /
fixture path can only emit FMX_BLOCKED_PENDING_EXPLICIT_FREEZE.  FMX_READY
requires a future externally supplied write-once freeze token bound to real
files on disk (matrix + token file under an external artifact root), with
SHA-256 and byte_count recomputed from those bytes, mandatory protected
roots, token authority fields, frozen lineage bindings, and a semantic
scan of the matrix bytes.  Pure in-memory token/matrix pairs can never
produce a verifiable READY.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from nepal.framework_v1 import feature_matrix_contract as fmx
from nepal.framework_v1.provenance import sha256_file

FC_FIELD = fmx.FEATURE_CONTRACT_SHA256_FIELD

DIRTY_MAIN = "/Users/sanjayb/nepal-event-anomaly"

# Lineage digest fields that must be bound to frozen files for FMX_READY.
_LINEAGE_FILES = {
    "producer_sha256": "producer.txt",
    FC_FIELD: "contract.txt",
    "preregistration_sha256": "prereg.txt",
    "source_sha256": "source.txt",
}


def _column(name="t2m_mean", unit="K", role="feature"):
    return {"name": name, "unit": unit, "role": role,
            "aggregation": "seasonal_mean",
            "temporal_resolution": "daily",
            "availability_time": "2015-04-01"}


def _columns():
    t2m = _column("t2m_mean", "K", "feature")
    t2m["min_value"] = 200.0
    t2m["max_value"] = 330.0
    return [
        {"name": "obs_date", "unit": "ISO8601", "role": "time",
         "aggregation": "none", "temporal_resolution": "daily",
         "availability_time": "2015-04-01"},
        t2m,
        _column("tp_total", "mm", "feature"),
        _column("synthetic_target", "count", "target"),
    ]


_CSV_HEADER = ("row_id,obs_date,lat,lon,t2m_mean,tp_total,"
               "synthetic_target,avail_date\n")
_CSV_ROW1 = ("row-001,2015-06-01,27.7,85.3,288.5,12.3,0,"
             "2015-06-05\n")
_CSV_TAIL = ("row-002,2015-06-02,27.8,85.4,289.1,0.0,1,2015-06-06\n"
             "row-003,2015-06-03,27.9,85.5,287.9,4.25,0,2015-06-07\n")


def _matrix_csv_text():
    return _CSV_HEADER + _CSV_ROW1 + _CSV_TAIL


def _matrix_meta(matrix_sha="23" * 32, byte_count=12345, digests=None):
    d = digests or {}
    return {
        "matrix_id": "fmx-synthetic-001",
        "candidate_generation_id": "synthetic-gen-0",
        "source_artifact_id": "synthetic-source-1",
        "source_sha256": d.get("source_sha256", "ab" * 32),
        "byte_count": byte_count,
        "producer_sha256": d.get("producer_sha256", "cd" * 32),
        FC_FIELD: d.get(FC_FIELD, "ef" * 32),
        "preregistration_sha256": d.get("preregistration_sha256",
                                        "01" * 32),
        "matrix_sha256": matrix_sha,
        "columns": _columns(),
        "missingness": {"fraction": 0.0, "policy": "complete"},
        "date_range": {"start": "2001-06-01", "end": "2015-08-31"},
        "spatial_coverage": {"region_id": "synthetic-region",
                             "n_units": 4},
        "target_spec": {"name": "synthetic_target",
                        "definition": "fixture"},
        "data_source_status": "SYNTHETIC_FIXTURE",
        "row_id_column": "row_id",
        "n_rows": 3,
        "coordinate_columns": {"lat": "lat", "lon": "lon"},
        "feature_cutoff": "2015-08-31",
        "availability_column": "avail_date",
    }


def _token(matrix_sha="23" * 32, digests=None):
    d = digests or {}
    return {"token_type": fmx.FREEZE_TOKEN_TYPE,
            "matrix_sha256": matrix_sha,
            "producer_sha256": d.get("producer_sha256", "cd" * 32),
            FC_FIELD: d.get(FC_FIELD, "ef" * 32),
            "preregistration_sha256": d.get("preregistration_sha256",
                                            "01" * 32),
            "write_once": True,
            "issued_by": "external-freeze-authority",
            "issued_at": "2026-09-13T00:00:00Z",
            "approved_by": "freeze-governance-board",
            "approved_at": "2026-09-12",
            "approval_record_sha256": "9a" * 32,
            "immutable_storage_evidence": "worm-store-record-001"}


def _token_for(meta, **overrides):
    token = _token(matrix_sha=meta["matrix_sha256"], digests=meta)
    token.update(overrides)
    return token


def _lineage_paths():
    return dict(_LINEAGE_FILES)


def _protected(tmp_path):
    p = tmp_path / "protected"
    p.mkdir(exist_ok=True)
    return p


def _staged(tmp_path, matrix_bytes=None, token=None):
    """Stage a real matrix CSV + token file + frozen lineage files under
    an external artifact root, with digests computed from disk."""
    root = tmp_path / "ext-root"
    root.mkdir()
    digests = {}
    for field, name in _LINEAGE_FILES.items():
        p = root / name
        p.write_bytes(f"frozen-lineage:{name}\n".encode("utf-8"))
        digests[field] = sha256_file(p)
    mbytes = matrix_bytes if matrix_bytes is not None else \
        _matrix_csv_text().encode("utf-8")
    (root / "matrix.csv").write_bytes(mbytes)
    meta = _matrix_meta(matrix_sha=sha256_file(root / "matrix.csv"),
                        byte_count=len(mbytes), digests=digests)
    if token is None:
        token = _token_for(meta)
    (root / "freeze_token.json").write_text(json.dumps(token))
    return root, meta, token


def _build_ready(meta, token, root, tmp_path, **kw):
    kw.setdefault("protected_roots", [_protected(tmp_path)])
    kw.setdefault("lineage_paths", _lineage_paths())
    kw.setdefault("matrix_relpath", "matrix.csv")
    kw.setdefault("token_relpath", "freeze_token.json")
    return fmx.build_fmx_envelope(
        meta, freeze_token=token, artifact_root=root, **kw)


def _verify_ready(env, root, tmp_path, **kw):
    kw.setdefault("protected_roots", [_protected(tmp_path)])
    return fmx.verify_fmx_envelope(env, artifact_root=root, **kw)


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

    def test_blocked_anti_confusion_fields(self):
        """PKG-08: a blocked envelope must explicitly mark itself as a
        schema fixture — never a frozen artifact."""
        env = fmx.build_fmx_envelope(_matrix_meta())
        assert env["schema_fixture"] is True
        assert env["external_freeze"] is False
        assert env["artifact_present"] is False
        assert env["not_a_real_matrix"] is True
        ok, problems = fmx.verify_fmx_envelope(env)
        assert ok, problems

    @pytest.mark.parametrize("field", (
        "schema_fixture", "external_freeze", "artifact_present",
        "not_a_real_matrix"))
    def test_blocked_missing_anti_confusion_field_rejected(self, field):
        env = fmx.build_fmx_envelope()
        del env[field]
        env = fmx.bind_fmx_envelope(env)
        ok, problems = fmx.verify_fmx_envelope(env)
        assert not ok

    @pytest.mark.parametrize("field,value", (
        ("schema_fixture", False), ("external_freeze", True),
        ("artifact_present", True), ("not_a_real_matrix", False)))
    def test_blocked_wrong_anti_confusion_value_rejected(
            self, field, value):
        env = fmx.build_fmx_envelope()
        env[field] = value
        env = fmx.bind_fmx_envelope(env)
        ok, problems = fmx.verify_fmx_envelope(env)
        assert not ok

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
        env = _build_ready(meta, token, root, tmp_path)
        assert env["fmx_status"] == fmx.FMX_READY
        assert env["file_bindings"]["lineage"] == _lineage_paths()
        ok, problems = _verify_ready(env, root, tmp_path)
        assert ok, problems

    def test_ready_verify_without_root_fails(self, tmp_path):
        """A READY envelope cannot verify without on-disk evidence."""
        root, meta, token = _staged(tmp_path)
        env = _build_ready(meta, token, root, tmp_path)
        ok, problems = fmx.verify_fmx_envelope(env)  # no artifact_root
        assert not ok
        assert any("file" in p or "root" in p for p in problems)

    def test_ready_rejected_matrix_mismatch(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        (root / "matrix.csv").write_bytes(b"tampered")
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_ready_rejected_token_matrix_mismatch(self, tmp_path):
        root, meta, _ = _staged(tmp_path)
        bad = _token_for(meta, matrix_sha256="ff" * 32)
        (root / "freeze_token.json").write_text(json.dumps(bad))
        with pytest.raises(ValueError):
            _build_ready(meta, bad, root, tmp_path)

    def test_ready_rejected_when_token_not_write_once(self, tmp_path):
        root, meta, _ = _staged(tmp_path)
        token = _token_for(meta, write_once=False)
        (root / "freeze_token.json").write_text(json.dumps(token))
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_ready_rejected_token_file_differs(self, tmp_path):
        """The in-envelope token must equal the on-disk token bytes."""
        root, meta, token = _staged(tmp_path)
        other = dict(token, issued_by="someone-else")
        with pytest.raises(ValueError):
            _build_ready(meta, other, root, tmp_path)

    def test_dirty_main_matrix_path_fails_closed(self, tmp_path):
        """A matrix path resolving under the dirty checkout is refused."""
        root, meta, token = _staged(tmp_path)
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(
                meta, freeze_token=token, artifact_root=Path(DIRTY_MAIN),
                matrix_relpath="data/features_nepal_jja_2001_2026.csv",
                token_relpath="freeze_token.json",
                dirty_checkout_root=DIRTY_MAIN,
                lineage_paths=_lineage_paths())

    def test_symlink_matrix_rejected(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        real = tmp_path / "real_matrix.csv"
        real.write_bytes(_matrix_csv_text().encode("utf-8"))
        (root / "matrix.csv").unlink()
        (root / "matrix.csv").symlink_to(real)
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_symlinked_parent_dir_rejected(self, tmp_path):
        """A symlinked intermediate directory must not smuggle the matrix
        out of the artifact root."""
        root, meta, token = _staged(tmp_path)
        outside = tmp_path / "outside"
        outside.mkdir()
        real = outside / "matrix.csv"
        real.write_bytes(_matrix_csv_text().encode("utf-8"))
        (root / "linkdir").symlink_to(outside, target_is_directory=True)
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path,
                         matrix_relpath="linkdir/matrix.csv")

    def test_traversal_relpath_rejected(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path,
                         matrix_relpath="../matrix.csv")

    def test_forged_ready_status_rejected(self):
        env = fmx.build_fmx_envelope()
        env["fmx_status"] = fmx.FMX_READY
        ok, problems = fmx.verify_fmx_envelope(env)
        assert not ok


class TestProtectedRoots:
    """FMX-02: protected roots are mandatory for READY build+verify."""

    def test_ready_requires_protected_roots(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(
                meta, freeze_token=token, artifact_root=root,
                matrix_relpath="matrix.csv",
                token_relpath="freeze_token.json",
                lineage_paths=_lineage_paths())  # no protected roots

    def test_artifact_root_under_protected_root_rejected(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(
                meta, freeze_token=token, artifact_root=root,
                matrix_relpath="matrix.csv",
                token_relpath="freeze_token.json",
                protected_roots=[tmp_path],  # contains ext-root
                lineage_paths=_lineage_paths())

    def test_bound_file_under_protected_root_rejected(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        zone = root / "frozen-zone"
        zone.mkdir()
        (root / "freeze_token.json").replace(zone / "freeze_token.json")
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(
                meta, freeze_token=token, artifact_root=root,
                matrix_relpath="matrix.csv",
                token_relpath="frozen-zone/freeze_token.json",
                protected_roots=[zone],
                lineage_paths=_lineage_paths())

    def test_verify_ready_requires_artifact_root_and_protected(
            self, tmp_path):
        root, meta, token = _staged(tmp_path)
        env = _build_ready(meta, token, root, tmp_path)
        ok, problems = fmx.verify_fmx_envelope(env)
        assert not ok
        ok, problems = fmx.verify_fmx_envelope(env, artifact_root=root)
        assert not ok  # protected_roots missing
        ok, problems = fmx.verify_fmx_envelope(
            env, protected_roots=[_protected(tmp_path)])
        assert not ok  # artifact_root missing

    def test_verify_ready_rejects_protected_violation(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        env = _build_ready(meta, token, root, tmp_path)
        ok, problems = fmx.verify_fmx_envelope(
            env, artifact_root=root, protected_roots=[root])
        assert not ok


class TestTokenAuthorityFields:
    """FMX-03: write_once + issued_by are necessary but not sufficient."""

    @pytest.mark.parametrize("field", (
        "approved_by", "approved_at", "approval_record_sha256",
        "immutable_storage_evidence"))
    def test_missing_authority_field_rejected(self, tmp_path, field):
        root, meta, token = _staged(tmp_path)
        del token[field]
        (root / "freeze_token.json").write_text(json.dumps(token))
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    @pytest.mark.parametrize("field,value", (
        ("approved_by", ""),
        ("approved_at", "2026-02-31"),       # not a real calendar date
        ("approved_at", "13-09-2026"),       # not ISO Y-M-D
        ("approval_record_sha256", "not-a-sha"),
        ("approval_record_sha256", "AB" * 32),  # uppercase
        ("immutable_storage_evidence", "")))
    def test_invalid_authority_field_rejected(self, tmp_path, field, value):
        root, meta, token = _staged(tmp_path)
        token[field] = value
        (root / "freeze_token.json").write_text(json.dumps(token))
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)


class TestLineageBinding:
    """FMX-04: lineage digest fields must be bound to frozen files."""

    def test_lineage_paths_required(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        with pytest.raises(ValueError):
            fmx.build_fmx_envelope(
                meta, freeze_token=token, artifact_root=root,
                matrix_relpath="matrix.csv",
                token_relpath="freeze_token.json",
                protected_roots=[_protected(tmp_path)])

    @pytest.mark.parametrize("field", sorted(_LINEAGE_FILES))
    def test_lineage_missing_field_rejected(self, tmp_path, field):
        root, meta, token = _staged(tmp_path)
        lp = _lineage_paths()
        del lp[field]
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path, lineage_paths=lp)

    def test_lineage_digest_mismatch_rejected(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        (root / "source.txt").write_bytes(b"different-bytes")
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_lineage_traversal_rejected(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        lp = _lineage_paths()
        lp["source_sha256"] = "../source.txt"
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path, lineage_paths=lp)

    def test_verify_rechecks_lineage(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        env = _build_ready(meta, token, root, tmp_path)
        (root / "producer.txt").write_bytes(b"tampered")
        ok, problems = _verify_ready(env, root, tmp_path)
        assert not ok


class TestMatrixSemanticScan:
    """FMX-05: the bound matrix bytes are semantically scanned."""

    def _stage(self, tmp_path, text):
        return _staged(tmp_path, matrix_bytes=text.encode("utf-8"))

    def test_csv_missing_declared_column_rejected(self, tmp_path):
        root, meta, token = self._stage(
            tmp_path, "obs_date,t2m_mean\n2015-06-01,288.5\n")
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_csv_non_finite_feature_rejected(self, tmp_path):
        root, meta, token = self._stage(
            tmp_path,
            _CSV_HEADER +
            "row-001,2015-06-01,27.7,85.3,NaN,1.0,0,2015-06-05\n" +
            _CSV_TAIL)
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_csv_infinite_feature_rejected(self, tmp_path):
        root, meta, token = self._stage(
            tmp_path,
            _CSV_HEADER +
            "row-001,2015-06-01,27.7,85.3,288.5,inf,0,2015-06-05\n" +
            _CSV_TAIL)
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_csv_non_numeric_feature_rejected(self, tmp_path):
        root, meta, token = self._stage(
            tmp_path,
            _CSV_HEADER +
            "row-001,2015-06-01,27.7,85.3,not-a-number,1.0,0,"
            "2015-06-05\n" + _CSV_TAIL)
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    @pytest.mark.parametrize("bad_col",
                             ("loo_score", "priority_index",
                              "top_five_flag", "site_rank"))
    def test_csv_b_derived_column_rejected(self, tmp_path, bad_col):
        root, meta, token = self._stage(
            tmp_path,
            "row_id,obs_date,lat,lon,t2m_mean,tp_total,synthetic_target,"
            "avail_date," + bad_col + "\n"
            "row-001,2015-06-01,27.7,85.3,288.5,1.0,0,2015-06-05,9\n"
            "row-002,2015-06-02,27.8,85.4,289.1,0.0,1,2015-06-06,9\n"
            "row-003,2015-06-03,27.9,85.5,287.9,4.25,0,2015-06-07,9\n")
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_csv_date_out_of_range_rejected(self, tmp_path):
        root, meta, token = self._stage(
            tmp_path,
            _CSV_HEADER +
            "row-001,2020-01-01,27.7,85.3,288.5,1.0,0,2015-06-05\n" +
            _CSV_TAIL)
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_csv_invalid_calendar_date_rejected(self, tmp_path):
        root, meta, token = self._stage(
            tmp_path,
            _CSV_HEADER +
            "row-001,2015-02-31,27.7,85.3,288.5,1.0,0,2015-06-05\n" +
            _CSV_TAIL)
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_csv_no_data_rows_rejected(self, tmp_path):
        root, meta, token = self._stage(tmp_path, _CSV_HEADER)
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_csv_missing_cell_complete_policy_rejected(self, tmp_path):
        root, meta, token = self._stage(
            tmp_path,
            _CSV_HEADER +
            "row-001,2015-06-01,27.7,85.3,288.5,,0,2015-06-05\n" +
            _CSV_TAIL)
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_unsupported_matrix_format_rejected(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        (root / "matrix.csv").rename(root / "matrix.bin")
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path,
                         matrix_relpath="matrix.bin")


class TestMatrixRowSemantics:
    """FMX-04: deeper CSV row semantics on the READY path."""

    def _stage(self, tmp_path, text):
        return _staged(tmp_path, matrix_bytes=text.encode("utf-8"))

    def test_duplicate_row_id_rejected(self, tmp_path):
        root, meta, token = self._stage(
            tmp_path,
            _CSV_HEADER +
            "row-001,2015-06-01,27.7,85.3,288.5,1.0,0,2015-06-05\n"
            "row-001,2015-06-02,27.8,85.4,289.1,0.0,1,2015-06-06\n"
            "row-003,2015-06-03,27.9,85.5,287.9,4.25,0,2015-06-07\n")
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_empty_row_id_rejected(self, tmp_path):
        root, meta, token = self._stage(
            tmp_path,
            _CSV_HEADER +
            ",2015-06-01,27.7,85.3,288.5,1.0,0,2015-06-05\n" +
            _CSV_TAIL)
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_row_id_column_missing_from_csv_rejected(self, tmp_path):
        root, meta, token = self._stage(
            tmp_path,
            "obs_date,lat,lon,t2m_mean,tp_total,synthetic_target,"
            "avail_date\n"
            "2015-06-01,27.7,85.3,288.5,1.0,0,2015-06-05\n"
            "2015-06-02,27.8,85.4,289.1,0.0,1,2015-06-06\n"
            "2015-06-03,27.9,85.5,287.9,4.25,0,2015-06-07\n")
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_n_rows_mismatch_rejected(self, tmp_path):
        root, meta, token = self._stage(
            tmp_path,
            _CSV_HEADER +
            "row-001,2015-06-01,27.7,85.3,288.5,1.0,0,2015-06-05\n"
            "row-002,2015-06-02,27.8,85.4,289.1,0.0,1,2015-06-06\n")
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_n_rows_declared_wrong_rejected(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        meta["n_rows"] = 4
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    @pytest.mark.parametrize("col_i,value", (
        (2, "95.0"), (2, "-91.0"), (3, "200.5"), (3, "-180.1"),
        (2, "not-a-coordinate")))
    def test_coordinate_out_of_range_rejected(self, tmp_path, col_i,
                                              value):
        cells = ["row-001", "2015-06-01", "27.7", "85.3", "288.5",
                 "1.0", "0", "2015-06-05"]
        cells[col_i] = value
        root, meta, token = self._stage(
            tmp_path, _CSV_HEADER + ",".join(cells) + "\n" + _CSV_TAIL)
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_coordinate_column_missing_from_csv_rejected(self, tmp_path):
        root, meta, token = self._stage(
            tmp_path,
            "row_id,obs_date,t2m_mean,tp_total,synthetic_target,"
            "avail_date\n"
            "row-001,2015-06-01,288.5,1.0,0,2015-06-05\n"
            "row-002,2015-06-02,289.1,0.0,1,2015-06-06\n"
            "row-003,2015-06-03,287.9,4.25,0,2015-06-07\n")
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_declared_max_bound_violation_rejected(self, tmp_path):
        """t2m_mean declares min_value=200 / max_value=330 in meta."""
        root, meta, token = self._stage(
            tmp_path,
            _CSV_HEADER +
            "row-001,2015-06-01,27.7,85.3,450.0,1.0,0,2015-06-05\n" +
            _CSV_TAIL)
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_declared_min_bound_violation_rejected(self, tmp_path):
        root, meta, token = self._stage(
            tmp_path,
            _CSV_HEADER +
            "row-001,2015-06-01,27.7,85.3,150.0,1.0,0,2015-06-05\n" +
            _CSV_TAIL)
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)


class TestTemporalCutoff:
    """FMX-05: feature_cutoff binds availability declarations."""

    def _stage(self, tmp_path, text):
        return _staged(tmp_path, matrix_bytes=text.encode("utf-8"))

    def test_availability_after_cutoff_rejected(self, tmp_path):
        root, meta, token = self._stage(
            tmp_path,
            _CSV_HEADER +
            "row-001,2015-06-01,27.7,85.3,288.5,1.0,0,2015-09-01\n" +
            _CSV_TAIL)
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_availability_not_a_date_rejected(self, tmp_path):
        root, meta, token = self._stage(
            tmp_path,
            _CSV_HEADER +
            "row-001,2015-06-01,27.7,85.3,288.5,1.0,0,soon\n" +
            _CSV_TAIL)
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_availability_column_missing_from_csv_rejected(self, tmp_path):
        root, meta, token = self._stage(
            tmp_path,
            "row_id,obs_date,lat,lon,t2m_mean,tp_total,synthetic_target\n"
            "row-001,2015-06-01,27.7,85.3,288.5,1.0,0\n"
            "row-002,2015-06-02,27.8,85.4,289.1,0.0,1\n"
            "row-003,2015-06-03,27.9,85.5,287.9,4.25,0\n")
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_column_availability_time_after_cutoff_rejected(
            self, tmp_path):
        root, meta, token = _staged(tmp_path)
        meta["columns"][1]["availability_time"] = "2016-01-01"
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)

    def test_invalid_feature_cutoff_rejected(self, tmp_path):
        root, meta, token = _staged(tmp_path)
        meta["feature_cutoff"] = "2015-13-40"
        with pytest.raises(ValueError):
            _build_ready(meta, token, root, tmp_path)


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
