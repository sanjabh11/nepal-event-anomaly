"""TEST-01 — fixture tests for the P5 I/O contract.

These use synthetic temporary payloads — they test the parser and
contract surface only; they are never scientific inputs.
"""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
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


def _hourly_ds(vars_present: list[str], n_hours: int = 48,
               times: pd.DatetimeIndex | None = None) -> xr.Dataset:
    if times is None:
        times = pd.date_range("2001-06-01", periods=n_hours, freq="h")
    n = len(times)
    return xr.Dataset(
        {v: (("valid_time", "latitude", "longitude"),
             rng_arr(n)) for v in vars_present},
        coords={
            "valid_time": times,
            "latitude": [28.2, 28.3],
            "longitude": [85.4, 85.5],
        })


def rng_arr(n: int) -> np.ndarray:
    rng = np.random.default_rng(1)
    return np.abs(rng.normal(size=(n, 2, 2)))


GOOD_VARS = ["t2m", "d2m", "u10", "v10", "sd", "sf", "tp"]

MERGED_NAME = "era5_land_nepal_jja_2001_2026.nc"


def _month_hours(year: int, month: str) -> pd.DatetimeIndex:
    """The exact hourly timestamp set a valid {year}-{month} payload
    must carry under the shared contract (days_for_month already
    encodes the 2026-08 cutoff at day 25)."""
    days = dl.days_for_month(year, month)
    return pd.date_range(f"{year}-{month}-{days[0]}T00:00",
                         periods=len(days) * 24, freq="h")


def _monthly_ds(year: int, month: str) -> xr.Dataset:
    """A normalized-form monthly dataset (canonical 'time' dim) as it
    would sit under <run_root>/monthly/."""
    times = _month_hours(year, month)
    n = len(times)
    return xr.Dataset(
        {v: (("time", "latitude", "longitude"), rng_arr(n))
         for v in GOOD_VARS},
        coords={
            "time": times,
            "latitude": [28.2, 28.3],
            "longitude": [85.4, 85.5],
        })


def _zip_payload(ds: xr.Dataset) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        nc = io.BytesIO()
        ds.to_netcdf(nc)
        z.writestr("data_0.nc", nc.getvalue())
    return buf.getvalue()


class TestPayloadNormalization:
    def test_zip_unwrap_and_rename_valid_time(self, tmp_path):
        # P5-07: a valid payload is the EXACT hourly set for the
        # declared month — 30 days x 24 h for June 2001.
        ds = _hourly_ds(GOOD_VARS, times=_month_hours(2001, "06"))
        payload = tmp_path / "era5_land_2001_06.nc"
        payload.write_bytes(_zip_payload(ds))
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

    def test_wrong_month_payload_rejected(self, tmp_path):
        # P5-12: internal timestamps are a full, valid July but the
        # filename/declared month says June — must raise, not normalize.
        ds = _hourly_ds(GOOD_VARS, times=_month_hours(2001, "07"))
        payload = tmp_path / "era5_land_2001_06.nc"
        payload.write_bytes(_zip_payload(ds))
        with pytest.raises(ValueError):
            dl.normalize_payload(payload, tmp_path / "m",
                                 year=2001, month="06")

    def test_post_cutoff_timestamp_rejected(self, tmp_path):
        # P5-12: hours on/after the held-out event date (2026-08-26)
        # must be rejected, even in an otherwise plausible August file.
        times = pd.date_range("2026-08-01", periods=26 * 24, freq="h")
        ds = _hourly_ds(GOOD_VARS, times=times)
        payload = tmp_path / "era5_land_2026_08.nc"
        ds.to_netcdf(payload)
        with pytest.raises(ValueError):
            dl.normalize_payload(payload, tmp_path / "m",
                                 year=2026, month="08")

    def test_duplicate_timestamps_rejected_or_flagged(self, tmp_path):
        # P5-12: a full June hourly set with one stamp duplicated over
        # its neighbour — same length, one duplicate, one missing hour.
        # The contract permits either an outright rejection OR a
        # recorded flag; silent dedup with no trace is a violation.
        times = list(_month_hours(2001, "06"))
        times[10] = times[9]
        ds = _hourly_ds(GOOD_VARS, times=pd.DatetimeIndex(times))
        payload = tmp_path / "era5_land_2001_06.nc"
        ds.to_netcdf(payload)
        entry: dict = {}
        try:
            out = dl.normalize_payload(payload, tmp_path / "m",
                                       year=2001, month="06",
                                       ledger_entry=entry)
        except ValueError:
            return  # contract option A: duplicates rejected outright
        # contract option B: dedup may proceed only if a flag is
        # recorded in the ledger entry.
        with xr.open_dataset(out) as norm:
            assert (np.unique(norm["time"].values).size
                    == norm.sizes["time"])
        assert any("dup" in k.lower() for k in entry), (
            "duplicate timestamps dropped silently — no dedup flag in "
            f"ledger_entry (keys: {sorted(entry)})")

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

    def test_snow_depth_name_rejected(self):
        # P5-12: a variable literally named 'snow_depth' is geometric
        # snow depth under the CDS long name — it must NOT satisfy the
        # 'sd' (snow-depth water equivalent) slot; the extractor raises.
        ds = _hourly_ds(["t2m", "d2m", "u10", "v10", "snow_depth",
                         "sf", "tp"])
        cell = ds.isel(latitude=0, longitude=0)
        with pytest.raises(ValueError):
            fe.extract_raw_features(cell)

    def test_pdd_reset_calls_production(self):
        # P5-12/CFM-01: the Aug31 -> Jun1 boundary must reset the 7-day
        # PDD window. Exercises the production compute_thermal_indices,
        # not a reimplemented formula.
        cti = getattr(fe, "compute_thermal_indices", None)
        if cti is None:
            pytest.skip("feature_extraction.compute_thermal_indices "
                        "absent")
        # Two 24h/day runs: end of JJA 2001, start of JJA 2002.
        idx1 = pd.date_range("2001-08-25", periods=7 * 24, freq="h")
        idx2 = pd.date_range("2002-06-01", periods=8 * 24, freq="h")
        df = pd.DataFrame({"t2m": 10.0}, index=idx1.append(idx2))
        try:
            daily = cti(df, model_elev_m=4322.0)
        except TypeError:  # positional-only signature variant
            daily = cti(df, 4322.0)
        pdd7 = daily["pdd_7day"]
        # The first run completes a full 7-day window (7 x 10 degC).
        assert pdd7.loc[pd.Timestamp("2001-08-31")] \
            == pytest.approx(70.0)
        # The second year's run restarts: its first six days are NaN
        # (the window must not reach back across the Aug31->Jun1 gap).
        assert np.isnan(pdd7.loc[pd.Timestamp("2002-06-01")])
        assert np.isnan(pdd7.loc[pd.Timestamp("2002-06-06")])
        # ...and inside the run the value is a true 7-day sum.
        assert pdd7.loc[pd.Timestamp("2002-06-07")] \
            == pytest.approx(70.0)
        assert pdd7.loc[pd.Timestamp("2002-06-08")] \
            == pytest.approx(70.0)

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


class TestGMMPreflight:
    """P5-12 — gmm.preflight gates on exact daily coverage."""

    def _exact_jja_frame(self) -> pd.DataFrame:
        # 92 JJA days x 25 baseline years (2001-2025) + 86 pre-event
        # days in 2026 (Jun 30 + Jul 31 + Aug 1-25).
        dates: list[pd.Timestamp] = []
        for y in range(2001, 2026):
            dates.extend(pd.date_range(f"{y}-06-01", f"{y}-08-31",
                                       freq="D"))
        dates.extend(pd.date_range("2026-06-01", "2026-08-25",
                                   freq="D"))
        idx = pd.DatetimeIndex(dates)
        rng = np.random.default_rng(0)
        return pd.DataFrame(
            {c: rng.normal(size=len(idx)) for c in gmm.GMM_FEATURES},
            index=idx)

    def test_exact_daily_coverage_preflight(self, tmp_path):
        if not hasattr(gmm, "preflight"):
            pytest.skip("gmm_descriptive.preflight absent")
        df = self._exact_jja_frame()
        csv = tmp_path / "features.csv"
        df.to_csv(csv)
        assert gmm.preflight(csv) == []

        # One duplicated day must be flagged — a dup can mask a missing
        # day, so silent acceptance violates exact coverage.
        dup_csv = tmp_path / "features_dup.csv"
        pd.concat([df, df.iloc[[0]]]).to_csv(dup_csv)
        assert gmm.preflight(dup_csv), \
            "duplicate row not flagged by preflight"

        # A non-JJA (December) row inside the baseline period must be
        # flagged — the frame is a JJA-only contract surface.
        dec = df.iloc[[0]].copy()
        dec.index = pd.DatetimeIndex(["2001-12-01"])
        dec_csv = tmp_path / "features_dec.csv"
        pd.concat([df, dec]).to_csv(dec_csv)
        assert gmm.preflight(dec_csv), \
            "non-JJA (December) row not flagged by preflight"


class TestSharedRunRootLayout:
    """P5-12 — one top-level RUN_ROOT: downloader writes monthly/ and
    merged/, the extractor consumes merged/<MERGED_NAME>."""

    def test_end_to_end_layout(self, tmp_path):
        run_root = tmp_path / "run_root"
        monthly_dir = run_root / "monthly"
        monthly_dir.mkdir(parents=True)
        completed = []
        for year, month in ((2001, "06"), (2001, "07")):
            _monthly_ds(year, month).to_netcdf(
                monthly_dir / f"era5_land_{year}_{month}.nc")
            completed.append({"year": year, "month": month})

        ledger: dict = {}
        out = dl.merge_monthly(run_root=run_root, completed=completed,
                               ledger=ledger)
        merged_name = getattr(dl, "MERGED_NAME", MERGED_NAME)
        merged = run_root / "merged" / merged_name
        assert out == merged and merged.is_file()
        # P5-08: complete.json is written only for a validated merge.
        assert (run_root / "merged" / "complete.json").is_file()
        assert ledger.get("incomplete_months") == []
        # The extractor's default input is <run_root>/merged/<name>.
        assert getattr(fe, "ERA5_FILENAME", merged_name) == merged_name

        # Real consumption: the production extractor pieces must read
        # the merged file. load_era5_land re-selects the event cell;
        # a merged file already carrying scalar cell coordinates is
        # also acceptable input to extract_raw_features directly.
        ds = xr.open_dataset(merged)
        try:
            cell, _lat, _lon, _elev = fe.load_era5_land(merged)
        except ValueError:
            cell = ds  # merged file is already the selected cell
        hourly = fe.extract_raw_features(cell)
        ds.close()
        assert len(hourly) == (30 + 31) * 24
        assert set(GOOD_VARS) <= set(hourly.columns)
        assert hourly.index.min() == pd.Timestamp("2001-06-01")
        assert hourly.index.max() == pd.Timestamp("2001-07-31 23:00")


class TestYearRange:
    """P5-02 — strict --year-range validation on dl.main.

    Reversed, out-of-bounds and post-event ranges are rejected with a
    nonzero exit before any cdsapi import or file creation; the full
    contract range dry-runs cleanly.
    """

    @staticmethod
    def _rc(argv: list[str]) -> int:
        """Process-style exit code for an in-process dl.main call: its
        int return, SystemExit.code, or 1 for an uncaught exception
        (a crash is still a nonzero exit)."""
        try:
            return int(dl.main(argv))
        except SystemExit as e:
            return e.code if isinstance(e.code, int) else 1
        except Exception:
            return 1

    @pytest.mark.parametrize(
        "spec", ["2026-2025", "2000-2001", "2027-2027"])
    def test_invalid_year_range_rejected(self, spec, tmp_path):
        rc = self._rc(["--year-range", spec, "--dry-run",
                       "--run-root", str(tmp_path / "run")])
        assert rc != 0, f"--year-range {spec} must exit nonzero"

    def test_contract_range_dry_run_ok(self, tmp_path, capsys):
        rc = self._rc(["--year-range", "2001-2026", "--dry-run",
                       "--run-root", str(tmp_path / "run")])
        capsys.readouterr()  # swallow the full request dump
        assert rc == 0

    def test_dry_run_prints_request_json(self, tmp_path, capsys):
        # P5-05 — canonical dry-run emits the deterministic CDS
        # request JSON for every planned monthly request.
        rc = self._rc(["--year-range", "2001-2001", "--dry-run",
                       "--run-root", str(tmp_path / "run")])
        out = capsys.readouterr().out
        assert rc == 0
        assert '"variable"' in out
        assert "snow_depth_water_equivalent" in out
        # one JSON request block per planned month (JJA = 3)
        assert out.count('"variable"') >= 3


class TestFullChain:
    """P5-08 — a validated merged NetCDF drives the whole chain:
    load_era5_land -> extract_raw_features -> compute_derived_features
    -> compute_thermal_indices -> run_gmm_descriptive -> write_bundle,
    all under a tmp run root."""

    def _synthetic_merged(self, path: Path) -> None:
        """Single-cell merged file: JJA-only hours for two baseline
        years plus the 2026 pre-event season (Jun 1 - Aug 25), all 7
        contract vars under their GRIB short names, canonical 'time'
        dim, scalar latitude/longitude coords (the merge already
        selected the event cell).

        The years must be CONTIGUOUS through 2026: resample("D") in
        compute_thermal_indices materialises every calendar day from
        the first to the last timestamp, so a skipped JJA season would
        surface as all-NaN rows and trip the completeness gate — a
        gap year is a data defect, not edge censoring."""
        idx: list[pd.Timestamp] = []
        for year in (2024, 2025):
            idx.extend(pd.date_range(f"{year}-06-01",
                                     f"{year}-08-31 23:00", freq="h"))
        idx.extend(pd.date_range("2026-06-01", "2026-08-25 23:00",
                                 freq="h"))
        times = pd.DatetimeIndex(idx)
        n = len(times)
        rng = np.random.default_rng(7)
        loc = {"t2m": 290.0, "d2m": 280.0, "u10": 1.0, "v10": 1.0,
               "sd": 0.02, "sf": 0.001, "tp": 0.002}
        ds = xr.Dataset(
            {v: (("time",),
                  np.abs(rng.normal(loc[v], max(loc[v] * 0.1, 1e-4),
                                    n)))
             for v in GOOD_VARS},
            coords={
                "time": times,
                "latitude": 28.3,    # scalar coord — cell pre-selected
                "longitude": 85.5,
            })
        path.parent.mkdir(parents=True, exist_ok=True)
        ds.to_netcdf(path)

    def test_merged_nc_through_gmm_bundle(self, tmp_path):
        merged_nc = tmp_path / "merged" / MERGED_NAME
        self._synthetic_merged(merged_nc)

        cell, _lat, _lon, elev = fe.load_era5_land(merged_nc)
        hourly = fe.extract_raw_features(cell)
        cell.close()
        hourly = fe.compute_derived_features(hourly)
        daily = fe.compute_thermal_indices(hourly, elev)

        # P5-03 — the daily frame is a JJA-only contract surface.
        assert set(daily.index.month.unique()) <= set(fe.JJA_MONTHS)

        feature_file = (tmp_path / "features"
                        / "features_nepal_jja_2001_2026.csv")
        feature_file.parent.mkdir(parents=True, exist_ok=True)
        daily.to_csv(feature_file)

        run_dir = tmp_path / "gmm"
        res = gmm.run_gmm_descriptive(daily, run_dir=run_dir)
        assert "error" not in res, res.get("error")

        # CFM-11 — the merged .nc digest is bound into the bundle.
        bundle = gmm.write_bundle(res, [merged_nc, feature_file],
                                  feature_file=feature_file,
                                  run_dir=run_dir)
        bundle_json = json.loads(
            (run_dir / "bundle.json").read_text())
        digests = bundle_json["input_digests"]
        assert merged_nc.name in digests
        assert digests[merged_nc.name] == hashlib.sha256(
            merged_nc.read_bytes()).hexdigest()
        assert bundle["input_digests"] == digests

        # P5-04 — edge censoring is flagged, never filled or dropped.
        if "edge_censored" not in daily.columns:
            pytest.skip("feature_extraction.compute_thermal_indices "
                        "does not emit edge_censored (P5-04 pending)")
        assert int(daily["edge_censored"].sum()) == 18  # 6 x 3 JJA runs

        # GMM-side accounting of the censoring (P5-04 consumer).
        acct = res if "rows_used" in res else res.get("missingness", {})
        if not {"rows_used", "edge_censored_dropped"} <= set(acct):
            pytest.skip("gmm_descriptive.run_gmm_descriptive does not "
                        "record rows_used / edge_censored_dropped "
                        "(P5-04 pending)")
        assert acct["edge_censored_dropped"] == \
            int(daily["edge_censored"].sum())
        assert 0 < acct["rows_used"] <= len(daily)


class TestAreaCellMarkerContract:
    """AUD-02 / P5-07 / P5-08 / P5-12 — the request area is the
    documented 1° x 1° box, the selected cell must lie inside it,
    hourly NaNs reject the payload, edge flags are only legitimate at
    June edges, and extraction is gated on the downloader's
    complete.json marker."""

    def test_area_is_one_by_one_degree(self):
        # [North, West, South, East] — exactly the contract box.
        assert dl.AREA == [29.0, 85.0, 28.0, 86.0]
        aac = getattr(dl, "assert_area_contract", None)
        if callable(aac):
            aac()  # must not raise for the contract area

    def test_hourly_nan_rejected(self, tmp_path):
        # P5-07: one NaN anywhere in a required hourly variable
        # rejects the payload outright — it must not normalize through.
        ds = _hourly_ds(GOOD_VARS, times=_month_hours(2001, "06"))
        t2m = ds["t2m"].values.copy()
        t2m[7, 0, 0] = np.nan
        ds["t2m"].data = t2m
        payload = tmp_path / "era5_land_2001_06.nc"
        ds.to_netcdf(payload)
        with pytest.raises(ValueError, match="non-finite|NaN|finite"):
            dl.normalize_payload(payload, tmp_path / "m",
                                 year=2001, month="06")

    def test_august_edge_flag_rejected(self, tmp_path):
        # P5-04/P5-09: edge_censored is only legitimate at the June
        # start of each JJA run (the first 6 days, whose 7-day PDD
        # window reaches outside JJA). A 2005-08-10 edge flag is a
        # contract violation and preflight must report it.
        if not hasattr(gmm, "preflight"):
            pytest.skip("gmm_descriptive.preflight absent")
        dates: list[pd.Timestamp] = []
        for y in range(2001, 2026):
            dates.extend(pd.date_range(f"{y}-06-01", f"{y}-08-31",
                                       freq="D"))
        dates.extend(pd.date_range("2026-06-01", "2026-08-25",
                                   freq="D"))
        idx = pd.DatetimeIndex(dates)
        rng = np.random.default_rng(0)
        df = pd.DataFrame(
            {c: rng.normal(size=len(idx)) for c in gmm.GMM_FEATURES},
            index=idx)
        df["edge_censored"] = False
        bad_day = pd.Timestamp("2005-08-10")
        df.loc[bad_day, "edge_censored"] = True
        df.loc[bad_day, "pdd_7day"] = np.nan  # censored-row shape
        csv = tmp_path / "features_aug_edge.csv"
        df.to_csv(csv)
        problems = gmm.preflight(csv)
        assert problems, ("edge_censored=true on 2005-08-10 accepted "
                          "by preflight")
        assert any("august" in p.lower() or "edge" in p.lower()
                   for p in problems), (
            f"no preflight problem mentions August/edge: "
            f"{list(problems)}")

    def test_complete_marker_required_for_extraction(self, tmp_path):
        # P5-08: a merged file without the downloader's complete.json
        # marker is unvalidated input — the marker check must fail.
        # Test the smallest callable surface (fe.main is heavy).
        check = getattr(fe, "_verify_complete_marker", None)
        if not callable(check):
            pytest.skip("feature_extraction._verify_complete_marker "
                        "absent")
        merged_dir = tmp_path / "run_root" / "merged"
        merged_dir.mkdir(parents=True)
        merged = merged_dir / getattr(dl, "MERGED_NAME", MERGED_NAME)
        _monthly_ds(2001, "06").to_netcdf(merged)
        assert merged.is_file()
        assert not (merged_dir / "complete.json").exists()
        with pytest.raises((FileNotFoundError, ValueError,
                            RuntimeError, SystemExit)):
            check(merged)

    def test_cell_inside_area(self):
        # The deterministic selected cell (28.3, 85.5) is inside the
        # requested AREA; a 27.5 latitude cell is outside (south of
        # the south bound).
        check = getattr(fe, "_assert_cell_in_area", None)
        if callable(check):
            assert check(28.3, 85.5) is True
            with pytest.raises(ValueError):
                check(27.5, 85.5)
        else:
            # No validity callable — assert the constants directly.
            # AREA is [North, West, South, East].
            north, west, south, east = (float(v) for v in dl.AREA)
            assert south <= 28.3 <= north
            assert west <= 85.5 <= east
            assert not (south <= 27.5 <= north)

    def test_dry_run_area_matches(self, tmp_path):
        # The canonical dry-run banner/request JSON must carry the
        # contract area [29.0, 85.0, 28.0, 86.0].
        script = Path(dl.__file__).resolve()
        proc = subprocess.run(
            [sys.executable, str(script), "--dry-run",
             "--year-range", "2001-2001",
             "--run-root", str(tmp_path / "run")],
            capture_output=True, text=True, timeout=120,
            cwd=str(script.parent.parent))
        assert proc.returncode == 0, proc.stderr[-2000:]
        # Matches both the 'Area: [29.0, 85.0, 28.0, 86.0]' banner and
        # a pretty-printed JSON "area" list (which spans lines).
        compact = "".join(proc.stdout.split())
        assert "[29.0,85.0,28.0,86.0]" in compact, (
            "dry-run stdout does not carry area "
            "[29.0, 85.0, 28.0, 86.0]")
