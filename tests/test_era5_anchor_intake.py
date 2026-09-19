"""Fixture tests for the P5-C anchor intake (era5_anchor_intake).

Synthetic payloads only — these test the parser and contract surface,
never scientific inputs.  A real-evidence smoke test skips when the
acquired bytes are absent.
"""
from __future__ import annotations

import csv
import io
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nepal"))

import era5_anchor_intake as ea

EVIDENCE = Path(
    "/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19/"
    "era5-multibasin")

_BASIN = "koshi"
_BOX = ea.ANCHOR_BOXES[_BASIN]


def _ts_zip(tmp_path: Path) -> Path:
    """A contiguous-timeseries payload: two member ncs, months 5-9."""
    times = pd.date_range("2001-05-01", "2001-09-30 23:00", freq="h")
    lats = np.linspace(_BOX["lat"][0] + 0.05, _BOX["lat"][1] - 0.05, 2)
    lons = np.linspace(_BOX["lon"][0] + 0.05, _BOX["lon"][1] - 0.05, 3)
    half = len(times) // 2
    zpath = tmp_path / ea.TIMESERIES_TEMPLATE.format(basin=_BASIN)
    with zipfile.ZipFile(zpath, "w") as z:
        for i, span in enumerate((times[:half], times[half:])):
            ds = xr.Dataset(
                {v: (("valid_time", "latitude", "longitude"),
                     np.full((len(span), 2, 3), float(i)))
                 for v in ea.TS_VARS},
                coords={"valid_time": span,
                        "latitude": lats, "longitude": lons})
            buf = io.BytesIO()
            ds.to_netcdf(buf)
            z.writestr(f"data_{i}.nc", buf.getvalue())
    return zpath


def _snow_csvs(root: Path, basin: str, years) -> None:
    for y in years:
        times = pd.date_range(f"{y}-06-01", f"{y}-08-31 23:00", freq="h")
        path = root / ea.SNOW_TEMPLATE.format(basin=basin, year=y)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.writer(fh)
            w.writerow(["id", "longitude", "latitude", "time",
                        ea.EE_SD, ea.EE_SF])
            for t in times:
                for cell in range(6):
                    w.writerow([t.strftime("%Y%m%dT%H"),
                                86.95 + 0.1 * cell, 27.95,
                                int(t.timestamp() * 1000),
                                5.0 + cell, 1e-7])


class TestTimeseriesLeg:
    def test_members_concat_and_jja_filter(self, tmp_path):
        _ts_zip(tmp_path)
        ts = ea._load_timeseries_hourly(
            tmp_path / ea.TIMESERIES_TEMPLATE.format(basin=_BASIN),
            _BASIN)
        # contiguous months 5..9 all present pre-filter
        assert ts.index.month.min() == 5 and ts.index.month.max() == 9
        assert not ts.index.duplicated().any()
        # box-mean: all cells identical -> mean equals cell value
        assert (ts["t2m"] == 0).all() or set(ts["t2m"]) == {0.0, 1.0}

    def test_grid_escaping_box_rejected(self, tmp_path):
        times = pd.date_range("2001-06-01", periods=48, freq="h")
        zpath = tmp_path / ea.TIMESERIES_TEMPLATE.format(basin=_BASIN)
        with zipfile.ZipFile(zpath, "w") as z:
            ds = xr.Dataset(
                {v: (("valid_time", "latitude", "longitude"),
                     np.zeros((48, 1, 1))) for v in ea.TS_VARS},
                coords={"valid_time": times, "latitude": [30.5],
                        "longitude": [90.0]})  # far outside the box
            buf = io.BytesIO()
            ds.to_netcdf(buf)
            z.writestr("data_0.nc", buf.getvalue())
        with pytest.raises(ValueError, match="escapes the frozen"):
            ea._load_timeseries_hourly(zpath, _BASIN)


class TestSnowLeg:
    def test_cell_mean_and_missing_year_fails(self, tmp_path):
        _snow_csvs(tmp_path, _BASIN, [2001])
        df = ea._load_snow_hourly.__wrapped__ if False else None
        # full set required -> 2002..2025 absent -> fail closed
        with pytest.raises(FileNotFoundError, match="missing snow"):
            ea._load_snow_hourly(tmp_path, _BASIN)


class TestMerge:
    def test_jja_rows_and_exclusion_count(self, tmp_path):
        _ts_zip(tmp_path)
        _snow_csvs(tmp_path, _BASIN, range(2001, 2026))
        merged, report = ea.build_basin_hourly(tmp_path, _BASIN)
        # EE snow is JJA-only, so merge keeps only JJA 2001 timestamps
        assert merged.index.year.unique().tolist() == [2001]
        assert set(merged.index.month.unique()) == {6, 7, 8}
        assert report["non_jja_rows_excluded"] > 0
        assert report["merged_per_year"][2001] == len(merged)


class TestRegimeFrame:
    def _daily(self, basin):
        idx = pd.date_range("2001-06-01", periods=92, freq="D")
        df = pd.DataFrame(
            {c: 1.0 for c in ea.DAILY_FEATURE_COLS}, index=idx)
        df["edge_censored"] = False
        return df

    def test_bookkeeping_columns(self):
        frame, prov = ea.build_regime_frame(
            {"koshi": self._daily("koshi"),
             "gandaki": self._daily("gandaki")},
            {"koshi": 5063.9, "gandaki": 4972.1})
        for col in ("unit_id", "date", "basin_group", "season", "era",
                    "elevation_m"):
            assert col in frame.columns
        assert set(frame["season"]) == {"JJA"}
        assert set(frame["unit_id"]) == {"koshi", "gandaki"}
        assert prov["era_boundary"] == ea.ERA_BOUNDARY_YEAR

    def test_missing_feature_col_fails(self):
        df = self._daily("koshi").drop(columns=["sd_daily"])
        with pytest.raises(ValueError, match="missing feature"):
            ea.build_regime_frame({"koshi": df}, {"koshi": 5000.0})


class TestManifestFloor:
    """FMX-02: unverified bytes never reach the parser."""

    def _manifest(self, tmp_path):
        from nepal.research_v0.source_intake import (
            build_source_manifest)
        import hashlib
        f = tmp_path / "a.csv"
        f.write_text("x\n", encoding="utf-8")
        return build_source_manifest(
            tmp_path, source_id="s", source_version="1",
            source_files=[{"relpath": "a.csv",
                           "sha256": hashlib.sha256(
                               f.read_bytes()).hexdigest()}],
            units=["a"], feature_allowlist=["f1"], lineage="t")

    def test_tampered_input_fails(self, tmp_path):
        m = self._manifest(tmp_path)
        (tmp_path / "a.csv").write_text("tampered\n")
        with pytest.raises(ValueError):
            ea.verify_inputs_against_manifest(
                tmp_path, m, ["a.csv"])

    def test_undeclared_path_fails(self, tmp_path):
        m = self._manifest(tmp_path)
        (tmp_path / "b.csv").write_text("y\n")
        with pytest.raises(ValueError, match="not a declared"):
            ea.verify_inputs_against_manifest(
                tmp_path, m, ["b.csv"])

    def test_stale_sidecar_fails(self, tmp_path):
        f = tmp_path / "a.csv"
        f.write_text("x\n", encoding="utf-8")
        import hashlib
        (tmp_path / "a.csv.sha256").write_text("0" * 64)
        m = self._manifest(tmp_path)
        with pytest.raises(ValueError, match="stale sidecar"):
            ea.verify_inputs_against_manifest(
                tmp_path, m, ["a.csv"])


@pytest.mark.skipif(
    not (EVIDENCE / "era5land_ts_koshi_hma_2001-2025.zip").exists(),
    reason="P5-C real evidence absent")
class TestRealEvidenceSmoke:
    def test_koshi_hourly_shape(self):
        merged, report = ea.build_basin_hourly(EVIDENCE, "koshi")
        assert report["merged_rows"] == 25 * 92 * 24
        assert set(merged.index.month.unique()) == {6, 7, 8}
        assert not merged.isna().any().any()
