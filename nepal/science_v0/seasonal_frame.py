"""SEASONAL-01 (P5 amendment v3): basin-season frame builder.

Aggregates the byte-verified daily JJA regime frame into basin-year
objects (3 basins x 25 seasons = 75 rows) under the owner-approved
six-feature seasonal contract.  This is a DIFFERENT estimand from the
daily P5-A2 run — seasonal hydroclimate types, never a rescue of the
daily negative result.

Feature contract (locked, p5_amendment_v3_seasonal_estimand.json):
    t2m_mean           mean of JJA daily t2m_daily               [degC]
    d2m_mean           mean of JJA daily d2m_daily               [degC]
    pdd_sum            sum of JJA daily pdd_daily                [degC-day]
    tp_q95             95th percentile of JJA daily tp_daily     [mm/day]
    wet_spell_max_days longest consecutive run of tp_daily >= 1  [days]
    sd_delta           last valid sd_daily - first valid sd_daily [mm w.e.]

Carrier columns: unit_id, basin_group, season ("JJA"), season_year,
year_block (5-year blocks) — a metadata/resampling carrier only;
NO year-block stability predicate is executed in this lane —
era (pre_2013/post_2013), elevation_m (basin constant),
date — a SYNTHETIC index date on a fixed 365-day grid anchored at
2001-07-16.  The index date carries season-year identity only (the
engine's cadence gate requires uniform spacing; calendar JJA
midpoints vary by 365/366 days across leap years).  It is not a
physical observation date and is declared as such in provenance.

Missingness: listwise at the basin-year level — a basin-year whose
declared features cannot all be computed is dropped and ledgered,
never silently imputed.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

#: The locked seasonal feature contract (amendment v3).
SEASONAL_FEATURES = ("t2m_mean", "d2m_mean", "pdd_sum", "tp_q95",
                     "wet_spell_max_days", "sd_delta")

#: Feature units for the seasonal contract.
SEASONAL_UNITS = {"t2m_mean": "degC", "d2m_mean": "degC",
                  "pdd_sum": "degC-day", "tp_q95": "mm/day",
                  "wet_spell_max_days": "days", "sd_delta": "mm w.e."}

#: Daily columns the seasonal contract consumes.
_REQUIRED_DAILY = ("date", "t2m_daily", "d2m_daily", "pdd_daily",
                   "tp_daily", "sd_daily", "unit_id", "basin_group",
                   "season", "era", "elevation_m")

#: JJA = June 1 .. August 31 inclusive = 92 days per basin-year.
JJA_DAYS = 92

#: Wet-spell threshold (amendment v3).
WET_SPELL_THRESHOLD_MM = 1.0

#: Declared year-block carrier — a metadata/resampling carrier only.
#: NO year-block stability predicate is executed by the engine in
#: this lane; the blocks exist solely for downstream stratification
#: and resampling declaration.
YEAR_BLOCKS = ("2001_2005", "2006_2010", "2011_2015",
               "2016_2020", "2021_2025")

#: Declared input domain — the only units and season-year bounds the
#: adapter accepts.  Anything outside this domain is contamination
#: and fails closed (ValueError), never a silent ledger drop.
EXPECTED_UNITS = ("gandaki", "karnali", "koshi")
YEAR_MIN, YEAR_MAX = 2001, 2025

#: Synthetic 365-day index grid anchor (JJA midpoint).  Index dates
#: carry season-year identity only; the uniform 365-day spacing is
#: what the engine's declared cadence "365D" binds.
INDEX_ANCHOR = "2001-07-16"


def _sha_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_input_bytes(daily_csv: Path) -> str:
    """Byte-verify the daily frame against its sha256 sidecar.
    Returns the verified digest; raises (fails closed) on any
    mismatch — a substituted daily frame can never seed a seasonal
    frame."""
    sidecar = Path(str(daily_csv) + ".sha256")
    digest = _sha_file(daily_csv)
    if not sidecar.exists():
        raise ValueError(
            f"daily frame sidecar {sidecar} missing — the seasonal "
            "adapter refuses an unbound input")
    declared = sidecar.read_text().split()[0].strip()
    if digest != declared:
        raise ValueError(
            f"daily frame digest {digest} != sidecar {declared} — "
            "byte substitution fails closed")
    return digest


def _wet_spell_max(tp: np.ndarray) -> int:
    """Longest consecutive run of tp_daily >= threshold."""
    best = run = 0
    for v in tp:
        if np.isfinite(v) and v >= WET_SPELL_THRESHOLD_MM:
            run += 1
            best = max(best, run)
        else:
            run = 0
    return int(best)


def _year_block(year: int) -> str:
    for blk in YEAR_BLOCKS:
        lo, hi = (int(x) for x in blk.split("_"))
        if lo <= year <= hi:
            return blk
    raise ValueError(f"season_year {year} outside declared "
                     f"year-block partition {YEAR_BLOCKS}")


def build_seasonal_frame(daily_csv: Path | str,
                         out_dir: Path | str
                         ) -> dict[str, Any]:
    """Build the 75-row basin-season frame from the byte-verified
    daily JJA frame.  Writes the seasonal CSV + sha256 sidecar +
    provenance record into ``out_dir`` and returns a build receipt.

    Fails closed: input bytes must verify against the daily sha256
    sidecar; input-domain contamination (non-JJA season labels,
    dates outside June 1..August 31, undeclared units, years outside
    2001..2025, duplicated or non-consecutive daily coverage within
    a basin-year, or a required column that cannot be coerced to
    float64) is refused with ValueError — never silently dropped;
    basin-years that cannot produce all six declared features are
    dropped and ledgered (never imputed); basin carriers
    (basin_group, era, elevation) must be constant within a unit or
    the basin-year is dropped and ledgered.  ``year_block`` is
    carried as metadata/resampling declaration only — no year-block
    stability predicate is executed in this lane."""
    daily_csv = Path(daily_csv)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    daily_digest = verify_input_bytes(daily_csv)
    daily = pd.read_csv(daily_csv)
    missing = [c for c in _REQUIRED_DAILY if c not in daily.columns]
    if missing:
        raise ValueError(f"daily frame lacks required columns "
                         f"{missing}")
    daily["date"] = pd.to_datetime(daily["date"], errors="raise")
    # --- strict input-domain refusals: contamination fails closed
    # with ValueError; it is never silently ledger-dropped ---
    non_jja = daily.loc[daily["season"] != "JJA", "season"].unique()
    if len(non_jja):
        raise ValueError(
            f"non-JJA season label in daily frame — input "
            f"contamination: {sorted(str(s) for s in non_jja)}")
    month = daily["date"].dt.month
    if ((month < 6) | (month > 8)).any():
        raise ValueError(
            "date outside JJA window in daily frame — input "
            "contamination (a seasonal row must fall within "
            "June 1..August 31 of its year)")
    bad_units = sorted(str(u) for u in
                       set(daily["unit_id"]) - set(EXPECTED_UNITS))
    if bad_units:
        raise ValueError(
            f"undeclared unit in daily frame — {bad_units} not in "
            f"{EXPECTED_UNITS}")
    years = daily["date"].dt.year
    if ((years < YEAR_MIN) | (years > YEAR_MAX)).any():
        raise ValueError(
            f"year outside declared domain {YEAR_MIN}..{YEAR_MAX} "
            "in daily frame — input contamination")
    for col in ("t2m_daily", "d2m_daily", "pdd_daily", "tp_daily",
                "sd_daily", "elevation_m"):
        try:
            daily[col] = pd.to_numeric(daily[col],
                                       errors="raise")
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"daily column {col!r} cannot be coerced to "
                "float64 — input contamination") from exc
    ledger = {"dropped_basin_years": [], "notes": []}
    rows = []
    anchor = pd.Timestamp(INDEX_ANCHOR)
    for (unit, year), grp in daily.groupby(
            ["unit_id", daily["date"].dt.year], sort=True):
        year = int(year)
        grp = grp.sort_values("date")
        if grp["date"].duplicated().any():
            raise ValueError(
                "non-unique or non-consecutive daily coverage — "
                f"input contamination: duplicated dates in "
                f"({unit}, {year})")
        if len(grp) != JJA_DAYS:
            ledger["dropped_basin_years"].append(
                {"unit": str(unit), "year": year,
                 "reason": f"{len(grp)} daily rows != {JJA_DAYS} "
                           "JJA days"})
            continue
        expected_dates = pd.date_range(f"{year}-06-01",
                                       f"{year}-08-31", freq="D")
        if not np.array_equal(grp["date"].to_numpy(),
                              expected_dates.to_numpy()):
            raise ValueError(
                "non-unique or non-consecutive daily coverage — "
                f"input contamination: ({unit}, {year}) has "
                f"{JJA_DAYS} rows but not the complete "
                "Jun1..Aug31 set")
        basin = grp["basin_group"].unique()
        era = grp["era"].unique()
        elev = grp["elevation_m"].unique()
        if len(basin) != 1 or len(era) != 1 or len(elev) != 1:
            ledger["dropped_basin_years"].append(
                {"unit": str(unit), "year": year,
                 "reason": "basin/era/elevation carriers are not "
                           "constant within the basin-year"})
            continue
        t2m = grp["t2m_daily"].to_numpy(dtype=np.float64)
        d2m = grp["d2m_daily"].to_numpy(dtype=np.float64)
        pdd = grp["pdd_daily"].to_numpy(dtype=np.float64)
        tp = grp["tp_daily"].to_numpy(dtype=np.float64)
        sd = grp["sd_daily"].to_numpy(dtype=np.float64)
        sd_valid = sd[np.isfinite(sd)]
        rec = {"unit_id": str(unit), "basin_group": str(basin[0]),
               "season": "JJA", "season_year": year,
               "year_block": _year_block(year),
               "era": str(era[0]),
               "elevation_m": float(elev[0]),
               # synthetic index date — declared, uniform 365-day
               # grid; carries season-year identity only
               "date": (anchor + pd.Timedelta(days=365)
                        * (year - 2001)).strftime("%Y-%m-%d"),
               "t2m_mean": float(np.nanmean(t2m)),
               "d2m_mean": float(np.nanmean(d2m)),
               "pdd_sum": float(np.nansum(pdd)),
               "tp_q95": float(np.nanpercentile(tp, 95)),
               "wet_spell_max_days": _wet_spell_max(tp),
               "sd_delta": (float(sd_valid[-1] - sd_valid[0])
                            if len(sd_valid) else np.nan)}
        if any(not np.isfinite(rec[f]) for f in SEASONAL_FEATURES):
            ledger["dropped_basin_years"].append(
                {"unit": str(unit), "year": year,
                 "reason": "one or more declared seasonal features "
                           "non-finite"})
            continue
        rows.append(rec)
    frame = pd.DataFrame(rows)
    if len(frame):
        frame = frame.sort_values(["unit_id", "season_year"]
                                  ).reset_index(drop=True)
    out_csv = out_dir / "seasonal_frame_jja_2001_2025.csv"
    frame.to_csv(out_csv, index=False)
    digest = _sha_file(out_csv)
    Path(str(out_csv) + ".sha256").write_text(f"{digest}  "
                                              f"{out_csv.name}\n")
    provenance = {
        "schema": "SEASONAL_FRAME_PROVENANCE_V0",
        "record_type": "seasonal_frame_provenance",
        "source_lineage": {
            "daily_frame": daily_csv.name,
            "daily_frame_sha256": daily_digest,
            "derivation": "JJA basin-year aggregation under the "
                          "amendment-v3 six-feature contract"},
        "feature_formulas": {
            "t2m_mean": "mean of JJA daily t2m_daily",
            "d2m_mean": "mean of JJA daily d2m_daily",
            "pdd_sum": "sum of JJA daily pdd_daily",
            "tp_q95": "95th percentile of JJA daily tp_daily",
            "wet_spell_max_days": "longest consecutive run of days "
                                  "with tp_daily >= 1.0 mm",
            "sd_delta": "last valid JJA sd_daily minus first valid "
                        "JJA sd_daily"},
        "index_date_semantics": "synthetic 365-day index grid "
                                "anchored 2001-07-16 (JJA midpoint) "
                                "— carries season-year identity "
                                "only; NOT a physical observation "
                                "date; declared cadence '365D'",
        "year_block_carrier": "5-year blocks "
                              "(2001_2005..2021_2025) — a "
                              "metadata/resampling carrier only; "
                              "NO year-block stability predicate is "
                              "executed by the engine in this lane",
        "missingness_policy": "listwise — invalid basin-years "
                              "dropped and ledgered",
        "units": dict(SEASONAL_UNITS),
        "n_rows": int(len(frame)),
        "n_basins": int(frame["unit_id"].nunique())
        if len(frame) else 0,
        "years": sorted(int(y) for y in frame["season_year"])
        if len(frame) else [],
        "frame_sha256": digest,
        "build_ledger": ledger}
    prov_path = out_dir / "seasonal_frame_provenance_v0.json"
    prov_path.write_text(json.dumps(provenance, indent=1,
                                    sort_keys=True) + "\n")
    Path(str(prov_path) + ".sha256").write_text(
        f"{_sha_file(prov_path)}  {prov_path.name}\n")
    return {"frame": frame, "csv": out_csv, "sha256": digest,
            "provenance": prov_path, "ledger": ledger,
            "daily_sha256": daily_digest}
