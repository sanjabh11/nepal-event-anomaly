"""P5-C intake: frozen-anchor ERA5-Land bytes -> daily feature frames.

Reads the byte-bound acquisition evidence (two authorized channels, per
the P5 record's owner-directed amendments):

  * CDS ``reanalysis-era5-land-timeseries`` zips — contiguous 2001-2025
    hourly for ``t2m, d2m, u10, v10, tp`` at each frozen anchor box.
    Non-JJA months are acquisition evidence only; they are filtered out
    deterministically here and counted in the report.
  * Google Earth Engine ``ECMWF/ERA5_LAND/HOURLY`` CSVs —
    ``snow_depth_water_equivalent`` + ``snowfall_hourly`` per grid cell
    inside the same anchor boxes, JJA 2001-2025.

Reduction semantics are identical across channels: the spatial mean
over the acquired cells of each frozen +-0.1 degree anchor box, applied
per timestep, before any feature computation.  Downstream feature math
is entirely the existing pipeline (``extract_raw_features``,
``compute_derived_features``, ``compute_thermal_indices``) — this module
adds no new feature definitions and no authority.

Fail-closed: a missing snow file, an unexpected member set, a duplicate
timestamp, or a spatial/domain violation raises rather than patching.
Nothing here authorizes intake, freeze, clustering, or any operational
claim.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import numpy as np
import pandas as pd
import xarray as xr

TIMESERIES_TEMPLATE = "era5land_ts_{basin}_hma_2001-2025.zip"
SNOW_TEMPLATE = "era5land_snow_{basin}_{year}_hma_JJA_ee.csv"
SNOW_YEARS = range(2001, 2026)
JJA_MONTHS = (6, 7, 8)

#: Operative (HMA-derived, RDS-7952-attributed) frozen anchors — the
#: authoritative set is retrieval/anchor_derivation_record.json; these
#: boxes mirror the acquisition requests and are asserted against the
#: payload grids, never used to choose new coordinates.
ANCHOR_BOXES = {
    "koshi":   {"lat": (27.9, 28.2), "lon": (86.9, 87.1)},
    "gandaki": {"lat": (28.5, 28.8), "lon": (84.6, 84.9)},
    "karnali": {"lat": (29.6, 29.9), "lon": (82.0, 82.4)},
}

TS_VARS = ("t2m", "d2m", "u10", "v10", "tp")
EE_SD = "snow_depth_water_equivalent"
EE_SF = "snowfall_hourly"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def verify_inputs_against_manifest(evidence_root: Path,
                                   manifest: Mapping,
                                   relpaths: Iterable[str]) -> None:
    """FMX-02 floor: verify the canonical source manifest BEFORE parsing.

    The manifest's evidence bytes are re-verified through the governed
    floor (containment, symlink policy, declared digests) and every
    consumed relpath must be a declared member — a tampered file, a
    stale sidecar, or an undeclared path fails closed here, never
    silently at parse time.
    """
    if manifest is None:
        raise ValueError(
            "source manifest is required for the real intake path — "
            "unverified bytes are inadmissible")
    from nepal.research_v0._hashing import verify_source_evidence
    problems = verify_source_evidence(manifest)
    if problems:
        raise ValueError(
            f"feature manifest evidence problems: {problems}")
    # membership + digest are checked against manifest-relative
    # relpaths resolved under the manifest's own evidence_root — the
    # intake's local root is not trusted for the binding
    manifest_root = Path(manifest["evidence_root"])
    declared = {f["relpath"]: f["sha256"]
                for f in manifest.get("source_files", ())}
    for rel in relpaths:
        if rel not in declared:
            raise ValueError(
                f"input {rel!r} is not a declared manifest member")
        p = manifest_root / rel
        actual = _sha256(p.read_bytes())
        if actual != declared[rel]:
            raise ValueError(
                f"input {rel!r} digest {actual} != manifest "
                f"{declared[rel]}")
        # stale sidecar probe: when a .sha256 sidecar exists it must
        # agree with the live bytes
        side = p.with_name(p.name + ".sha256")
        if side.exists():
            recorded = side.read_text().strip().split()[0]
            if recorded != actual:
                raise ValueError(
                    f"stale sidecar on {rel!r}: recorded {recorded} "
                    f"!= live {actual}")


def load_feature_manifest(evidence_root: Path) -> Mapping:
    """The canonical four-role manifest set's feature role."""
    rec = json.loads(
        (Path(evidence_root) / "retrieval" / "role_manifests_v0.json")
        .read_text())
    return rec["feature"]


def _load_timeseries_hourly(zip_path: Path, basin: str) -> pd.DataFrame:
    """Zip -> member netCDFs -> concat -> box-mean -> hourly df.

    Members are grouped CDS netCDF files inside the retrieval zip; all
    declared members are read and concatenated on ``valid_time``.
    """
    box = ANCHOR_BOXES[basin]
    frames = []
    with zipfile.ZipFile(zip_path) as z:
        members = [i for i in z.infolist() if i.filename.endswith(".nc")]
        if not members:
            raise ValueError(f"{zip_path.name}: no .nc members")
        with tempfile.TemporaryDirectory() as td:
            for member in members:
                extracted = Path(z.extract(member, td))
                ds = xr.open_dataset(extracted)
                if "latitude" in ds.dims or "longitude" in ds.dims:
                    lat = ds["latitude"].values
                    lon = ds["longitude"].values
                    if not (box["lat"][0] - 0.051 <= lat.min()
                            and lat.max() <= box["lat"][1] + 0.051
                            and box["lon"][0] - 0.051 <= lon.min()
                            and lon.max() <= box["lon"][1] + 0.051):
                        raise ValueError(
                            f"{zip_path.name}/{member.filename}: "
                            f"payload grid {lat.min():.3f}-"
                            f"{lat.max():.3f}N/{lon.min():.3f}-"
                            f"{lon.max():.3f}E escapes the frozen "
                            f"{basin} anchor box")
                    cell = ds.mean(dim=("latitude", "longitude"))
                else:
                    # timeseries members arrive spatially reduced —
                    # the scalar coords still must sit inside the box
                    lat = float(ds["latitude"].values)
                    lon = float(ds["longitude"].values)
                    if not (box["lat"][0] - 0.051 <= lat
                            <= box["lat"][1] + 0.051
                            and box["lon"][0] - 0.051 <= lon
                            <= box["lon"][1] + 0.051):
                        raise ValueError(
                            f"{zip_path.name}/{member.filename}: "
                            f"point {lat:.3f}N/{lon:.3f}E escapes the "
                            f"frozen {basin} anchor box")
                    cell = ds
                present = [v for v in TS_VARS if v in ds.data_vars]
                if not present:
                    raise ValueError(
                        f"{zip_path.name}/{member.filename}: carries "
                        f"none of {list(TS_VARS)}")
                t = pd.to_datetime(cell["valid_time"].values)
                frames.append(pd.DataFrame(
                    {v: cell[v].values for v in present},
                    index=pd.DatetimeIndex(t, name="time")))
                ds.close()
    # Members partition the variable set (CDS groups them) OR the time
    # axis; a (time, var) cell sourced by two members is an anomaly —
    # raise rather than coalesce.
    seen = set()
    for f in frames:
        for v in f.columns:
            overlap = seen.intersection(zip(f.index, [v] * len(f)))
            if overlap:
                raise ValueError(
                    f"{zip_path.name}: (time, {v}) cells sourced by "
                    "multiple members")
            seen.update(zip(f.index, [v] * len(f)))
    df = frames[0]
    for f in frames[1:]:
        df = df.combine_first(f)
    df = df.sort_index()
    missing = [v for v in TS_VARS if v not in df.columns]
    if missing:
        raise ValueError(
            f"{zip_path.name}: no member carries vars {missing}")
    if df.index.duplicated().any() or df.isna().any().any():
        raise ValueError(f"{zip_path.name}: duplicate or gappy axis")
    return df


def _load_snow_hourly(evidence_root: Path, basin: str) -> pd.DataFrame:
    """EE per-cell CSVs -> per-timestamp box-mean -> hourly sd/sf."""
    frames = []
    for year in SNOW_YEARS:
        path = evidence_root / SNOW_TEMPLATE.format(
            basin=basin, year=year)
        if not path.exists():
            raise FileNotFoundError(
                f"missing snow payload {path.name} — fail closed, no "
                "partial-year substitution")
        with open(path, newline="", encoding="utf-8") as fh:
            reader = csv.DictReader(fh)
            rows = [
                (pd.Timestamp(int(r["time"]), unit="ms", tz="UTC")
                 .tz_convert(None),
                 float(r[EE_SD]) if r[EE_SD] not in ("", "None") else
                 np.nan,
                 float(r[EE_SF]) if r[EE_SF] not in ("", "None") else
                 np.nan)
                for r in reader]
        df = pd.DataFrame(rows, columns=["time", "sd", "sf"])
        frames.append(df.groupby("time", sort=True).mean())
    df = pd.concat(frames).sort_index()
    if df.index.duplicated().any():
        raise ValueError(f"{basin} snow frames: duplicate timestamps")
    return df


def build_basin_hourly(evidence_root: Path, basin: str,
                       manifest: Optional[Mapping] = None) -> tuple:
    """Return (hourly_df, report) for one frozen anchor basin.

    The JJA filter is applied to the contiguous timeseries leg here;
    excluded non-JJA rows are counted in the report, never analyzed.
    When ``manifest`` is supplied (the canonical feature-role
    manifest), every consumed byte is verified against it BEFORE
    parsing — the FMX-02 floor; real runs must pass it.
    """
    ts_zip = evidence_root / TIMESERIES_TEMPLATE.format(basin=basin)
    if not ts_zip.exists():
        raise FileNotFoundError(f"missing timeseries payload {ts_zip}")
    if manifest is not None:
        consumed = [
            f"era5-multibasin/{TIMESERIES_TEMPLATE.format(basin=basin)}"
        ] + [
            f"era5-multibasin/"
            f"{SNOW_TEMPLATE.format(basin=basin, year=y)}"
            for y in range(2001, 2026)]
        verify_inputs_against_manifest(
            evidence_root, manifest, consumed)
    ts = _load_timeseries_hourly(ts_zip, basin)
    jja = ts[ts.index.month.isin(JJA_MONTHS)]
    excluded = int(len(ts) - len(jja))
    snow = _load_snow_hourly(evidence_root, basin)
    merged = jja.join(snow, how="inner")
    if merged[["sd", "sf"]].isna().any().any():
        raise ValueError(
            f"{basin}: NaN in merged snow columns — fail closed")
    year_counts = merged.groupby(merged.index.year).size().to_dict()
    report = {
        "basin": basin,
        "ts_zip": ts_zip.name,
        "ts_rows_total": int(len(ts)),
        "non_jja_rows_excluded": excluded,
        "jja_rows": int(len(jja)),
        "snow_rows": int(len(snow)),
        "merged_rows": int(len(merged)),
        "merged_per_year": {int(k): int(v) for k, v in
                            sorted(year_counts.items())},
        "first_step": str(merged.index[0]),
        "last_step": str(merged.index[-1]),
    }
    return merged, report


def build_daily_feature_frame(hourly: pd.DataFrame,
                              model_elev_m: float) -> pd.DataFrame:
    """Run the existing feature pipeline on a merged 7-var hourly frame.

    Units entering here are native (K, m, m/s); conversions and
    accumulation semantics are the pipeline's job, not ours.
    """
    import feature_extraction as fe  # nepal/ sibling module
    ds = xr.Dataset(
        {v: ("valid_time", hourly[v].values) for v in
         ("t2m", "d2m", "u10", "v10", "tp", "sd", "sf")},
        coords={"valid_time": hourly.index.values})
    df = fe.extract_raw_features(ds)
    df = fe.compute_derived_features(df)
    return fe.compute_thermal_indices(df, model_elev_m)


# ------------------------------------------------------------------
# Regime-input frame assembly (frozen feature-frame contract)
# ------------------------------------------------------------------

#: Daily feature columns feeding the regime fit — the ten
#: pre-registered quantities (wind_dir is carried as its sin/cos pair).
DAILY_FEATURE_COLS = (
    "t2m_daily", "d2m_daily", "tp_daily", "sf_daily", "sd_daily",
    "wind_speed_daily", "wind_dir_sin", "wind_dir_cos", "rh_daily",
    "pdd_daily",
)

#: Declared era boundary for the era-stability ablation — a fixed
#: calendar split, never data-derived.
ERA_BOUNDARY_YEAR = 2013


def build_regime_frame(basin_daily: Mapping[str, pd.DataFrame],
                       basin_elev_m: Mapping[str, float]) -> tuple:
    """Assemble the multi-basin regime input frame.

    Adds the bookkeeping carriers the frozen contract requires —
    ``unit_id`` (basin), ``date``, ``basin_group``, ``season``
    (all rows JJA by construction), ``era`` (declared calendar split
    at ``ERA_BOUNDARY_YEAR``), ``elevation_m`` (basin model elevation
    from the digested lake-population means) — and preserves
    ``edge_censored`` so the missingness policy stays honest.
    Returns (frame, provenance) where provenance carries the
    per-predictor fields the FMX audit requires.
    """
    frames = []
    for basin in sorted(basin_daily):
        df = basin_daily[basin].copy()
        missing = [c for c in DAILY_FEATURE_COLS if c not in df.columns]
        if missing:
            raise ValueError(
                f"{basin}: daily frame missing feature cols {missing}")
        df = df.reset_index().rename(
            columns={df.reset_index().columns[0]: "date"})
        df["unit_id"] = basin
        df["basin_group"] = basin
        df["season"] = "JJA"
        df["era"] = np.where(
            pd.to_datetime(df["date"]).dt.year < ERA_BOUNDARY_YEAR,
            "pre_2013", "post_2013")
        df["elevation_m"] = float(basin_elev_m[basin])
        frames.append(df)
    frame = pd.concat(frames, ignore_index=True).sort_values(
        ["unit_id", "date"]).reset_index(drop=True)
    provenance = {
        "source_lineage": {
            "timeseries_channel": "cds reanalysis-era5-land-timeseries",
            "snow_channel": "google-earth-engine ECMWF/ERA5_LAND/HOURLY",
            "reduction": "box-mean over frozen +-0.1deg anchor boxes",
            "aggregation": "hourly -> daily via feature_extraction",
            "elevation_source":
                "mean Lake_Elev of RDS7952-attributed HMA lakes"},
        "availability_semantics":
            "ERA5-Land reanalysis published ~5-day lag; all JJA "
            "2001-2025 values precede any declared cutoff",
        "missingness_policy": "listwise; edge_censored rows carry "
            "NaN pdd_7day by construction (warm-up boundary)",
        "era_boundary": ERA_BOUNDARY_YEAR,
        "effort_axis": "none — reanalysis is assimilation-complete; "
            "effort ablation requires a declared waiver",
        "units": {"t2m_daily": "degC", "d2m_daily": "degC",
                  "tp_daily": "mm/day", "sf_daily": "mm/day w.e.",
                  "sd_daily": "mm w.e.", "wind_speed_daily": "m/s",
                  "wind_dir_sin": "1", "wind_dir_cos": "1",
                  "rh_daily": "%", "pdd_daily": "degC-day"},
    }
    return frame, provenance


def write_basin_outputs(evidence_root: Path, basin: str,
                        daily: pd.DataFrame, report: dict,
                        out_dir: Optional[Path] = None) -> dict:
    """Write the daily feature CSV + report JSON + sha256 sidecars."""
    out = out_dir or evidence_root / "features"
    out.mkdir(parents=True, exist_ok=True)
    csv_path = out / f"features_{basin}_hma_jja_2001_2025.csv"
    payload = daily.to_csv().encode("utf-8")
    csv_path.write_bytes(payload)
    csv_path.with_suffix(".csv.sha256").write_text(_sha256(payload) + "\n")
    rep_path = out / f"feature_report_{basin}.json"
    rep_payload = json.dumps(report, indent=2).encode("utf-8")
    rep_path.write_bytes(rep_payload)
    rep_path.with_suffix(".json.sha256").write_text(
        _sha256(rep_payload) + "\n")
    return {"csv": str(csv_path), "report": str(rep_path),
            "csv_sha256": _sha256(payload)}
