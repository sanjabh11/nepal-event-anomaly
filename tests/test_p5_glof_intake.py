"""Real-intake lane tests — byte-bound P5 intake (Lane A).

The contract lane (``tests/test_glof_poc_contract.py``) proves the
source-intake interfaces on shape probes; this lane proves the
*real-data* acceptance evidence the P5 phase demands:

* byte-bound manifests built from REAL temp bytes (digest equals the
  file's actual sha256 — never a fake hex constant);
* verifier-BEFORE-parser ordering, proven with call-order spies;
* forged manifests, fake roots, missing files, leaf/intermediate
  symlinks, digest mismatches, post-hash replacement, duplicate
  headers, malformed timestamps, unknown basins, and invalid maps
  all reject with a bounded ``ValueError`` — never an uncaught
  exception;
* the blocked-authorization posture: a fixture manifest can never
  pass as real evidence, and intake performs NO network access.

All fixtures are synthetic; no real HMAGLOFDB bytes are involved
(``P5_BLOCKED_NO_AUTHORIZATION`` stands) and no source status changes.
"""
from __future__ import annotations

import csv
import hashlib
import socket
from pathlib import Path

import pytest

from nepal.research_v0.records import MECHANISM_IDS
from nepal.research_v0.source_intake import (
    build_source_manifest, load_hmaglofdb_rows)
from nepal.science_v0.events import BASIN_UNIVERSE, SourceRow

_SOURCE_ID = "hmaglofdb"
_SOURCE_VERSION = "1.3.0"

_CSV_HEADER = ("key", "basin", "t0", "t1", "prec", "mech",
               "cg", "parent", "obs")
_COLUMN_MAP = {
    "source_row_key": "key", "basin": "basin",
    "interval_start": "t0", "interval_end": "t1",
    "declared_precision": "prec", "mechanism": "mech",
    "cascade_group_id": "cg", "parent_source_row_key": "parent",
    "observed_on": "obs"}
_REQUIRED_COLUMN_MAP = {
    k: v for k, v in _COLUMN_MAP.items()
    if k not in ("cascade_group_id", "parent_source_row_key",
                 "observed_on")}

#: 9 rows / 8 basins / 4+ groups — honest precision mix.
_BASE_ROWS = [
    ("r1", "koshi", "2020-06-01T00:00:00Z", "2020-06-02T00:00:00Z",
     "day", "lake_outburst", "cg-koshi-1", "", ""),
    ("r2", "koshi", "2020-07-10T00:00:00Z", "2020-07-11T00:00:00Z",
     "day", "lake_outburst", "cg-koshi-1", "r1",
     "2020-07-15T00:00:00Z"),
    ("r3", "bagmati", "2020-06-10T00:00:00Z", "2020-06-11T00:00:00Z",
     "day", "lake_outburst", "", "", ""),
    ("r4", "gandaki", "2020-08-05T00:00:00Z", "2020-08-06T00:00:00Z",
     "day", "lake_outburst", "", "", ""),
    ("r5", "karnali", "2020-09-01T00:00:00Z", "2020-09-02T00:00:00Z",
     "day", "lake_outburst", "", "", ""),
    ("r6", "dudh_koshi", "2020-07-01T00:00:00Z",
     "2020-08-01T00:00:00Z", "month", "lake_outburst", "", "", ""),
    ("r7", "mahakali", "2020-05-01T00:00:00Z",
     "2020-05-31T00:00:00Z", "interval_8_30d", "lake_outburst",
     "", "", ""),
    ("r8", "arun", "2020-06-15T00:00:00Z", "2020-06-18T00:00:00Z",
     "interval_3d", "lake_outburst", "", "", ""),
    ("r9", "seti", "2020-01-01T00:00:00Z", "2021-01-01T00:00:00Z",
     "year", "lake_outburst", "", "", ""),
]

_GROUP_OF_BASIN = {
    "koshi": "grp_east", "dudh_koshi": "grp_east",
    "bagmati": "grp_central", "gandaki": "grp_west",
    "karnali": "grp_north", "arun": "grp_north",
    "mahakali": "grp_farwest", "seti": "grp_farwest"}

# ------------------------------------------------------------- helpers

def _write_csv(root, rows, name="events.csv"):
    path = root / name
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.writer(fh)
        writer.writerow(_CSV_HEADER)
        writer.writerows(rows)
    return path


def _bind(root: Path, path: Path):
    return build_source_manifest(
        root, source_id=_SOURCE_ID, source_version=_SOURCE_VERSION,
        source_files=[{"relpath": str(path.relative_to(root)),
                       "sha256": hashlib.sha256(
                           path.read_bytes()).hexdigest()}],
        units=list(_GROUP_OF_BASIN),
        feature_allowlist=["f1", "f2"],
        lineage="intake-lane acquired bytes")


def _real_intake(tmp_path, rows=None):
    root = tmp_path / "evidence"
    root.mkdir()
    path = _write_csv(root, _BASE_ROWS if rows is None else rows)
    return root, path, _bind(root, path)


# ------------------------------------------------------- 1. byte binding

class TestRealByteManifest:
    def test_manifest_binds_actual_file_bytes(self, tmp_path):
        root, path, manifest = _real_intake(tmp_path)
        real = hashlib.sha256(path.read_bytes()).hexdigest()
        assert manifest["evidence_root"] == str(root)
        assert manifest["source_files"][0]["sha256"] == real
        assert manifest["source_digests"] == [real]
        assert manifest["lineage"].startswith(
            f"source_version={_SOURCE_VERSION};")

    def test_load_succeeds_on_verified_bytes(self, tmp_path):
        root, path, manifest = _real_intake(tmp_path)
        rows = load_hmaglofdb_rows(
            path, source_manifest=manifest,
            column_map=dict(_COLUMN_MAP))
        assert len(rows) == len(_BASE_ROWS)
        assert all(r.source_id == _SOURCE_ID for r in rows)
        assert all(r.source_version == _SOURCE_VERSION
                   for r in rows)
        assert all(r.basin in BASIN_UNIVERSE for r in rows)
        assert all(r.mechanism in MECHANISM_IDS for r in rows)


# ------------------------------------------- 2. verifier-before-parser

class TestVerifierBeforeParser:
    @pytest.fixture
    def order_spy(self, monkeypatch):
        import nepal.research_v0.source_intake as si
        order = []
        real_verify = si.verify_source_evidence
        real_read = si.read_evidence_file

        def verify_spy(manifest):
            order.append("verify")
            return real_verify(manifest)

        def read_spy(root, rel, label=""):
            order.append("read")
            return real_read(root, rel, label=label)

        monkeypatch.setattr(si, "verify_source_evidence", verify_spy)
        monkeypatch.setattr(si, "read_evidence_file", read_spy)
        return order

    def test_verify_runs_before_any_byte_is_read(self, tmp_path,
                                                 order_spy):
        root, path, manifest = _real_intake(tmp_path)
        load_hmaglofdb_rows(path, source_manifest=manifest,
                            column_map=dict(_COLUMN_MAP))
        assert order_spy == ["verify", "read"], order_spy

    def test_no_parse_after_failed_verification(self, tmp_path,
                                                order_spy):
        root, path, manifest = _real_intake(tmp_path)
        with open(path, "ab") as fh:
            fh.write(b"tamper")
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(path, source_manifest=manifest,
                                column_map=dict(_COLUMN_MAP))
        assert "read" not in order_spy, order_spy

# ------------------------------------------------- 3. unauthorized bytes

class TestUnauthorizedBytes:
    def test_missing_source_file_rejected(self, tmp_path):
        root, path, manifest = _real_intake(tmp_path)
        path.unlink()
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(path, source_manifest=manifest,
                                column_map=dict(_COLUMN_MAP))

    def test_fake_evidence_root_rejected(self, tmp_path):
        root, path, manifest = _real_intake(tmp_path)
        vanished = tmp_path / "evidence-renamed"
        root.rename(vanished)
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(vanished / "events.csv",
                                source_manifest=manifest,
                                column_map=dict(_COLUMN_MAP))

    def test_leaf_symlink_rejected(self, tmp_path):
        root, path, manifest = _real_intake(tmp_path)
        alias = root / "alias.csv"
        alias.symlink_to(path)
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(alias, source_manifest=manifest,
                                column_map=dict(_COLUMN_MAP))

    def test_intermediate_symlink_rejected(self, tmp_path):
        root, path, manifest = _real_intake(tmp_path)
        deep = root / "sub"
        real = tmp_path / "real-dir"
        real.mkdir()
        deep.symlink_to(real)
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(deep / "events.csv",
                                source_manifest=manifest,
                                column_map=dict(_COLUMN_MAP))

    def test_digest_mismatch_rejected(self, tmp_path):
        root, path, manifest = _real_intake(tmp_path)
        forged = dict(manifest, source_files=[{
            "relpath": "events.csv", "sha256": "a" * 64}])
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(path, source_manifest=forged,
                                column_map=dict(_COLUMN_MAP))

    def test_post_hash_replacement_rejected(self, tmp_path):
        root, path, manifest = _real_intake(tmp_path)
        # The declared bytes verify once...
        load_hmaglofdb_rows(path, source_manifest=manifest,
                            column_map=dict(_COLUMN_MAP))
        # ...then the file is replaced under the same name: the
        # manifest still binds the ORIGINAL bytes, so the swap
        # rejects — a post-hash replacement is never an artifact.
        _write_csv(root, _BASE_ROWS[:-1])
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(path, source_manifest=manifest,
                                column_map=dict(_COLUMN_MAP))

    def test_no_network_access_during_intake(self, tmp_path,
                                             monkeypatch):
        def _forbidden(*a, **k):
            raise AssertionError("network access during intake")

        monkeypatch.setattr(socket, "socket", _forbidden)
        root, path, manifest = _real_intake(tmp_path)
        rows = load_hmaglofdb_rows(path, source_manifest=manifest,
                                   column_map=dict(_COLUMN_MAP))
        assert len(rows) == len(_BASE_ROWS)

# --------------------------------------------- 4. header and map integrity

class TestHeaderAndMapIntegrity:
    def test_duplicate_csv_headers_rejected(self, tmp_path):
        root = tmp_path / "ev"
        root.mkdir()
        text = ",".join(_CSV_HEADER + ("t1",)) + "\n" + \
            ",".join(_BASE_ROWS[0] + ("x",)) + "\n"
        path = root / "events.csv"
        path.write_text(text, encoding="utf-8")
        manifest = _bind(root, path)
        with pytest.raises(ValueError, match="duplicate"):
            load_hmaglofdb_rows(path, source_manifest=manifest,
                                column_map=dict(_COLUMN_MAP))

    def test_duplicate_source_row_keys_rejected(self, tmp_path):
        root, path, manifest = _real_intake(
            tmp_path, rows=[_BASE_ROWS[0], _BASE_ROWS[0]])
        with pytest.raises(ValueError, match="duplicate"):
            load_hmaglofdb_rows(path, source_manifest=manifest,
                                column_map=dict(_COLUMN_MAP))

    @pytest.mark.parametrize("cmap", (
        {}, "not-a-map", None,
        dict(_REQUIRED_COLUMN_MAP, mechanism=""),
        dict(_REQUIRED_COLUMN_MAP, basin=7),
        dict(_REQUIRED_COLUMN_MAP, t0="t0"),          # unknown key
        dict(_REQUIRED_COLUMN_MAP, source_row_key="basin"),))
    def test_invalid_column_maps_rejected(self, tmp_path, cmap):
        root, path, manifest = _real_intake(tmp_path)
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(path, source_manifest=manifest,
                                column_map=cmap)

    def test_two_semantics_sharing_one_header_rejected(self, tmp_path):
        root, path, manifest = _real_intake(tmp_path)
        cmap = dict(_REQUIRED_COLUMN_MAP, cascade_group_id="key")
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(path, source_manifest=manifest,
                                column_map=cmap)

    def test_fixture_manifest_never_passes_as_real_evidence(
            self, tmp_path):
        root, path, _ = _real_intake(tmp_path)
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(path, source_manifest={"fixture": True},
                                column_map=dict(_COLUMN_MAP))

    def test_extra_manifest_key_rejected(self, tmp_path):
        root, path, manifest = _real_intake(tmp_path)
        forged = dict(manifest, extra_key="bypass")
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(path, source_manifest=forged,
                                column_map=dict(_COLUMN_MAP))

    def test_loader_returns_typed_rows(self, tmp_path):
        root, path, manifest = _real_intake(tmp_path)
        rows = load_hmaglofdb_rows(path, source_manifest=manifest,
                                   column_map=dict(_COLUMN_MAP))
        assert isinstance(rows, tuple)
        assert all(isinstance(r, SourceRow) for r in rows)


# ----------------------------------------------- 5. timestamps and semantics

class TestTimestampsAndSemantics:
    def _row(self, **over):
        base = dict(zip(
            ("key", "basin", "t0", "t1", "prec", "mech",
             "cg", "parent", "obs"), _BASE_ROWS[2]))
        base.update(over)
        return [tuple(base[k] for k in
                      ("key", "basin", "t0", "t1", "prec",
                       "mech", "cg", "parent", "obs"))]

    def _assert_reject(self, tmp_path, **over):
        root, path, manifest = _real_intake(
            tmp_path, rows=self._row(**over))
        with pytest.raises(ValueError):
            load_hmaglofdb_rows(path, source_manifest=manifest,
                                column_map=dict(_COLUMN_MAP))

    def test_naive_timestamps_rejected(self, tmp_path):
        self._assert_reject(
            tmp_path, t0="2020-06-10T00:00:00",
            t1="2020-06-11T00:00:00")

    def test_nonexistent_dates_rejected(self, tmp_path):
        self._assert_reject(
            tmp_path, t0="2020-02-30T00:00:00Z",
            t1="2020-03-02T00:00:00Z")

    def test_reversed_intervals_rejected(self, tmp_path):
        self._assert_reject(
            tmp_path, t0="2020-06-11T00:00:00Z",
            t1="2020-06-10T00:00:00Z")

    def test_unknown_basin_rejected(self, tmp_path):
        self._assert_reject(tmp_path, basin="atlantis")

    def test_basin_outside_declared_units_rejected(self, tmp_path):
        # tamor is in the universe but not in THIS manifest's units —
        # the manifest binds the loaded unit universe.
        self._assert_reject(tmp_path, basin="tamor")

    def test_unknown_mechanism_rejected(self, tmp_path):
        self._assert_reject(tmp_path, mech="mystery_burst")

    def test_unknown_precision_rejected(self, tmp_path):
        self._assert_reject(tmp_path, prec="fortnight")

    def test_month_precision_stays_honest(self, tmp_path):
        """A month-declared window is preserved verbatim — never
        silently converted into an exact date (finding 13)."""
        root, path, manifest = _real_intake(tmp_path)
        rows = load_hmaglofdb_rows(path, source_manifest=manifest,
                                   column_map=dict(_COLUMN_MAP))
        month_row = next(r for r in rows
                         if r.declared_precision == "month")
        assert month_row.interval_start == "2020-07-01T00:00:00Z"
        assert month_row.interval_end == "2020-08-01T00:00:00Z"



