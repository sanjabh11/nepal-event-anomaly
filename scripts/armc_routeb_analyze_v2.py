"""Route-B analyser v2 — post-hoc DIAGNOSTIC re-analysis of the Nepal cohort.

Implements p5-nepal-reanalysis-2026-09-26/plan/nepal_diagnostic_reanalysis_plan_v1.json
(Steps 1.2-1.8 and 1.10).  The sealed v1 analyser is imported, never
modified: hourly loading, duplicate/conflict guards and box merging are
v1 code.  New in v2:

  * configurable windows (trigger -3..0 / -3..-1, v1 -10..-4, early -17..-11)
  * thermal exposures from t2m (7d/14d mean, 7d PDD, 72 h warming)
  * mid-rank percentile statistic next to z
  * Monte-Carlo random pseudo-event-date null (same months, same treatment)
  * superposed-epoch daily composite with pointwise MC bands
  * lake-cluster bootstrap, sign test, BH bookkeeping, driver/era strata
  * coverage matrix
  * DEFECT GUARD: a daily value is only formed from a complete set of 24
    hourly validities (v1 formed partial-day sums at selection edges);
    the v1-compat regression mode reproduces v1 bytes WITHOUT the guard
    and reports which v1 windows touched a partial day.

Claim ceiling: descriptive, post-hoc, development-set diagnostics only.
"""
from __future__ import annotations

import argparse
import csv
import datetime
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import armc_routeb_analyze as V1  # noqa: E402
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402

DAY = pd.Timedelta(days=1)
WASH_DAYS = 7
MIN_REF = 100
R_MC = 10_000
B_BOOT = 10_000
SEED_MC, SEED_BOOT = 20260926, 20260927
SEA_LAGS = tuple(range(-17, 4))

# name -> (series, agg, start_offset, end_offset)   (offsets inclusive, days rel. event)
EXPOSURES = {
    "E1_tp_trig0": ("tp", "sum", -3, 0),
    "E2_tp_trig1": ("tp", "sum", -3, -1),
    "E3_tp_v1": ("tp", "sum", -10, -4),
    "E4_tp_early": ("tp", "sum", -17, -11),
    "E5_sf_trig0": ("sf", "sum", -3, 0),
    "E6_t2m_7d": ("t2m", "mean", -7, -1),
    "E7_t2m_14d": ("t2m", "mean", -14, -1),
    "E8_pdd_7d": ("pdd", "sum", -7, -1),
    "E9_dt2m_72h": ("t2m", "diff3", -3, 0),
}
AUTHORITY = dict(V1.AUTHORITY_CEILING, confirmatory_claim_authorized=False)


def _sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


# ---------------------------------------------------------------- daily data
def daily_from_hourly(s: pd.Series, accum: bool, require_complete: bool = True) -> pd.Series:
    """UTC daily aggregate with v1 conventions (accumulations use the
    end-of-hour convention: validity - 1 h).  With require_complete, days
    lacking any of the 24 hourly validities are dropped (NaN)."""
    s = s.dropna()
    key = ((s.index - pd.Timedelta(hours=1)) if accum else s.index).normalize()
    g = s.groupby(key)
    out = g.sum() if accum else g.mean()
    if require_complete:
        out = out[g.count() == 24]
    out.index = out.index.tz_localize(None) if out.index.tz is not None else out.index
    return out.sort_index()


def build_daily(hourly: dict, sel_ids, require_complete: bool = True) -> pd.DataFrame:
    cols = {}
    for var, accum in (("tp", True), ("sf", True), ("t2m", False)):
        cols[var] = daily_from_hourly(V1.merge_box_series(hourly[var], sel_ids), accum,
                                      require_complete)
    df = pd.DataFrame(cols)
    if len(df):
        df = df.reindex(pd.date_range(df.index.min(), df.index.max(), freq="D"))
    df["pdd"] = (df["t2m"] - 273.15).clip(lower=0)
    return df


def rolled(df: pd.DataFrame, series: str, agg: str, length: int) -> pd.Series:
    """Value of the window ENDING on each day; NaN unless every day present."""
    x = df[series]
    if agg == "diff3":
        return x - x.shift(3)
    r = x.rolling(length, min_periods=length)
    return r.sum() if agg == "sum" else r.mean()


def washout_mask(index: pd.DatetimeIndex, wash_dates, length: int) -> np.ndarray:
    """True where the window [e-L+1, e] intersects [wd-7, wd+7] for any wd."""
    m = np.zeros(len(index), bool)
    for wd in wash_dates:
        lo = wd - WASH_DAYS * DAY
        hi = wd + (WASH_DAYS + length - 1) * DAY
        m |= (index >= lo) & (index <= hi)
    return m


def midrank_percentile(sorted_ref: np.ndarray, x: float) -> float:
    lt = np.searchsorted(sorted_ref, x, "left")
    le = np.searchsorted(sorted_ref, x, "right")
    return float((lt + 0.5 * (le - lt)) / len(sorted_ref))


class RefCache:
    """Per (unit-box, exposure): month -> sorted reference array."""

    def __init__(self, roll: pd.Series, wash_dates, length: int):
        valid = roll.notna().to_numpy() & ~washout_mask(roll.index, wash_dates, length)
        self.roll, self.valid = roll, valid
        self._by_month = {}

    def ref(self, month: int):
        if month not in self._by_month:
            sel = self.valid & (self.roll.index.month == month)
            vals = np.sort(self.roll.to_numpy()[sel])
            self._by_month[month] = vals if (len(vals) >= MIN_REF and vals.std() > 0) else None
        return self._by_month[month]

    def stat(self, end: pd.Timestamp):
        """(value, z, pct, n_ref, reason) for the window ending on `end`."""
        if end not in self.roll.index or pd.isna(self.roll.loc[end]):
            return None, None, None, 0, "window_incomplete"
        ref = self.ref(end.month)
        if ref is None:
            return None, None, None, 0, "reference_insufficient"
        x = float(self.roll.loc[end])
        return x, float((x - ref.mean()) / ref.std()), midrank_percentile(ref, x), len(ref), None

    def pseudo_outcomes(self, event_month: int, end_offset: int):
        """Exposure outcomes at every pseudo-event day in `event_month`
        (all years) whose window is computable and outside washout."""
        days = self.roll.index[self.roll.index.month == event_month]
        zs, ps, dates = [], [], []
        for d in days:
            e = d + end_offset * DAY
            if e not in self.roll.index:
                continue
            i = self.roll.index.get_loc(e)
            if not self.valid[i]:
                continue
            ref = self.ref(e.month)
            if ref is None:
                continue
            x = float(self.roll.iloc[i])
            zs.append((x - ref.mean()) / ref.std())
            ps.append(midrank_percentile(ref, x))
            dates.append(d)
        return np.array(zs), np.array(ps), pd.DatetimeIndex(dates)


# ---------------------------------------------------------------- inference
def mc_null(obs_mean: float, pools: list, rng) -> dict:
    """Composite-mean null: each unit contributes one random draw from its
    own pseudo-outcome pool per replicate."""
    pools = [p for p in pools if len(p)]
    if not pools or obs_mean is None:
        return {"p_upper": None, "p_two_sided": None, "null_mean": None}
    draws = np.stack([p[rng.integers(0, len(p), R_MC)] for p in pools])
    null = draws.mean(axis=0)
    mu = float(null.mean())
    return {"p_upper": float((1 + (null >= obs_mean).sum()) / (R_MC + 1)),
            "p_two_sided": float((1 + (np.abs(null - mu) >= abs(obs_mean - mu)).sum()) / (R_MC + 1)),
            "null_mean": mu,
            "null_q025": float(np.quantile(null, 0.025)), "null_q975": float(np.quantile(null, 0.975)),
            "null_q005": float(np.quantile(null, 0.005)), "null_q995": float(np.quantile(null, 0.995))}


def cluster_bootstrap(values: np.ndarray, groups: list, rng) -> dict:
    ok = ~np.isnan(values)
    vals, grp = values[ok], np.asarray(groups)[ok]
    if len(vals) < 2:
        return {"ci95": None, "n_clusters": int(len(set(grp)))}
    uniq = sorted(set(grp))
    members = [vals[grp == g] for g in uniq]
    idx = rng.integers(0, len(uniq), (B_BOOT, len(uniq)))
    sums = np.array([m.sum() for m in members]); cnts = np.array([len(m) for m in members])
    boot = sums[idx].sum(1) / cnts[idx].sum(1)
    return {"ci95": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))],
            "n_clusters": len(uniq)}


def sign_test(pcts: np.ndarray) -> dict:
    from scipy.stats import binomtest
    k = int((pcts > 0.5).sum()); n = int((pcts != 0.5).sum())
    return {"n_above_median": k, "n_informative": n,
            "p_two_sided": float(binomtest(k, n, 0.5).pvalue) if n else None}


def driver_stratum(driver: str) -> str:
    d = (driver or "").lower()
    if "rain" in d:
        return "RAIN"
    if any(t in d for t in ("temperat", "melt", "thaw")):
        return "THERMAL"
    if any(t in d for t in ("avalanche", "rockfall", "landslide", "calving", "debris")):
        return "MASS"
    return "UNKNOWN" if d == "unknown" else "OTHER"


def lake_key(unit: dict) -> str:
    lk = (unit.get("lake") or "").strip()
    return f"__distinct__{unit['unit_id']}" if lk.lower() in ("", "(unnamed)", "unknown", "unnamed") else lk


# ---------------------------------------------------------------- cohort
def load_context(lanes, inventories, episode_map, decision):
    hourly = {v: {} for v in V1.VARS}
    for lane in lanes:
        for var, d in V1.load_hourly(Path(lane)).items():
            hourly[var].update(d)
    invs = [json.loads(Path(p).read_text()) for p in inventories]
    sels = [s for i in invs for s in i["climatology_selections"]] + \
           [s for i in invs for s in i["antecedent_spillover_selections"]]
    clim = [s for i in invs for s in i["climatology_selections"]]
    ep = json.loads(Path(episode_map).read_text())
    dec = json.loads(Path(decision).read_text())
    events = {e["event_id"].rsplit(":", 1)[1]: e for e in dec["events"]
              if e["adjudication"]["disposition"] == "ELIGIBLE"}

    def mdate(mid):
        return pd.Timestamp(events[mid]["adjudication"]["event_time_interval"]["start"][:10])
    mem_box = {}
    for s in clim:
        for mid in s["member_ids"]:
            if mid in events and mdate(mid).month == s["calendar_month"]:
                mem_box[mid] = s["event_box"]
    return hourly, sels, clim, ep, events, mdate, mem_box


def box_key(box) -> str:
    return json.dumps(box, sort_keys=True, separators=(",", ":"))


def analyse(lanes, inventories, episode_map, decision, hmaglofdb, mode="full"):
    hourly, sels, clim, ep, events, mdate, mem_box = load_context(lanes, inventories, episode_map, decision)
    gf_driver = {}
    if hmaglofdb:
        with open(hmaglofdb, encoding="cp1252") as fh:
            gf_driver = {r["GF_ID"]: r["Driver_GLOF"] for r in csv.DictReader(fh)}
    # washout: every eligible member date sharing the box (any month)
    box_members = {}
    for mid, b in mem_box.items():
        box_members.setdefault(box_key(b), set()).add(mid)
    rng_mc = np.random.default_rng(SEED_MC)
    rng_bt = np.random.default_rng(SEED_BOOT)

    units, coverage, pools, sea_pools, sea_obs, contrast_pools = [], [], {}, {}, {}, {}
    daily_drop_log = {}
    for u in ep["units"]:
        mids = sorted(u["member_ids"], key=lambda m: events[m]["adjudication"]["event_time_interval"]["start"])
        pm = mids[0]
        d = mdate(pm)
        box = mem_box[pm]
        bk = box_key(box)
        sel_ids = {s["selection_id"] for s in sels if box_key(s["event_box"]) == bk}
        wash = sorted({mdate(m) for m in box_members[bk]})
        daily = build_daily(hourly, sel_ids, require_complete=True)
        raw = build_daily(hourly, sel_ids, require_complete=False)
        daily_drop_log[u["unit_id"]] = int(raw["tp"].notna().sum() - daily["tp"].notna().sum())
        gf = (u.get("gf_ids") or [pm])[0]
        row = {"unit_id": u["unit_id"], "primary_member": pm, "event_date": str(d.date()),
               "lake": u.get("lake"), "lake_cluster": lake_key(u), "basin": u.get("basin"),
               "era": u.get("era"), "gorkha_window": bool(u.get("gorkha_window")),
               "driver_episode_map": u.get("driver"), "driver_hmaglofdb": gf_driver.get(gf),
               "driver_stratum": driver_stratum(u.get("driver")),
               "n_selections_merged": len(sel_ids)}
        caches = {}
        for name, (series, agg, a, b) in EXPOSURES.items():
            L = (b - a + 1) if agg != "diff3" else 4
            rc = RefCache(rolled(daily, series, agg, L), wash, L)
            caches[name] = rc
            x, z, p, n_ref, reason = rc.stat(d + b * DAY)
            row[name] = {"value": x, "z": z, "pct": p, "n_ref": n_ref}
            coverage.append({"unit_id": u["unit_id"], "exposure": name,
                             "status": "COMPUTABLE" if reason is None else "NOT_COMPUTABLE",
                             "reason": reason, "n_ref": n_ref})
            zs, ps, pdates = rc.pseudo_outcomes(d.month, b)
            pools.setdefault(name, {})[u["unit_id"]] = (zs, ps)
            if name in ("E1_tp_trig0", "E4_tp_early"):
                contrast_pools.setdefault(u["unit_id"], {})[name] = dict(zip(pdates, ps))
        # C1 contrast
        e1, e4 = row["E1_tp_trig0"]["pct"], row["E4_tp_early"]["pct"]
        row["C1_trig_minus_early"] = (e1 - e4) if (e1 is not None and e4 is not None) else None
        cp = contrast_pools[u["unit_id"]]
        common = sorted(set(cp.get("E1_tp_trig0", {})) & set(cp.get("E4_tp_early", {})))
        pools.setdefault("C1_trig_minus_early", {})[u["unit_id"]] = (
            None, np.array([cp["E1_tp_trig0"][k] - cp["E4_tp_early"][k] for k in common]))
        # superposed epoch (daily, L=1)
        for series in ("tp", "t2m"):
            rc = RefCache(daily[series], wash, 1)
            for lag in SEA_LAGS:
                _, _, p, n_ref, reason = rc.stat(d + lag * DAY)
                sea_obs.setdefault(series, {}).setdefault(lag, {})[u["unit_id"]] = p
                coverage.append({"unit_id": u["unit_id"], "exposure": f"SEA_{series}_lag{lag:+d}",
                                 "status": "COMPUTABLE" if reason is None else "NOT_COMPUTABLE",
                                 "reason": reason, "n_ref": n_ref})
                if mode == "full":
                    sea_pools.setdefault(series, {}).setdefault(lag, {})[u["unit_id"]] = \
                        rc.pseudo_outcomes(d.month, lag)[1]
        units.append(row)

    cov_summary = {}
    for c in coverage:
        k = c["exposure"]
        cs = cov_summary.setdefault(k, {"COMPUTABLE": 0, "NOT_COMPUTABLE": 0, "reasons": {}})
        cs[c["status"]] += 1
        if c["reason"]:
            cs["reasons"][c["reason"]] = cs["reasons"].get(c["reason"], 0) + 1
    result = {"coverage_rows": coverage, "coverage_summary": cov_summary,
              "partial_day_values_dropped_per_unit": daily_drop_log}
    if mode == "coverage":
        return result, units

    # ------------------------------------------------ aggregates
    def aggregate(subset, with_inference=True):
        agg = {}
        for name in list(EXPOSURES) + ["C1_trig_minus_early"]:
            if name == "C1_trig_minus_early":
                vals = np.array([r[name] if r[name] is not None else np.nan for r in subset], float)
                zv = None
            else:
                vals = np.array([r[name]["pct"] if r[name]["pct"] is not None else np.nan for r in subset], float)
                zv = np.array([r[name]["z"] if r[name]["z"] is not None else np.nan for r in subset], float)
            ok = ~np.isnan(vals)
            ids = [r["unit_id"] for r, k in zip(subset, ok) if k]
            entry = {"n": int(ok.sum()), "n_of": len(subset)}
            if ok.sum():
                entry["mean_pct" if zv is not None else "mean_delta_pct"] = float(vals[ok].mean())
                if zv is not None:
                    entry.update({"mean_z": float(np.nanmean(zv)), "median_z": float(np.nanmedian(zv)),
                                  "n_pct_ge_0.9": int((vals[ok] >= 0.9).sum()),
                                  "n_pct_le_0.1": int((vals[ok] <= 0.1).sum())})
            if with_inference and ok.sum():
                if zv is not None:
                    entry["sign_test"] = sign_test(vals[ok])
                    entry["mc_null_mean_pct"] = mc_null(float(vals[ok].mean()),
                                                        [pools[name][i][1] for i in ids], rng_mc)
                    entry["mc_null_mean_z"] = mc_null(float(np.nanmean(zv)),
                                                      [pools[name][i][0] for i in ids], rng_mc)
                    entry["boot_mean_z"] = cluster_bootstrap(zv, [r["lake_cluster"] for r in subset], rng_bt)
                    entry["boot_mean_pct"] = cluster_bootstrap(vals, [r["lake_cluster"] for r in subset], rng_bt)
                else:
                    entry["mc_null_mean_delta"] = mc_null(float(vals[ok].mean()),
                                                          [pools[name][i][1] for i in ids], rng_mc)
                    entry["boot_mean_delta"] = cluster_bootstrap(vals, [r["lake_cluster"] for r in subset], rng_bt)
            agg[name] = entry
        return agg

    primary = [r for r in units if not r["gorkha_window"]]
    agg_primary = aggregate(primary)
    ps = [(n, agg_primary[n]["mc_null_mean_pct"]["p_two_sided"]) for n in EXPOSURES
          if agg_primary[n].get("mc_null_mean_pct", {}).get("p_two_sided") is not None]
    if ps:
        for (n, _), q in zip(ps, V1.bh_fdr(np.array([p for _, p in ps]))):
            agg_primary[n]["q_BH_descriptive"] = float(q)
    strata = {}
    for key, fn in (("driver", lambda r: r["driver_stratum"]), ("era", lambda r: r["era"])):
        for lvl in sorted({fn(r) for r in primary}):
            sub = [r for r in primary if fn(r) == lvl]
            strata[f"{key}={lvl}"] = {"n_units": len(sub),
                                      "label": "ANECDOTAL" if len(sub) < 5 else "DESCRIPTIVE",
                                      "unit_ids": [r["unit_id"] for r in sub],
                                      "exposures": aggregate(sub, with_inference=len(sub) >= 5)}
    sea = {}
    for series, lags in sea_obs.items():
        sea[series] = []
        for lag in SEA_LAGS:
            obs = {k: v for k, v in lags[lag].items()
                   if v is not None and not next(r for r in units if r["unit_id"] == k)["gorkha_window"]}
            m = float(np.mean(list(obs.values()))) if obs else None
            null = mc_null(m, [sea_pools[series][lag][k] for k in obs], rng_mc) if obs else {}
            sea[series].append({"lag": lag, "n": len(obs), "mean_pct": m,
                                "band95": [null.get("null_q025"), null.get("null_q975")],
                                "band99": [null.get("null_q005"), null.get("null_q995")],
                                "p_two_sided": null.get("p_two_sided")})
    result.update({"aggregates_primary": agg_primary,
                   "aggregates_all26": aggregate(units, with_inference=False),
                   "strata": strata, "superposed_epoch": sea})
    return result, units


def v1_compat_check(lanes, inventories, episode_map, decision, v34_path):
    """Reproduce v34 tp_antecedent_7d_sum primary z with v1 semantics
    (v1 box merge, group-member washout, no completeness guard) and report
    windows that contained a partial (<24 h) day."""
    hourly, sels, clim, ep, events, mdate, mem_box = load_context(lanes, inventories, episode_map, decision)
    v34 = {r["unit_id"]: r for r in json.loads(Path(v34_path).read_text())["unit_rows"]}
    clim_by = {s["selection_id"]: s for s in clim}
    spills = [s for s in sels if "dates" in s]
    rows, max_abs = [], 0.0
    for u in ep["units"]:
        mids = sorted(u["member_ids"], key=lambda m: events[m]["adjudication"]["event_time_interval"]["start"])
        pm = mids[0]; d = mdate(pm)
        sid = next(s["selection_id"] for s in clim if pm in s["member_ids"] and d.month == s["calendar_month"])
        box = clim_by[sid]["event_box"]
        sel_ids = {sid} | {s["selection_id"] for s in spills if s["event_box"] == box}
        wash = [mdate(x) for x in clim_by[sid]["member_ids"]]
        daily = build_daily(hourly, sel_ids, require_complete=False)
        s = daily["tp"].dropna()
        x, cov = V1.antecedent(pd.DataFrame({"tp": s}), "tp", d, True)
        ref = V1.ref_distribution(s, d.month, wash, True)
        z = float((x - ref.mean()) / ref.std()) if not np.isnan(x) and len(ref) >= MIN_REF else None
        tgt = v34[u["unit_id"]].get("tp_antecedent_7d_sum")
        tgt = float(tgt) if isinstance(tgt, (int, float)) and not np.isnan(tgt) else None
        diff = abs(z - tgt) if (z is not None and tgt is not None) else (0.0 if z is None and tgt is None else np.inf)
        max_abs = max(max_abs, diff)
        full = build_daily(hourly, sel_ids, require_complete=True)["tp"]
        win = pd.date_range(d - 10 * DAY, d - 4 * DAY)
        partial = [str(t.date()) for t in win if t in s.index and (t not in full.index or pd.isna(full.get(t)))]
        rows.append({"unit_id": u["unit_id"], "z_v2_compat": z, "z_v34": tgt, "abs_diff": diff,
                     "v1_window_partial_days": partial})
    return {"max_abs_diff": max_abs, "pass": bool(max_abs <= 1e-9), "rows": rows}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("coverage", "full"), required=True)
    ap.add_argument("--lane-root", action="append", required=True)
    ap.add_argument("--inventory", action="append", required=True)
    ap.add_argument("--compat-lane-root", action="append", default=[])
    ap.add_argument("--compat-inventory", action="append", default=[])
    ap.add_argument("--episode-map", required=True)
    ap.add_argument("--decision", required=True)
    ap.add_argument("--hmaglofdb", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--v34", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    compat = None
    if a.compat_lane_root:
        compat = v1_compat_check(a.compat_lane_root, a.compat_inventory, a.episode_map, a.decision, a.v34)
        if not compat["pass"]:
            print(json.dumps({"v1_compat": "FAIL", "max_abs_diff": compat["max_abs_diff"]}))
    res, units = analyse(a.lane_root, a.inventory, a.episode_map, a.decision, a.hmaglofdb, a.mode)
    doc = {"schema": "P5_NEPAL_DIAGNOSTIC_REANALYSIS_V1" if a.mode == "full" else "P5_NEPAL_COVERAGE_MATRIX_V1",
           "plan_sha256": _sha(a.plan),
           "claim_scope": "descriptive_post_hoc_diagnostic_only (development set; not confirmatory)",
           "authority": AUTHORITY,
           "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "run_provenance": {"code_file_v2": _sha(__file__), "code_file_v1": _sha(V1.__file__),
                              "argv": sys.argv, "python": sys.version.split()[0]},
           "inputs_sha256": {"inventories": {Path(p).name: _sha(p) for p in a.inventory},
                             "episode_map": _sha(a.episode_map), "decision": _sha(a.decision),
                             "hmaglofdb": _sha(a.hmaglofdb), "v34": _sha(a.v34)},
           "lanes": a.lane_root,
           "constants": {"MIN_REF": MIN_REF, "WASH_DAYS": WASH_DAYS, "R_MC": R_MC, "B_BOOT": B_BOOT,
                         "SEED_MC": SEED_MC, "SEED_BOOT": SEED_BOOT, "SEA_LAGS": list(SEA_LAGS),
                         "EXPOSURES": {k: list(v) for k, v in EXPOSURES.items()}},
           "implementation_guards": ["24-hour completeness required for every daily value (v1 edge-partial-day defect guard)"],
           "v1_compat_regression": compat,
           "units": units, **res}
    write_once_json(Path(a.out), doc, indent=2)
    write_once_sidecar(Path(a.out))
    summary = {"mode": a.mode, "v1_compat_pass": compat["pass"] if compat else None,
               "coverage": {k: v["COMPUTABLE"] for k, v in res["coverage_summary"].items() if not k.startswith("SEA")}}
    if a.mode == "full":
        summary["primary"] = {k: {kk: v.get(kk) for kk in ("n", "mean_z", "median_z", "mean_pct", "mean_delta_pct")}
                              for k, v in res["aggregates_primary"].items()}
    print(json.dumps(summary, indent=1, default=str))


if __name__ == "__main__":
    main()
