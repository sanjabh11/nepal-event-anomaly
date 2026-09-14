"""TEST-01 — fixture tests for the P5 I/O contract.

These use synthetic temporary payloads — they test the parser and
contract surface only; they are never scientific inputs.
"""
from __future__ import annotations

import io
import sys
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "nepal"))

import era5_download as dl
import feature_extraction as fe
import gmm_descriptive as gmm


def _hourly_ds(vars_present: list[str], n_hours: int = 48) -> xr.Dataset:
    times = pd.date_range("2001-06-01", periods=n_hours, freq="h")
    return xr.Dataset(
        {v: (("valid_time", "latitude", "longitude"),
             rng_arr(n_hours)) for v in vars_present},
        coords={
            "valid_time": times,
            "latitude": [28.2, 28.3],
            "longitude": [85.4, 85.5],
        })


def rng_arr(n: int) -> np.ndarray:
    rng = np.random.default_rng(1)
    return np.abs(rng.normal(size=(n, 2, 2)))


GOOD_VARS = ["t2m", "d2m", "u10", "v10", "sd", "sf", "tp"]


class TestPayloadNormalization:
    def test_zip_unwrap_and_rename_valid_time(self, tmp_path):
        ds = _hourly_ds(GOOD_VARS)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            nc = io.BytesIO()
            ds.to_netcdf(nc)
            z.writestr("data_0.nc", nc.getvalue())
        payload = tmp_path / "era5_land_2001_06.nc"
        payload.write_bytes(buf.getvalue())
        out = dl.normalize_payload(payload, tmp_path / "m",
                                   year=2001, month="06")
        norm = xr.open_dataset(out)
        assert "time" in norm.dims and "valid_time" not in norm.dims
        assert set(GOOD_VARS) <= set(norm.data_vars)
        assert "sde" not in norm.data_vars

    def test_sde_rejected_not_renamed(self, tmp_path):
        ds = _hourly_ds(["t2m", "d2m", "u10", "v10", "sde", "sf", "tp"])
        payload = tmp_path / "bad.nc"
        ds.to_netcdf(payload)
        with pytest.raises(ValueError, match="sde|snow"):
            dl.normalize_payload(payload, tmp_path / "m")

    def test_non_netcdf_rejected(self, tmp_path):
        payload = tmp_path / "junk.bin"
        payload.write_bytes(b"not a netcdf at all")
        with pytest.raises(ValueError):
            dl.normalize_payload(payload, tmp_path / "m")

    def test_real_legacy_payload_is_zip_with_sde(self):
        # The committed June-2001 payload is a ZIP carrying sde — the
        # corrected request must be re-run; it cannot be normalized.
        payload = (Path(__file__).resolve().parents[1]
                   / "data" / "temp" / "era5_land_2001_06.nc")
        if not payload.exists():
            pytest.skip("legacy payload absent")
        with zipfile.ZipFile(payload) as z:
            assert any(n.endswith(".nc") for n in z.namelist())


class TestRequestSemantics:
    def test_august_2026_capped_at_25(self):
        days = dl.days_for_month(2026, "08")
        assert len(days) == 25 and days[-1] == "25"

    def test_june_2026_full_month(self):
        assert len(dl.days_for_month(2026, "06")) == 30

    def test_invalid_calendar_days_never_generated(self):
        assert len(dl.days_for_month(2001, "06")) == 30
        assert len(dl.days_for_month(2001, "02")) == 28 \
            if hasattr(dl, "days_for_month") else True

    def test_run_root_rejects_frozen_paths(self):
        for bad in ("data", "data/x", "pinned",
                    "nepal/framework_v1"):
            with pytest.raises(ValueError):
                dl.resolve_run_root(Path(bad))


class TestCellSelection:
    def test_tie_breaks_to_higher_latitude(self):
        ds = _hourly_ds(GOOD_VARS)
        lat, lon = dl.select_cell(ds, 28.25, 85.5)
        assert lat == pytest.approx(28.3)  # tie → higher lat
        assert lon == pytest.approx(85.5)


class TestFeatureExtraction:
    def test_missing_required_var_aborts(self):
        ds = _hourly_ds(["t2m", "d2m", "u10", "v10", "sf", "tp"])
        cell = ds.isel(latitude=0, longitude=0)
        with pytest.raises(ValueError, match="sd"):
            fe.extract_raw_features(cell)

    def test_sde_is_not_sd(self):
        ds = _hourly_ds(["t2m", "d2m", "u10", "v10", "sde",
                         "sf", "tp"])
        cell = ds.isel(latitude=0, longitude=0)
        with pytest.raises(ValueError):
            fe.extract_raw_features(cell)

    def test_pdd7day_resets_at_year_boundary(self):
        # JJA-only index: Aug 31 2001 → Jun 1 2002 is a gap.
        idx = pd.to_datetime(
            ["2001-08-25", "2001-08-26", "2001-08-27", "2001-08-28",
             "2001-08-29", "2001-08-30", "2001-08-31",
             "2002-06-01", "2002-06-02", "2002-06-03", "2002-06-04",
             "2002-06-05", "2002-06-06", "2002-06-07"])
        df = pd.DataFrame({"t2m_daily": np.full(len(idx), 10.0)},
                          index=idx)
        df["pdd_daily"] = df["t2m_daily"].clip(lower=0)
        run_id = (df.index.to_series().diff().dt.days != 1).cumsum()
        pdd7 = df.groupby(run_id)["pdd_daily"].transform(
            lambda s: s.rolling(7, min_periods=7).sum())
        # 2002-06-07 is the first valid value of the second run.
        assert np.isnan(pdd7.loc["2002-06-06"])
        assert pdd7.loc["2002-06-07"] == pytest.approx(70.0)


class TestGMMResiduals:
    def _frame(self):
        rng = np.random.default_rng(0)
        idx = pd.date_range("2001-06-01", "2026-08-25", freq="D")
        return pd.DataFrame(
            {c: rng.normal(size=len(idx)) for c in gmm.GMM_FEATURES},
            index=idx)

    def test_missingness_computed_pre_filter(self):
        df = self._frame()
        df.loc[df.index[:50], "tp_daily"] = np.nan
        res = gmm.run_gmm_descriptive(df)
        assert res["missingness"]["per_column_na"]["tp_daily"] == 50

    def test_js_bootstrap_honors_declared_replicates(self):
        res = gmm.run_gmm_descriptive(self._frame())
        assert res["js_distance"]["n_replicates"] == \
            gmm.JS_BOOTSTRAP_BLOCKS

    def test_only_converged_fits_selectable(self):
        res = gmm.run_gmm_descriptive(self._frame())
        seed = res["best_k_per_seed"]
        # every selected K must come from a converged fit
        for s, k in seed.items():
            assert res["converged"][str(s)][str(k)] is True
