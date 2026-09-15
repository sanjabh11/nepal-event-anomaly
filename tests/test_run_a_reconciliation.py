"""Run A reconciliation contract tests (audit gaps H04–H07, H10).

Covers: valid-calendar-day request construction, recursive claim-scan,
ledger size/timestamp repair on a synthetic run root, and canonical
JSON helpers.  All inputs are synthetic fixtures — no real data.
"""
from __future__ import annotations

import calendar
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pytest

from nepal.era5_hybrid_fetch import (sd_sf_chunks, stage_size_accounting,
                                     utc_now_iso, _month_day_groups)
from nepal.era5_download import days_for_month
from nepal.run_ledger_repair import repair_ledger, write_provenance_receipts
from nepal.research_v0.cli import build_parser


def _claim_scan(target: str) -> int:
    args = build_parser().parse_args(["claim-scan", target])
    return args.func(args)


class TestValidDayRequests:
    """H05 — every requested day must exist in every requested month."""

    def _all_cross_products_valid(self, chunk) -> bool:
        return all(
            d in days_for_month(y, m)
            for y in chunk["years"] for m in chunk["months"]
            for d in chunk["days"])

    def test_all_chunks_valid_days(self):
        years = list(range(2001, 2027))
        for chunk in sd_sf_chunks(years):
            assert self._all_cross_products_valid(chunk), \
                f"invalid days in chunk {chunk['tag']}"

    def test_june_never_requests_day_31(self):
        for chunk in sd_sf_chunks([2001, 2024]):
            if "06" in chunk["months"]:
                assert "31" not in chunk["days"], chunk["tag"]

    def test_event_year_august_capped_at_25(self):
        aug = [c for c in sd_sf_chunks([2026])
               if c["months"] == ["08"]]
        assert aug and max(aug[0]["days"]) == "25"

    def test_chunk_tags_unique(self):
        tags = [c["tag"] for c in sd_sf_chunks(list(range(2001, 2027)))]
        assert len(tags) == len(set(tags))

    def test_days_for_month_leap_and_cutoff(self):
        assert len(days_for_month(2024, "02")) == 29   # leap
        assert len(days_for_month(2023, "02")) == 28   # non-leap
        assert len(days_for_month(2001, "06")) == 30
        assert len(days_for_month(2026, "08")) == 25   # cutoff

    def test_month_day_groups_jja(self):
        groups = dict((frozenset(m), d) for d, m in
                      _month_day_groups([2001]))
        assert frozenset({"06"}) in groups
        assert frozenset({"07", "08"}) in groups


class TestUtcTimestamps:
    """H07 — emitted timestamps are explicit UTC."""

    def test_utc_now_iso_is_aware_z(self):
        ts = utc_now_iso()
        assert ts.endswith("Z")
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        assert dt.tzinfo == timezone.utc

    def test_utc_now_orders(self):
        a = utc_now_iso()
        b = utc_now_iso()
        assert a <= b


class TestClaimScanRecursion:
    """H10 — directories recurse; files behave as before."""

    def test_directory_recursion_flags_nested(self, tmp_path):
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        forbidden = "PILOT_" + "READY"  # keep this test file scan-clean
        (nested / "bad.md").write_text(f"status: {forbidden}\n")
        assert _claim_scan(str(tmp_path)) == 1

    def test_directory_clean_tree_passes(self, tmp_path):
        (tmp_path / "ok.md").write_text("status: DESIGN_DRAFT_COMPLETE\n")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "ok2.md").write_text("descriptive only\n")
        assert _claim_scan(str(tmp_path)) == 0

    def test_single_file_unchanged(self, tmp_path):
        f = tmp_path / "f.md"
        f.write_text("status: DESIGN_DRAFT_COMPLETE\n")
        assert _claim_scan(str(f)) == 0

    def test_missing_file_fails_closed(self, tmp_path):
        assert _claim_scan(str(tmp_path / "nope.md")) == 1


class TestLedgerRepair:
    """H06 — repair recomputes totals from real bytes; H02 receipts."""

    def _fake_run(self, tmp_path) -> Path:
        root = tmp_path / "run"
        for stage, payload in (("raw", b"x" * 100),
                               ("monthly", b"y" * 50),
                               ("merged", b"z" * 30)):
            d = root / stage
            d.mkdir(parents=True)
            (d / f"{stage}.nc").write_bytes(payload)
        ledger = {
            "start_time": "2026-09-15T10:52:14.831623",
            "end_time": "2026-09-15T11:40:00.0",
            "status": "completed",
            "total_size_mb": 0.0,
            "retrieval_paths": {"t2m": "reanalysis-era5-land"},
            "completed_months": [],
        }
        (root / "download_ledger.json").write_text(
            json.dumps(ledger))
        return root

    def test_totals_match_byte_sum(self, tmp_path):
        root = self._fake_run(tmp_path)
        repaired = repair_ledger(root)
        expected = 100 + 50 + 30
        acc = repaired["size_accounting"]
        assert acc["total_bytes"] == expected
        assert repaired["total_size_mb"] == pytest.approx(
            expected / (1024 * 1024), abs=1e-2)

    def test_timestamps_normalized(self, tmp_path):
        root = self._fake_run(tmp_path)
        repaired = repair_ledger(root)
        # Original naive value preserved verbatim; normalized UTC added.
        assert repaired["start_time"] == "2026-09-15T10:52:14.831623"
        assert repaired["start_time_utc"].endswith("Z")
        assert repaired["repaired_at_utc"].endswith("Z")

    def test_receipts_written_with_real_digests(self, tmp_path):
        root = self._fake_run(tmp_path)
        repair_ledger(root)
        out = write_provenance_receipts(root)
        receipts = json.loads(out.read_text())
        route = receipts["routes"]["t2m"]
        assert route["endpoint"] == "reanalysis-era5-land"
        payloads = route["payloads"]
        assert payloads and len(payloads[0]["sha256"]) == 64


class TestCanonicalJson:
    """H09 — _to_jsonable converts numpy explicitly, rejects NaN."""

    def test_numpy_types(self):
        from nepal.gmm_descriptive import _to_jsonable
        out = _to_jsonable({"a": np.int64(3), "b": np.array([1.0, 2.0]),
                            1: np.bool_(True)})
        assert out == {"a": 3, "b": [1.0, 2.0], "1": True}

    def test_nan_rejected(self):
        from nepal.gmm_descriptive import _to_jsonable
        with pytest.raises(TypeError):
            _to_jsonable({"x": float("nan")})

    def test_canonical_dump_sorts(self, tmp_path):
        from nepal.gmm_descriptive import _dump_canonical
        p = tmp_path / "o.json"
        with open(p, "w") as f:
            _dump_canonical({"b": 1, "a": 2}, f)
        assert p.read_text().index('"a"') < p.read_text().index('"b"')
