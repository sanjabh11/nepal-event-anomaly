"""Adversarial input-domain tests for the seasonal frame adapter.

A post-implementation audit found the adapter under-validated its
input domain.  Out-of-window dates, non-JJA season labels,
undeclared units, out-of-domain years, duplicated or
non-consecutive daily coverage, and non-coercible daily columns are
CONTAMINATION — they must fail closed with ValueError and can never
be silently ledger-dropped.  Honest missingness (a truncated
season, non-constant carriers, non-finite features) remains a
ledgered drop.
"""
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from nepal.science_v0.seasonal_frame import (JJA_DAYS,
                                           build_seasonal_frame)

EVIDENCE = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
DAILY_CSV = (EVIDENCE / "p5-glof-2026-09-19/era5-multibasin/features/"
             "regime_frame_hma_jja_2001_2025.csv")


def _jja_rows(basin="gandaki", year=2001, dates=None):
    """One valid basin-year of synthetic daily rows; defaults to the
    complete consecutive Jun1..Aug31 window (JJA_DAYS rows)."""
    rng = np.random.default_rng(7)
    if dates is None:
        dates = pd.date_range(f"{year}-06-01", f"{year}-08-31",
                              freq="D")
    elev = {"gandaki": 4972.1, "karnali": 4800.0,
            "koshi": 4300.0}.get(basin, 4000.0)
    rows = []
    for d in dates:
        rows.append({
            "date": d.strftime("%Y-%m-%d"),
            "t2m_daily": float(rng.normal(2, 1)),
            "d2m_daily": float(rng.normal(0, 1)),
            "pdd_daily": float(abs(rng.normal(3, 1))),
            "tp_daily": float(abs(rng.normal(1, 1))),
            "sd_daily": float(100 + rng.normal(0, 5)),
            "unit_id": basin,
            "basin_group": basin,
            "season": "JJA",
            "era": "pre_2013" if year < 2013 else "post_2013",
            "elevation_m": elev})
    return rows


def _write_daily(tmp_path, rows):
    """Write the synthetic daily CSV plus its sha256 sidecar so the
    byte-verification gate binds it."""
    df = pd.DataFrame(rows)
    csv = tmp_path / "daily.csv"
    df.to_csv(csv, index=False)
    sha = hashlib.sha256(csv.read_bytes()).hexdigest()
    Path(str(csv) + ".sha256").write_text(f"{sha}  daily.csv\n")
    return csv


class TestSeasonalFrameAdversarial:
    """Contamination fails closed; honest missingness is ledgered."""

    def test_non_jja_season_label_refused(self, tmp_path):
        rows = _jja_rows()
        rows[10]["season"] = "JAS"
        csv = _write_daily(tmp_path, rows)
        with pytest.raises(ValueError, match="non-JJA season"):
            build_seasonal_frame(csv, tmp_path / "out")

    def test_dates_outside_jja_window_refused(self, tmp_path):
        """92 validly-shaped rows whose dates spill into May are
        contamination, not a short season."""
        dates = pd.date_range("2001-05-01", periods=JJA_DAYS,
                              freq="D")
        csv = _write_daily(tmp_path, _jja_rows(dates=dates))
        with pytest.raises(ValueError, match="outside JJA window"):
            build_seasonal_frame(csv, tmp_path / "out")

    def test_duplicated_dates_padded_to_92_refused(self, tmp_path):
        dates = list(pd.date_range("2001-06-01", "2001-08-30",
                                   freq="D"))          # 91 unique
        dates = dates + [dates[-1]]                     # dup -> 92
        csv = _write_daily(tmp_path, _jja_rows(dates=dates))
        with pytest.raises(
                ValueError,
                match="non-unique or non-consecutive"):
            build_seasonal_frame(csv, tmp_path / "out")

    def test_gap_inside_92_row_group_refused(self, tmp_path):
        """92 rows that omit a mid-season day (the count is padded
        by a duplicate elsewhere) are contamination."""
        dates = list(pd.date_range("2001-06-01", "2001-08-31",
                                   freq="D"))
        del dates[45]                                   # a gap
        dates = dates + [dates[0]]                      # pad to 92
        csv = _write_daily(tmp_path, _jja_rows(dates=dates))
        with pytest.raises(
                ValueError,
                match="non-unique or non-consecutive"):
            build_seasonal_frame(csv, tmp_path / "out")

    def test_july_only_group_is_ledger_drop(self, tmp_path):
        """A truncated season is honest missingness: dropped and
        ledgered, never an exception."""
        dates = pd.date_range("2001-07-01", "2001-07-31", freq="D")
        csv = _write_daily(tmp_path, _jja_rows(dates=dates))
        build = build_seasonal_frame(csv, tmp_path / "out")
        assert len(build["frame"]) == 0
        drops = build["ledger"]["dropped_basin_years"]
        assert len(drops) == 1
        assert drops[0]["unit"] == "gandaki"
        assert drops[0]["year"] == 2001
        assert f"{len(dates)} daily rows != {JJA_DAYS}" \
            in drops[0]["reason"]

    def test_undeclared_unit_refused(self, tmp_path):
        csv = _write_daily(tmp_path,
                           _jja_rows(basin="madeup_basin"))
        with pytest.raises(ValueError, match="undeclared unit"):
            build_seasonal_frame(csv, tmp_path / "out")

    def test_out_of_domain_year_refused(self, tmp_path):
        csv = _write_daily(tmp_path, _jja_rows(year=2026))
        with pytest.raises(ValueError,
                           match="year outside declared domain"):
            build_seasonal_frame(csv, tmp_path / "out")

    def test_non_coercible_daily_column_refused(self, tmp_path):
        rows = _jja_rows()
        rows[5]["t2m_daily"] = "not-a-number"
        csv = _write_daily(tmp_path, rows)
        with pytest.raises(ValueError, match="coerced to float64"):
            build_seasonal_frame(csv, tmp_path / "out")

    def test_mixed_basin_group_is_ledger_drop(self, tmp_path):
        """Non-constant carriers stay a ledgered drop, not a
        refusal."""
        rows = _jja_rows()
        for r in rows[:46]:
            r["basin_group"] = "other_group"
        csv = _write_daily(tmp_path, rows)
        build = build_seasonal_frame(csv, tmp_path / "out")
        assert len(build["frame"]) == 0
        drops = build["ledger"]["dropped_basin_years"]
        assert len(drops) == 1
        assert "not constant" in drops[0]["reason"]

    def test_year_block_carrier_is_metadata_only(self, tmp_path):
        """Provenance must not claim an executed year-block
        stability predicate — the column is a metadata/resampling
        carrier only."""
        csv = _write_daily(tmp_path, _jja_rows())
        build = build_seasonal_frame(csv, tmp_path / "out")
        prov = json.loads(Path(build["provenance"]).read_text())
        txt = prov["year_block_carrier"].lower()
        assert "metadata" in txt
        # any mention of "stability predicate" must be negated
        assert "stability predicate" in txt
        assert ("no year-block stability predicate" in txt
                or "not executed" in txt
                or "never executed" in txt)

    def test_real_daily_frame_builds_cleanly(self, tmp_path):
        """The critical regression: the strict refusals must not
        break the bound real daily frame."""
        if not DAILY_CSV.exists():
            pytest.skip("real daily frame absent")
        build = build_seasonal_frame(DAILY_CSV, tmp_path / "out")
        frame = build["frame"]
        assert len(frame) == 75
        assert sorted(frame["unit_id"].unique()) == \
            ["gandaki", "karnali", "koshi"]
        assert frame["season_year"].nunique() == 25
        assert build["ledger"]["dropped_basin_years"] == []
        assert Path(str(build["csv"]) + ".sha256").exists()
        assert Path(build["provenance"]).exists()
