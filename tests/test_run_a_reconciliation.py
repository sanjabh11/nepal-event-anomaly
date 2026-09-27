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
import pandas as pd
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
        # route-attributable payload name
        (root / "raw" / "arco_timeseries.nc").write_bytes(b"a" * 10)
        ledger = {
            "start_time": "2026-09-15T10:52:14.831623",
            "end_time": "2026-09-15T11:40:00.0",
            "status": "completed",
            "total_size_mb": 0.0,
            "retrieval_paths": {
                "t2m,d2m,u10,v10,tp": "reanalysis-era5-land-timeseries"},
            "completed_months": [],
        }
        (root / "download_ledger.json").write_text(
            json.dumps(ledger))
        return root

    def test_totals_match_byte_sum(self, tmp_path):
        root = self._fake_run(tmp_path)
        repaired = repair_ledger(root)
        expected = 100 + 10 + 50 + 30  # raw.nc + arco + monthly + merged
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
        route = receipts["routes"]["t2m,d2m,u10,v10,tp"]
        assert route["endpoint"] == "reanalysis-era5-land-timeseries"
        payloads = route["payloads"]
        assert len(payloads) == 1
        assert payloads[0]["file"] == "arco_timeseries.nc"
        assert len(payloads[0]["sha256"]) == 64
        # the generic raw.nc does not match any route pattern
        assert [p["file"] for p in receipts["unattributed"]] == \
            ["raw.nc"]


class TestAccumulationSemantics:
    """RA-01 — running-accumulation vs hourly-increment detection and
    closing-value daily totals for ERA5-Land sf/tp."""

    def _running_series(self) -> "pd.Series":
        import pandas as pd
        # 3 days: day1 accumulates to 0.070, day2 to 0.002,
        # day3 partial (no 00:00 close) — 00:00 stamp closes prior day.
        idx = pd.date_range("2012-02-08 01:00", "2012-02-10 23:00",
                            freq="h")
        vals = []
        base = 0.0
        for i, _ in enumerate(idx):
            day = i // 24
            target = [0.070, 0.002, 0.010][day]
            vals.append(base + (i % 24 + 1) / 24 * target)
            if i % 24 == 23:
                base = 0.0
        # 00:00-02-09 holds day-1's close (0.070 + tiny step)
        s = pd.Series(vals, index=idx)
        return s

    def test_detects_running_accumulation(self):
        from nepal.feature_extraction import _is_running_accumulation
        s = self._running_series()
        assert _is_running_accumulation(s)

    def test_increments_not_running(self):
        import pandas as pd
        from nepal.feature_extraction import _is_running_accumulation
        idx = pd.date_range("2024-06-01 00:00", "2024-06-04 23:00",
                            freq="h")
        rng = np.random.default_rng(0)
        s = pd.Series(rng.random(len(idx)) * 0.001, index=idx)
        assert not _is_running_accumulation(s)

    def test_closing_value_attribution(self):
        from nepal.feature_extraction import (
            _accumulation_day, _daily_accumulation_total)
        s = self._running_series()
        days = _accumulation_day(s.index)
        # the 00:00-02-09 stamp belongs to 2012-02-08
        assert days[23] == pd.Timestamp("2012-02-08")
        totals, partial = _daily_accumulation_total(s)
        assert totals.loc[pd.Timestamp("2012-02-08")] == \
            pytest.approx(0.070, abs=1e-3)
        # last day lacks a 00:00 close -> flagged partial
        assert bool(partial.loc[pd.Timestamp("2012-02-10")])

    def test_daily_total_equals_final_step_not_sum(self):
        from nepal.feature_extraction import _daily_accumulation_total
        s = self._running_series()
        totals, _ = _daily_accumulation_total(s)
        # buggy sum would be ~0.85 (sum of the ramp); closing value is 0.070
        assert totals.loc[pd.Timestamp("2012-02-08")] < 0.1

    def test_sparse_snow_still_detected(self):
        """Regression: zero-close days must not dilute the reset
        fraction below threshold (auditor-found latent defect)."""
        from nepal.feature_extraction import _is_running_accumulation
        idx = pd.date_range("2024-06-01 01:00", "2024-06-21 00:00",
                            freq="h")
        vals = np.zeros(len(idx))
        # only 2 of 20 days accumulate; the rest are all-zero
        for day_start in (5, 12):
            lo = day_start * 24
            vals[lo:lo + 24] = np.linspace(0.001, 0.05, 24)
        s = pd.Series(vals, index=idx)
        assert _is_running_accumulation(s)

    def test_nan_closing_stamp_flags_partial(self):
        from nepal.feature_extraction import (
            _daily_accumulation_total)
        idx = pd.date_range("2024-06-01 01:00", "2024-06-02 00:00",
                            freq="h")
        vals = np.linspace(0.001, 0.02, len(idx))
        vals[-1] = np.nan  # NaN closing stamp
        s = pd.Series(vals, index=idx)
        _, partial = _daily_accumulation_total(s)
        assert bool(partial.iloc[0])


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
