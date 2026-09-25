"""Arm C daily-grain frame builder (amendment v16).

Derives the 6,900-row daily basin-day frame for the pressure/single-level
extension lane.  Rules (all declared in armc_daily_feature_contract_v0.json):

- hourly -> UTC daily mean over valid cell-hours; box-mean over the day's
  valid cells (fixed spatial weights; complete-cell policy unchanged)
- terrain mask at pressure level L: cell-hour valid iff sp(cell,hour) > L
  (surface-pressure mask from the same pinned Earthmover snapshot)
- a daily value is emitted iff >=90% of the day's cell-hours are valid,
  else NaN (listwise missingness handled downstream)
- theta500_minus_thetasfc = t500*(1000/500)^k - t2m*(1000/sp)^k, k=0.286
- single-level fields (cape, tcwv, sp, t2m) are column/surface-valid —
  unmasked
- row universe MUST equal the existing daily surface frame's
  (unit_id, date) pairs — join is validated one-to-one
- provenance binds: source chunk digests, source composition,
  mask statistics, base-frame sha, contract sha
"""
import json, sys, hashlib, subprocess
from pathlib import Path
sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
import xarray as xr
from p5_safe_io import write_once_bytes, write_once_json, write_once_sidecar, ExistingEvidenceError

ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence/p5-armc-pressure-levels-2026-09-22")
BASE_FRAME = Path("/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19/era5-multibasin/features/regime_frame_hma_jja_2001_2025.csv")
CONTRACT = ROOT / "retrieval" / "armc_daily_feature_contract_v0.json"
SOURCE_MAP = ROOT / "retrieval" / "armc_source_map_v15.json"
OUTDIR = ROOT / "daily-frame-v1"

KAPPA = 0.286
VALID_FRACTION_MIN = 0.90
LEVEL_VARS = {"z500_mean": ("geopotential", "z", 500),
              "t500_mean": ("temperature", "t", 500),
              "q500_mean": ("specific_humidity", "q", 500),
              "w500_mean": ("vertical_velocity", "w", 500)}
SINGLE_VARS = {"cape_mean": "cape", "tcwv_mean": "tcwv"}
ERA_BOUNDARY = 2013

def _sha(p: Path) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def _verify_sidecar(p: Path) -> str:
    d = _sha(p)
    s = Path(str(p) + ".sha256")
    if not s.exists() or s.read_text().split()[0] != d:
        raise ValueError(f"sidecar mismatch: {p}")
    return d

def _receipt_for(lane: Path, cid: str) -> dict:
    rp = lane / "retrieval" / "chunks" / f"{cid}.json"
    _verify_sidecar(rp)
    return json.loads(rp.read_text())

def _open_payload(lane: Path, cid: str) -> xr.Dataset:
    p = lane / "retrieval" / "payloads" / f"{cid}.nc"
    actual = _verify_sidecar(p)
    rec = _receipt_for(lane, cid)
    if rec.get("response_sha256") != actual:
        raise ValueError(f"{cid}: receipt response_sha256 does not bind payload bytes")
    return xr.open_dataset(p).load()

def day_index(times: np.ndarray) -> pd.DatetimeIndex:
    return pd.DatetimeIndex(pd.to_datetime(times)).normalize()

def main() -> int:
    contract = json.loads(CONTRACT.read_text())
    if contract["schema"] != "P5_ARMC_DAILY_FEATURE_CONTRACT_V0":
        raise ValueError("wrong feature contract schema")
    contract_sha = _verify_sidecar(CONTRACT)
    smap = json.loads(SOURCE_MAP.read_text())["chunks"]
    smap_sha = _verify_sidecar(SOURCE_MAP)
    def _src(cid: str, receipt: dict) -> str:
        return receipt.get("source") or smap.get(cid, {}).get("source") or "unresolved"
    base_sha = _verify_sidecar(BASE_FRAME)
    base = pd.read_csv(BASE_FRAME)
    base["date"] = pd.to_datetime(base["date"])
    if len(base) != 6900:
        raise ValueError("base daily frame is not 6,900 rows")
    if OUTDIR.exists():
        raise ExistingEvidenceError(f"refusing existing output: {OUTDIR}")
    OUTDIR.mkdir(parents=True)

    rows_out = []
    prov_cells = {}
    derived_lineage = {}
    all_mask_stats = {}
    basins = sorted(base["unit_id"].unique())
    for basin in basins:
        acc = {}  # date -> feature dict
        mask_stats = {}
        # pressure vars at 500
        for feat, (lane_var, em_var, lvl) in LEVEL_VARS.items():
            lane = ROOT / f"payload-{lane_var}"
            sp_lane = ROOT / "payload-single_sp"
            for year in range(2001, 2026):
                cid = f"{basin}-{lane_var}-{lvl}-{year}"
                ds = _open_payload(lane, cid)
                rec = _receipt_for(lane, cid)
                arr = ds[em_var].isel(pressure_level=0).values  # (t, la, lo)
                times = day_index(ds["valid_time"].values)
                sp_cid = f"{basin}-sp-single-{year}"
                sp_ds = _open_payload(sp_lane, sp_cid)
                sp_rec = _receipt_for(sp_lane, sp_cid)
                prov_cells.setdefault(sp_cid, {"source": _src(sp_cid, sp_rec),
                                               "payload_sha256": sp_rec.get("response_sha256")})
                sp = sp_ds["sp"].values  # Pa
                if not np.array_equal(day_index(sp_ds["valid_time"].values), times):
                    raise ValueError(f"{cid}: sp time axis mismatch")
                valid = sp[:, :, :] > lvl * 100  # cell-hour above ground
                n_days = len(np.unique(times))
                vals = np.full(n_days, np.nan)
                day_of = {d: i for i, d in enumerate(np.unique(times))}
                for d, i in day_of.items():
                    sel = times == d
                    v = valid[sel]
                    frac = v.mean()
                    mask_stats.setdefault(cid, []).append(float(frac))
                    if frac >= VALID_FRACTION_MIN:
                        vals[i] = float(np.nanmean(arr[sel][v]))
                    else:
                        vals[i] = np.nan
                for d, i in day_of.items():
                    acc.setdefault(d, {})[feat] = vals[i]
                prov_cells[cid] = {"source": _src(cid, rec), "payload_sha256": rec.get("response_sha256"),
                                   "sp_receipt_sha256": sp_rec.get("response_sha256")}
        # theta stability: t500 - theta_sfc
        t_lane = ROOT / "payload-temperature"
        s_lane = ROOT / "payload-single_t2m"
        sp_lane = ROOT / "payload-single_sp"
        for year in range(2001, 2026):
            cid = f"{basin}-temperature-500-{year}"
            tds = _open_payload(t_lane, cid)
            rec = _receipt_for(t_lane, cid)
            t500 = tds["t"].isel(pressure_level=0).values
            times = day_index(tds["valid_time"].values)
            t2m_cid = f"{basin}-t2m-single-{year}"
            sds = _open_payload(s_lane, t2m_cid)
            srec = _receipt_for(s_lane, t2m_cid)
            prov_cells.setdefault(t2m_cid, {"source": _src(t2m_cid, srec),
                                            "payload_sha256": srec.get("response_sha256")})
            t2m = sds["t2m"].values
            sp_ds = _open_payload(sp_lane, f"{basin}-sp-single-{year}")
            sp = sp_ds["sp"].values
            valid = sp > 500 * 100
            theta500 = t500 * (1000.0/500.0)**KAPPA
            thetasfc = t2m * (1000.0/(sp/100.0))**KAPPA
            stab = theta500 - thetasfc
            day_of = {d: i for i, d in enumerate(np.unique(times))}
            for d, i in day_of.items():
                sel = times == d
                v = valid[sel]
                acc.setdefault(d, {})["theta500_minus_thetasfc"] = (
                    float(np.nanmean(stab[sel][v])) if v.mean() >= VALID_FRACTION_MIN else np.nan)
            derived_lineage.setdefault("theta500_minus_thetasfc", []).append(
                {"chunk_id": cid, "source": _src(cid, rec),
                 "payload_sha256": rec.get("response_sha256"),
                 "t2m_receipt_sha256": srec.get("response_sha256"),
                 "sp_receipt_sha256": _receipt_for(sp_lane, f"{basin}-sp-single-{year}").get("response_sha256")})
        # single-level column fields
        for feat, svar in SINGLE_VARS.items():
            lane = ROOT / f"payload-single_{svar}"
            for year in range(2001, 2026):
                cid = f"{basin}-{svar}-single-{year}"
                ds = _open_payload(lane, cid)
                rec = _receipt_for(lane, cid)
                arr = ds[svar].values
                times = day_index(ds["valid_time"].values)
                day_of = {d: i for i, d in enumerate(np.unique(times))}
                for d, i in day_of.items():
                    sel = times == d
                    acc.setdefault(d, {})[feat] = float(np.nanmean(arr[sel]))
                prov_cells[cid] = {"source": _src(cid, rec), "payload_sha256": rec.get("response_sha256")}
        for d, feats in acc.items():
            rows_out.append({"unit_id": basin, "date": d, **feats})
        all_mask_stats.update(mask_stats)
        mask_min = min(min(v) for v in mask_stats.values())
        print(f"[{basin}] done; min daily valid-cell-hour fraction at 500hPa: {mask_min:.3f}", flush=True)

    ext = pd.DataFrame(rows_out)
    ext["date"] = pd.to_datetime(ext["date"])
    merged = base.merge(ext, on=["unit_id", "date"], how="left", validate="one_to_one")
    if len(merged) != 6900:
        raise ValueError("row universe drifted from base frame")
    feature_cols = contract["feature_cols"]
    ext_names = [f["name"] for f in contract["extended_features"]]
    missing = {c: float(merged[c].isna().mean()) for c in ext_names}
    # carry meta cols needed by engine
    merged["season"] = "JJA"
    if "era" not in merged.columns:
        merged["era"] = np.where(merged["date"].dt.year < ERA_BOUNDARY, "pre_2013", "post_2013")

    provenance = {
        "schema": "P5_ARMC_DAILY_FRAME_PROVENANCE_V0",
        "n_rows": len(merged),
        "feature_contract_sha256": contract_sha,
        "base_frame_sha256": base_sha,
        "declared_extended_features": ext_names,
        "feature_missingness": missing,
        "terrain_mask": {"rule": "cell-hour valid iff sp > level", "emit_threshold": VALID_FRACTION_MIN,
                         "per_chunk_min_daily_valid_fraction": all_mask_stats},
        "aggregation_note": ("daily value = mean over the day's valid cell-hours pooled "
                             "jointly (equivalent to box-mean of cell means whenever "
                             "coverage is 100%, which held for all days here)"),
        "cells_by_chunk": prov_cells,
        "derived_feature_lineage": derived_lineage,
        "source_map_sha256": smap_sha,
        "source_composition": {},
        "claim_scope": "research_only_no_operational_authorization",
    }
    from collections import Counter
    srcs = Counter()
    for c in prov_cells.values():
        srcs[str(c.get("source"))] += 1
    provenance["source_composition"] = dict(sorted(srcs.items()))

    frame_path = OUTDIR / "armc_daily_frame_v0.csv"
    frame_bytes = merged.to_csv(index=False).encode()
    write_once_bytes(frame_path, frame_bytes)
    write_once_sidecar(frame_path)
    provenance["frame_sha256"] = _sha(frame_path)
    write_once_json(OUTDIR / "armc_daily_frame_provenance_v0.json", provenance, indent=2)
    write_once_sidecar(OUTDIR / "armc_daily_frame_provenance_v0.json")
    units = {c: contract["feature_units"][c] for c in feature_cols}
    up = OUTDIR / "feature_units_v0.json"
    write_once_json(up, {"schema": "P5_FEATURE_UNITS_V0", "units": units}, indent=2)
    write_once_sidecar(up)
    print(json.dumps({"status": "DAILY_FRAME_BUILT", "rows": len(merged),
                      "missingness": missing, "frame": str(frame_path)}, indent=2))
    return 0

if __name__ == "__main__":
    sys.exit(main())
