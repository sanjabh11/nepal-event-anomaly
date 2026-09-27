"""Tien Shan thermal run — ONE inferential pass for the declared E6 estimand
under the frozen stub + tienshan_amendment_v1 (both-nulls rule).

This protocol fetches t2m ONLY, so armc_routeb_analyze_v2.analyse() cannot be
invoked verbatim (its build_daily unconditionally merges tp/sf/t2m and the
undeclared precip/snow series are absent by design). This wrapper therefore
replicates the SEALED v2 math for the single declared exposure, using only
v2/v1 primitives and identical constants/seeds:

  per unit:  roll = rolled(daily_t2m, mean, L=7); RefCache(washout) -> value,
             z, mid-rank pct vs same-calendar-month reference;
             pseudo_outcomes(event month, end=-1) -> MC pools
  aggregate: mean_pct/mean_z/sign_test/mc_null_mean_{pct,z}/
             cluster_bootstrap{mean_z,mean_pct} — same call order and seeds
             (SEED_MC 20260926, SEED_BOOT 20260927, R=10000, B=10000) as v2.
  null B:    DOY-matched +/-15d reference + composite-mean null after
             p5-hma-diagnostic-2026-09-26/doy_matched_diag_v1.py
             (R=10000, seed 20260928).

Verdict (amendment): PASS iff E6 mean_z > 0 AND MC p_upper < 0.05 under BOTH
nulls; otherwise NULL. Bootstrap CI descriptive only.

The analyzed event per unit is the stub's primary (most recent day-dated);
the episode map restricts member_ids accordingly.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
import armc_routeb_analyze as V1  # noqa: E402
import armc_routeb_analyze_v2 as V2  # noqa: E402
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402

HALF = 15
R_DOY = 10_000
SEED_DOY = 20260928
AUTHORITY = dict(V1.AUTHORITY_CEILING, confirmatory_claim_authorized=False)
E6 = ("t2m", "mean", -7, -1)


def _sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _doy_dist(a, b):
    d = np.abs(a - b)
    return np.minimum(d, 366 - d)


def daily_t2m(hourly, sel_ids, require_complete=True):
    """build_daily restricted to the single declared variable."""
    s = V2.daily_from_hourly(V1.merge_box_series(hourly["t2m"], sel_ids),
                             False, require_complete)
    if len(s):
        s = s.reindex(pd.date_range(s.index.min(), s.index.max(), freq="D"))
    return s


def per_unit_rows(hourly, sels, ep, events, mdate, mem_box):
    """Replicates analyse()'s unit loop for E6 only."""
    box_members = {}
    for mid, b in mem_box.items():
        box_members.setdefault(V2.box_key(b), set()).add(mid)
    units, coverage, pools, drop_log = [], [], {}, {}
    for u in ep["units"]:
        pm = sorted(u["member_ids"],
                    key=lambda m: events[m]["adjudication"]["event_time_interval"]["start"])[0]
        d = mdate(pm)
        bk = V2.box_key(mem_box[pm])
        sel_ids = {s["selection_id"] for s in sels if V2.box_key(s["event_box"]) == bk}
        wash = sorted({mdate(m) for m in box_members[bk]})
        daily = pd.DataFrame({"t2m": daily_t2m(hourly, sel_ids)})
        raw = daily_t2m(hourly, sel_ids, require_complete=False)
        drop_log[u["unit_id"]] = int(raw.notna().sum() - daily["t2m"].notna().sum())
        series, agg, aa, bb = E6
        L = bb - aa + 1
        rc = V2.RefCache(V2.rolled(daily, series, agg, L), wash, L)
        x, z, p, n_ref, reason = rc.stat(d + bb * V2.DAY)
        zs, ps, pdates = rc.pseudo_outcomes(d.month, bb)
        pools[u["unit_id"]] = (zs, ps)
        coverage.append({"unit_id": u["unit_id"], "exposure": "E6_t2m_7d",
                         "status": "COMPUTABLE" if reason is None else "NOT_COMPUTABLE",
                         "reason": reason, "n_ref": n_ref})
        units.append({"unit_id": u["unit_id"], "primary_member": pm,
                      "event_date": str(d.date()), "lake": u.get("lake"),
                      "lake_cluster": V2.lake_key(u), "basin": u.get("basin"),
                      "era": u.get("era"), "gorkha_window": False,
                      "n_selections_merged": len(sel_ids),
                      "E6_t2m_7d": {"value": x, "z": z, "pct": p, "n_ref": n_ref},
                      "_roll": V2.rolled(daily, series, agg, L), "_wash": wash, "_d": d})
    return units, coverage, pools, drop_log, box_members


def doy_matched(units):
    """DOY-matched E6 z per unit + composite-mean null (doy_matched_diag_v1 method)."""
    rows, pools, units_meta = [], [], {}
    for u in units:
        roll, wash, d = u["_roll"], u["_wash"], u["_d"]
        valid = roll.notna().to_numpy() & ~V2.washout_mask(roll.index, wash, 7)
        vals, doy = roll.to_numpy(), roll.index.dayofyear.to_numpy()
        end = d - 1 * V2.DAY
        if end not in roll.index or pd.isna(roll.loc[end]):
            units_meta[u["unit_id"]] = "NOT_COMPUTABLE"
            continue
        near = valid & (_doy_dist(doy, end.dayofyear) <= HALF)
        ref = vals[near]
        if len(ref) < V2.MIN_REF or ref.std() == 0:
            units_meta[u["unit_id"]] = "INSUFFICIENT_DOY_REF"
            continue
        mu, sd = ref.mean(), ref.std()
        rows.append(float((roll.loc[end] - mu) / sd))
        pools.append((ref - mu) / sd)
        units_meta[u["unit_id"]] = {"z": rows[-1], "n_ref": int(len(ref))}
    rng = np.random.default_rng(SEED_DOY)
    z = np.array(rows)
    draws = np.stack([p[rng.integers(0, len(p), R_DOY)] for p in pools]).mean(axis=0)
    obs = float(z.mean())
    return {"n": int(len(z)), "mean_z_doy": obs, "median_z_doy": float(np.median(z)),
            "n_pos": int((z > 0).sum()),
            "mc_p_upper_doy": float((1 + (draws >= obs).sum()) / (R_DOY + 1)),
            "mc_p_two_sided_doy": float(min(1, 2 * min((1 + (draws >= obs).sum()),
                                                     (1 + (draws <= obs).sum())) / (R_DOY + 1))),
            "null_q025_q975": [float(np.quantile(draws, .025)), float(np.quantile(draws, .975))],
            "R": R_DOY, "seed": SEED_DOY, "half_window_days": HALF,
            "per_unit": units_meta}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=("coverage", "full"), required=True)
    ap.add_argument("--lane-root", action="append", required=True)
    ap.add_argument("--inventory", action="append", required=True)
    ap.add_argument("--episode-map", required=True)
    ap.add_argument("--decision", required=True)
    ap.add_argument("--hmaglofdb", required=True)
    ap.add_argument("--amendment", required=True)
    ap.add_argument("--stub", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    hourly, sels, clim, ep, events, mdate, mem_box = V2.load_context(
        a.lane_root, a.inventory, a.episode_map, a.decision)
    units, coverage, pools, drop_log, box_members = per_unit_rows(
        hourly, sels, ep, events, mdate, mem_box)

    result = {"coverage_rows": coverage,
              "coverage_summary": {"E6_t2m_7d": {
                  "COMPUTABLE": sum(1 for c in coverage if c["status"] == "COMPUTABLE"),
                  "NOT_COMPUTABLE": sum(1 for c in coverage if c["status"] != "COMPUTABLE"),
                  "reasons": {}}},
              "partial_day_values_dropped_per_unit_t2m": drop_log}
    for c in coverage:
        if c["reason"]:
            result["coverage_summary"]["E6_t2m_7d"]["reasons"][c["reason"]] = \
                result["coverage_summary"]["E6_t2m_7d"]["reasons"].get(c["reason"], 0) + 1

    if a.mode == "full":
        rng_mc = np.random.default_rng(V2.SEED_MC)
        rng_bt = np.random.default_rng(V2.SEED_BOOT)
        vals = np.array([r["E6_t2m_7d"]["pct"] if r["E6_t2m_7d"]["pct"] is not None else np.nan
                         for r in units])
        zv = np.array([r["E6_t2m_7d"]["z"] if r["E6_t2m_7d"]["z"] is not None else np.nan
                       for r in units])
        ok = ~np.isnan(vals)
        ids = [r["unit_id"] for r, k in zip(units, ok) if k]
        entry = {"n": int(ok.sum()), "n_of": len(units)}
        if ok.sum():
            entry.update({"mean_pct": float(vals[ok].mean()),
                          "mean_z": float(np.nanmean(zv)),
                          "median_z": float(np.nanmedian(zv)),
                          "n_pct_ge_0.9": int((vals[ok] >= 0.9).sum()),
                          "n_pct_le_0.1": int((vals[ok] <= 0.1).sum()),
                          "sign_test": V2.sign_test(vals[ok]),
                          "mc_null_mean_pct": V2.mc_null(float(vals[ok].mean()),
                                                         [pools[i][1] for i in ids], rng_mc),
                          "mc_null_mean_z": V2.mc_null(float(np.nanmean(zv)),
                                                       [pools[i][0] for i in ids], rng_mc),
                          "boot_mean_z": V2.cluster_bootstrap(zv,
                              [r["lake_cluster"] for r in units], rng_bt),
                          "boot_mean_pct": V2.cluster_bootstrap(vals,
                              [r["lake_cluster"] for r in units], rng_bt)})
        result["aggregates_primary"] = {"E6_t2m_7d": entry}
        doy = doy_matched(units)
        result["doy_matched_E6"] = doy

        sm = {"mean_z": entry.get("mean_z"),
              "mc_p_upper": (entry.get("mc_null_mean_z") or {}).get("p_upper"),
              "boot_ci95_descriptive": (entry.get("boot_mean_z") or {}).get("ci95"),
              "n": entry.get("n")}
        dy = {"mean_z_doy": doy.get("mean_z_doy"),
              "mc_p_upper_doy": doy.get("mc_p_upper_doy"), "n": doy.get("n")}
        sm_pass = sm["mean_z"] is not None and sm["mean_z"] > 0 \
            and sm["mc_p_upper"] is not None and sm["mc_p_upper"] < 0.05
        dy_pass = dy["mean_z_doy"] is not None and dy["mean_z_doy"] > 0 \
            and dy["mc_p_upper_doy"] is not None and dy["mc_p_upper_doy"] < 0.05
        result["amendment_verdict"] = {
            "rule": "PASS iff E6 mean_z > 0 AND MC p_upper < 0.05 under BOTH "
                    "same-month and +/-15d DOY-matched nulls; bootstrap CI descriptive",
            "same_month": {**sm, "passes": sm_pass},
            "doy_matched": {**dy, "passes": dy_pass},
            "overall": "PASS" if (sm_pass and dy_pass) else "NULL"}

    doc = {"schema": "P5_TIENSHAN_COVERAGE_V1" if a.mode == "coverage"
                     else "P5_TIENSHAN_THERMAL_REANALYSIS_V1",
           "amendment_sha256": _sha(a.amendment),
           "stub_sha256": _sha(a.stub),
           "claim_scope": "inference limited to the declared E6 both-nulls rule; "
                          "all other emitted quantities descriptive",
           "authority": AUTHORITY,
           "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "run_provenance": {"code_file_ts": _sha(__file__),
                              "code_file_v2": _sha(V2.__file__),
                              "code_file_v1": _sha(V1.__file__),
                              "argv": sys.argv, "python": sys.version.split()[0]},
           "inputs_sha256": {"inventories": {Path(p).name: _sha(p) for p in a.inventory},
                             "episode_map": _sha(a.episode_map), "decision": _sha(a.decision),
                             "hmaglofdb": _sha(a.hmaglofdb),
                             "amendment": _sha(a.amendment), "stub": _sha(a.stub)},
           "lanes": a.lane_root,
           "constants": {"MIN_REF": V2.MIN_REF, "WASH_DAYS": V2.WASH_DAYS,
                         "R_MC": V2.R_MC, "B_BOOT": V2.B_BOOT,
                         "SEED_MC": V2.SEED_MC, "SEED_BOOT": V2.SEED_BOOT,
                         "R_DOY": R_DOY, "SEED_DOY": SEED_DOY, "DOY_HALF": HALF,
                         "adaptation": "E6-only path replicating sealed v2 math; "
                                       "tp/sf not part of this protocol"},
           "units": [{k: v for k, v in u.items() if not k.startswith("_")} for u in units],
           **result}
    out = Path(a.out)
    write_once_json(out, doc, indent=2)
    write_once_sidecar(out)
    print(json.dumps({"mode": a.mode, "out": str(out),
                      "verdict": (result.get("amendment_verdict") or {}).get("overall"),
                      "units": len(units)}, default=str))


if __name__ == "__main__":
    main()
