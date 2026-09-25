"""Route-B analysis v1 — descriptive antecedent-weather anomalies.

Implements armc_routeb_transform_contract_v0.json and fixes the defects
recorded in armc_v18_remediation_note_v0.json:

  * hourly timestamps are deduplicated BEFORE aggregation; conflicting
    duplicates (same validity, different value) are a hard error
  * theta-deficit mask: a 500 hPa level is below ground where surface
    pressure < 50000 Pa (was inverted); masking is applied per-cell
    before spatial averaging, with valid_fraction reported
  * each event MEMBER is mapped to the climatology selection matching
    its own (box, calendar-month); recurrent units' members use their
    own month frames
  * antecedent windows require all 7 consecutive dates present
  * reference = rolling CONSECUTIVE 7-day windows whose last day falls
    in the event's calendar month, across all years, minus +-7d washout
    around every eligible member interval in that box-month group
  * estimand is unit-level: earliest member is the primary observation,
    latest member is the predeclared recurrence sensitivity
  * frozen uncertainty rules implemented: naive CI + basin-cluster SE
    (3 clusters -> reported as diagnostic, NOT inferential proof),
    linear-year detrended sensitivity, BH-FDR across secondary exposures
  * run record binds payload shas, transform contract, code hash

Claim ceiling: descriptive only. No event-risk odds. Ever.
"""
import argparse, json, sys, hashlib, datetime
from pathlib import Path
import numpy as np
import pandas as pd
import xarray as xr

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from p5_safe_io import write_once_json, write_once_sidecar

def _sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

VARS = ["tp", "tcwv", "cape", "t2m", "sp", "sf", "t"]
ACCUM = {"tp", "sf"}
EXPOSURES = {"tp_antecedent_7d_sum": ("tp", "sum"),
             "tcwv_7d_mean": ("tcwv", "mean"),
             "cape_7d_mean": ("cape", "mean"),
             "theta_deficit_7d_mean": ("theta_deficit", "mean"),
             "sf_7d_sum": ("sf", "sum")}
PRIMARY = "tp_antecedent_7d_sum"
EQ_START, EQ_END = pd.Timestamp("2015-04-25"), pd.Timestamp("2015-06-30")
KAPPA = 0.286


def load_hourly(lane_root: Path):
    """var -> selection_id -> hourly Series (cell-mean), dedupe checked."""
    out = {v: {} for v in VARS}
    conflicts = []
    for var in VARS:
        for f in sorted((lane_root / f"payload-v17_{var}/retrieval/payloads").glob("*.nc")):
            sid = f.stem.replace(f"v17-", "").replace(f"-{var}", "")
            ds = xr.open_dataset(f)
            da = ds[var]
            da = da.isel(pressure_level=0) if "pressure_level" in da.dims else da
            vt = pd.DatetimeIndex(da["valid_time"].values, tz="UTC")
            vals = np.asarray(da.values).reshape(len(vt), -1).mean(axis=1)
            s = pd.Series(vals, index=vt)
            dup = s.index.duplicated(keep=False)
            if dup.any():
                grp = s[dup]
                for t, g in grp.groupby(grp.index):
                    if g.nunique() > 1:
                        conflicts.append({"var": var, "sel": sid,
                                          "time": str(t), "vals": list(g)})
                s = s[~s.index.duplicated(keep="first")]
            out[var][sid] = s
    if conflicts:
        raise ValueError(f"conflicting duplicate timestamps: {conflicts[:3]}")
    return out


def merge_box_series(hourly, sel_ids):
    """Merge a var's hourly series across a box's selections (clim+spill).
    Exact-duplicate timestamps are dropped; CONFLICTING duplicates
    (same validity, different value) are a hard error."""
    s = pd.concat([hourly[i] for i in sel_ids if i in hourly]).sort_index()
    dup = s.index.duplicated(keep=False)
    if dup.any():
        bad = [t for t, g in s[dup].groupby(s[dup].index) if g.nunique() > 1]
        if bad:
            raise ValueError(f"conflicting duplicate validities: {[str(t) for t in bad[:3]]}")
        s = s[~s.index.duplicated(keep="first")]
    return s


def daily_frame(var_series: dict, sp_series: pd.Series, t_series):
    """Build per-box daily frame honoring accumulation vs instantaneous
    conventions and per-cell theta masking. var_series maps var->Series."""
    df = {}
    for var, s in var_series.items():
        if var in ACCUM:
            df[var] = s.groupby((s.index - pd.Timedelta(hours=1))
                                .normalize()).sum()
        else:
            df[var] = s.resample("D").mean()
    frame = pd.DataFrame(df)
    # theta_deficit needs cell-level masking — handled upstream: here we
    # use the pre-masked series passed via var_series['theta_deficit']
    return frame


def load_cell_hourly(lane_root: Path, vars_):
    """var -> selection_id -> hourly DataFrame (rows=validity, cols=cells).
    Same dedupe/conflict rules as the cell-mean loader."""
    out = {v: {} for v in vars_}
    conflicts = []
    for var in vars_:
        for f in sorted((lane_root / f"payload-v17_{var}/retrieval/payloads").glob("*.nc")):
            sid = f.stem.replace("v17-", "").replace(f"-{var}", "")
            ds = xr.open_dataset(f)
            da = ds[var]
            da = da.isel(pressure_level=0) if "pressure_level" in da.dims else da
            vt = pd.DatetimeIndex(da["valid_time"].values, tz="UTC")
            arr = np.asarray(da.values).reshape(len(vt), -1)
            s = pd.DataFrame(arr, index=vt)
            dup = s.index.duplicated(keep=False)
            if dup.any():
                for t, g in s[dup].groupby(s[dup].index):
                    if g.stack().nunique() > g.shape[1]:
                        conflicts.append({"var": var, "sel": sid, "time": str(t)})
                s = s[~s.index.duplicated(keep="first")]
            out[var][sid] = s
    if conflicts:
        raise ValueError(f"conflicting cell duplicates: {conflicts[:3]}")
    return out


def merge_cell_series(cellmap, sel_ids):
    s = pd.concat([cellmap[i] for i in sel_ids if i in cellmap]).sort_index()
    dup = s.index.duplicated(keep=False)
    if dup.any():
        bad = [t for t, g in s[dup].groupby(s[dup].index)
               if g.stack().nunique() > g.shape[1]]
        if bad:
            raise ValueError(f"conflicting cell validities: {[str(t) for t in bad[:3]]}")
        s = s[~s.index.duplicated(keep="first")]
    return s


def masked_theta_cells(t_df, sp_df, t2m_df):
    """TRUE per-cell terrain mask: a 500 hPa level is below ground where
    that cell's sp < 50000 Pa. Returns (hourly box-mean deficit series,
    hourly valid-cell fraction series)."""
    common = t_df.index.intersection(sp_df.index).intersection(t2m_df.index)
    t, sp, t2m = t_df.loc[common], sp_df.loc[common], t2m_df.loc[common]
    theta500 = t * (1000 / 500) ** KAPPA
    thetasfc = t2m * (1000 / (sp / 100)) ** KAPPA
    deficit = theta500 - thetasfc
    valid = sp >= 50000
    frac = valid.sum(axis=1) / valid.shape[1]
    box_mean = deficit.where(valid).mean(axis=1)
    return box_mean, frac


def verify_signoff(so: dict, allowed_decision_shas: set,
                   episode_map_sha: str | None = None) -> bool:
    """Production signoff gate — a signoff approves strata only if it
    (a) is APPROVED, (b) binds a decision file whose sha is in the
    allowed set (v0-style approved_artifact.sha256 or v1-style
    target_sha256), (c) has an owner-role approver/signer, and for
    v1-style also (d) binds this episode map and (e) scopes to
    eligibility adjudication."""
    if so.get("status") != "APPROVED":
        return False
    tgt = so.get("target_sha256") or \
        (so.get("approved_artifact") or {}).get("sha256")
    if tgt not in allowed_decision_shas:
        return False
    role = (so.get("approver") or {}).get("role") or so.get("role") or ""
    if role not in {"owner", "owner_approval"}:  # exact — 'not-owner' must fail
        return False
    if so.get("target_sha256"):  # v1-style: stricter
        if episode_map_sha and so.get("episode_map_sha256") != episode_map_sha:
            return False
        sc = str(so.get("scope", "")).upper()
        # must claim eligibility adjudication; negations rejected
        if not ("ELIGIBIL" in sc and "ADJUDIC" in sc and "NOT" not in sc):
            return False
    return True


def approved_record_ids(signoff_paths, decision_path, predecessor_paths,
                        episode_map_sha):
    """Verified, fail-closed approval coverage.

    Trust rules:
    - allowed decision digests = bound decision bytes + EXPLICIT
      predecessor files only (never a sha a signoff merely names)
    - v1-style signoffs approve their `records` keys
    - v0-style signoffs approve ALL ELIGIBLE records of their bound
      decision file
    - predecessor-approved records are revoked if their fields differ
      in the current decision (supersession must not silently alter
      approved records)
    """
    dec_sha = _sha(decision_path)
    dec = json.loads(decision_path.read_text())
    dec_files = {dec_sha: decision_path}
    for p in predecessor_paths:
        dec_files[_sha(p)] = p
    approved_ids = set()
    for p in signoff_paths:
        so = json.loads(Path(p).read_text())
        if not verify_signoff(so, set(dec_files), episode_map_sha):
            continue
        if so.get("records"):
            approved_ids |= set(so["records"].keys())
        else:
            tgt = (so.get("approved_artifact") or {}).get("sha256")
            if tgt in dec_files:
                dd = json.loads(dec_files[tgt].read_text())
                approved_ids |= {e["event_id"] for e in dd["events"]
                                 if e["adjudication"]["disposition"] == "ELIGIBLE"}
    # unchanged-field check across supersession
    cur = {e["event_id"]: (e["adjudication"]["event_time_interval"]["start"],
                           e["local"]["lat"], e["local"]["lon"])
           for e in dec["events"]}
    for tgt, f in dec_files.items():
        if tgt == dec_sha:
            continue
        for e in json.loads(f.read_text())["events"]:
            eid = e["event_id"]
            old = (e["adjudication"]["event_time_interval"]["start"],
                   e["local"]["lat"], e["local"]["lon"])
            if eid in approved_ids and cur.get(eid) != old:
                approved_ids.discard(eid)
    return approved_ids


def detrend_years(days: pd.Series) -> pd.Series:
    """Remove a linear year trend (frozen detrended sensitivity)."""
    d = days.dropna().sort_index()
    yr = np.asarray(d.index.year, dtype=float)
    coef = np.polyfit(yr - yr.mean(), d.values, 1)
    return d - np.polyval(coef, yr - yr.mean())


def ref_distribution(days: pd.Series, month: int, wash_dates, accum: bool,
                     max_year=None):
    """Consecutive rolling 7-day windows ending inside `month`, minus
    windows whose 7-day span overlaps +-7d of any washout date."""
    days = days.dropna().sort_index()
    if max_year is not None:
        days = days[days.index.year <= max_year]
    idx = days.index
    is_daily = idx.to_series().diff().dt.days.fillna(1) == 1
    full = np.concatenate([[False] * 6, is_daily.rolling(6).min().values[6:].astype(bool)])
    roll = days.rolling(7).sum() if accum else days.rolling(7).mean()
    roll = roll[full & (roll.index.month == month)]
    keep = np.ones(len(roll), bool)
    for wd in wash_dates:
        lo = wd - pd.Timedelta(days=7)   # washout band
        hi = wd + pd.Timedelta(days=7)
        wstart = roll.index - pd.Timedelta(days=6)
        keep &= ~((wstart <= hi) & (roll.index >= lo))
    return roll[keep].dropna()


def antecedent(df: pd.DataFrame, var: str, event_date, accum: bool):
    win = pd.date_range(event_date - pd.Timedelta(days=10),
                        event_date - pd.Timedelta(days=4))
    days = df[var].dropna()
    got = win.intersection(days.index)
    if len(got) < 7:
        return np.nan, int(len(got))
    return (days.loc[win].sum() if accum else days.loc[win].mean()), 7


def build_unit_frame(hourly, sid_set, cellmap=None):
    vs = {v: merge_box_series(hourly[v], sid_set) for v in
          ["tp", "tcwv", "cape", "sf"]}
    if cellmap is None:
        raise ValueError("cell-level arrays required for per-cell theta masking")
    td, frac = masked_theta_cells(
        merge_cell_series(cellmap["t"], sid_set),
        merge_cell_series(cellmap["sp"], sid_set),
        merge_cell_series(cellmap["t2m"], sid_set))
    vs["theta_deficit"] = td.resample("D").mean()
    df = {}
    for var, s in vs.items():
        if var in ACCUM:
            df[var] = s.groupby((s.index - pd.Timedelta(hours=1)).normalize()).sum()
        elif var != "theta_deficit":
            df[var] = s.resample("D").mean()
    df["theta_deficit"] = vs["theta_deficit"]
    out = pd.DataFrame(df)
    out.index = out.index.tz_localize(None).normalize()
    if frac is not None:
        vfd = frac.resample("D").mean()
        vfd.index = vfd.index.tz_localize(None).normalize()
        out.attrs["theta_valid_fraction_daily"] = vfd
    return out


def bh_fdr(pvals):
    p = np.asarray(pvals); n = len(p)
    order = np.argsort(p); ranked = p[order]
    q = ranked * n / (np.arange(n) + 1)
    q = np.minimum.accumulate(q[::-1])[::-1]
    out = np.empty(n); out[order] = np.minimum(q, 1.0)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane-root", action="append", required=True)
    ap.add_argument("--inventory", action="append", required=True)
    ap.add_argument("--episode-map", required=True)
    ap.add_argument("--decision", required=True)
    ap.add_argument("--protocol", required=True)
    ap.add_argument("--transform-contract", required=True)
    ap.add_argument("--owner-signoff", action="append", default=[])
    ap.add_argument("--decision-predecessor", action="append", default=[],
                    help="explicit sealed predecessor decision files; "
                         "signoff targets are trusted ONLY against these bytes")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    lanes = [Path(p) for p in a.lane_root]
    invs = [json.loads(Path(p).read_text()) for p in a.inventory]
    epmap = json.loads(Path(a.episode_map).read_text())
    dec = json.loads(Path(a.decision).read_text())
    hourly = {v: {} for v in VARS}
    for lane in lanes:
        for var, d in load_hourly(lane).items():
            hourly[var].update(d)
    cellmap = {v: {} for v in ("t", "sp", "t2m")}
    for lane in lanes:
        for var, d in load_cell_hourly(lane, ("t", "sp", "t2m")).items():
            cellmap[var].update(d)

    clim = {s["selection_id"]: s for i in invs for s in i["climatology_selections"]}
    spills = [s for i in invs for s in i["antecedent_spillover_selections"]]
    events = {e["event_id"].rsplit(":", 1)[1]: e for e in dec["events"]
              if e["adjudication"]["disposition"] == "ELIGIBLE"}

    # member -> its climatology selection (match member_ids AND month)
    mem_sel, mem_box = {}, {}
    for s in clim.values():
        for mid in s["member_ids"]:
            d = pd.Timestamp(events[mid]["adjudication"]["event_time_interval"]["start"][:10])
            if d.month == s["calendar_month"]:
                mem_sel[mid] = s["selection_id"]
                mem_box[mid] = s["event_box"]

    # payload hash binding
    payload_shas = {}
    for lane in lanes:
        for var in VARS:
            for f in sorted((lane / f"payload-v17_{var}/retrieval/payloads").glob("*.nc")):
                payload_shas[f.stem] = _sha(f)

    member_rows, unit_rows = [], []
    missing = []
    for u in epmap["units"]:
        mids = sorted(u["member_ids"], key=lambda m: events[m]["adjudication"]["event_time_interval"]["start"])
        mres = {}
        for mid in mids:
            e = events[mid]
            d = pd.Timestamp(e["adjudication"]["event_time_interval"]["start"][:10])
            sid = mem_sel.get(mid)
            box = mem_box.get(mid)
            sel_ids = {sid} | {s["selection_id"] for s in spills
                               if s["event_box"] == box}
            # washout = ALL eligible member intervals sharing this
            # box-month climatology group (frozen rule), not just the
            # unit's own members — otherwise a sibling recurrence's
            # anomalous window contaminates the reference
            group_mids = clim[sid]["member_ids"]
            wash = [pd.Timestamp(events[x]["adjudication"]["event_time_interval"]["start"][:10])
                    for x in group_mids]
            row = {"unit_id": u["unit_id"], "member_id": mid, "lake": u["lake"],
                   "event_date": str(d.date()), "month": d.month,
                   "gorkha_window": bool(EQ_START <= d <= EQ_END),
                   "selection_id": sid}
            df = build_unit_frame(hourly, sel_ids, cellmap)
            vf = df.attrs.get("theta_valid_fraction_daily")
            if vf is not None:
                vfw = vf.loc[pd.date_range(d - pd.Timedelta(days=10),
                                           d - pd.Timedelta(days=4))]
                row["theta_valid_fraction_window"] = float(vfw.mean()) if len(vfw) else None
            df_det = df.copy()
            for v in df.columns:
                df_det[v] = detrend_years(df[v])
            for name, (v, kind) in EXPOSURES.items():
                x, n_cov = antecedent(df, v, d, kind == "sum")
                ref = ref_distribution(df[v], d.month, wash, kind == "sum")
                ref_dt = ref_distribution(df_det[v], d.month, wash, kind == "sum")
                if np.isnan(x) or len(ref) < 100 or ref.std() == 0:
                    row[name] = np.nan; row[f"{name}_cov"] = n_cov
                    missing.append({"unit": u["unit_id"], "member": mid,
                                    "exposure": name, "coverage": n_cov})
                else:
                    row[name] = float((x - ref.mean()) / ref.std())
                    row[f"{name}_cov"] = n_cov
                    row[f"{name}_nref"] = len(ref)
                    if len(ref_dt) >= 100 and ref_dt.std() > 0:
                        xdt = antecedent(df_det, v, d, kind == "sum")[0]
                        if not np.isnan(xdt):
                            row[f"{name}_detrended"] = float((xdt - ref_dt.mean()) / ref_dt.std())
                    # declared +-3d date-uncertainty sensitivity + post-hoc
                    # +-7d temporal diagnostic (not an uncertainty test)
                    for sh, tag in ((-3, "unc_m3"), (3, "unc_p3"),
                                    (-7, "diag_m7"), (7, "diag_p7")):
                        xs, cs = antecedent(df, v, d + pd.Timedelta(days=sh), kind == "sum")
                        if not np.isnan(xs):
                            row[f"{name}_{tag}"] = float((xs - ref.mean()) / ref.std())
                        else:
                            row[f"{name}_{tag}_miss"] = f"coverage={cs}<7"
                    # true era-matched sensitivity for pre-2001 units:
                    # reference restricted to <=2000
                    if d.year < 2001:
                        ref2 = ref_distribution(df[v], d.month, wash, kind == "sum", max_year=2000)
                        if len(ref2) >= 100 and ref2.std() > 0:
                            row[f"{name}_era_matched"] = float((x - ref2.mean()) / ref2.std())
            mres[mid] = row
            member_rows.append(row)
        # unit-level: earliest member primary, latest sensitivity
        up = {"unit_id": u["unit_id"], "lake": u["lake"], "era": u.get("era", "post2000"),
              "basin": u["basin"], "primary_member": mids[0],
              "sensitivity_member": mids[-1] if len(mids) > 1 else None,
              "gorkha_window": mres[mids[0]]["gorkha_window"]}
        for name in EXPOSURES:
            up[name] = mres[mids[0]][name]
            if len(mids) > 1:
                up[f"{name}_sens_latest"] = mres[mids[-1]][name]
        for name in EXPOSURES:
            for k, v2 in mres[mids[0]].items():
                if k.startswith(f"{name}_"):
                    up[k] = v2
        unit_rows.append(up)

    ut = pd.DataFrame(unit_rows)
    prim = ut[~ut["gorkha_window"]]
    summ = {}
    pvals, pnames = [], []
    for name in EXPOSURES:
        z = pd.to_numeric(prim[name], errors="coerce").dropna()
        n = len(z)
        mean_z = float(z.mean()) if n else None
        sd = float(z.std(ddof=1)) if n > 1 else None
        se_naive = sd / np.sqrt(n) if n else None
        # basin-cluster SE (diagnostic only — few clusters):
        # Var(mean) ~ (1/G) * (1/(G-1)) * sum_g (ybar_g - ybar)^2
        se_cl = None
        if n > 2 and prim["basin"].nunique() >= 2:
            b = prim.dropna(subset=[name])
            G = b["basin"].nunique()
            gm = b.groupby("basin")[name].mean()
            se_cl = float(np.sqrt(((gm - mean_z) ** 2).sum() / (G * (G - 1))))
        pv = float(2 * (1 - __import__("scipy.stats", fromlist=["norm"]).norm.cdf(abs(mean_z / se_naive)))) \
            if se_naive else None
        summ[name] = {"n_units": int(n), "mean_z": round(mean_z, 3) if mean_z is not None else None,
                      "se_naive": round(se_naive, 3) if se_naive else None,
                      "ci_half_width": round(1.96 * se_naive, 3) if se_naive else None,
                      "se_basin_cluster_DIAGNOSTIC": round(se_cl, 3) if se_cl else None,
                      "p_value": pv,
                      "n_anomalous_abs_z_gt1": int((z.abs() > 1).sum()),
                      "n_positive_z_gt1": int((z > 1).sum()),
                      "n_negative_z_lt_neg1": int((z < -1).sum())}
        if pv is not None and name != PRIMARY:  # FDR over secondaries only
            pvals.append(pv); pnames.append(name)
        # unit-level sensitivity aggregates (primary-member rows only)
        for tag, lbl in (("detrended", "detrended"), ("unc_m3", "unc-3d"),
                         ("unc_p3", "unc+3d"), ("diag_m7", "diag-7d"),
                         ("diag_p7", "diag+7d")):
            zs = pd.to_numeric(prim[f"{name}_{tag}"], errors="coerce").dropna() \
                if f"{name}_{tag}" in prim.columns else pd.Series(dtype=float)
            summ[name][f"mean_z_{lbl}"] = round(float(zs.mean()), 3) if len(zs) else None
            summ[name][f"n_{lbl}"] = int(len(zs))
        # paired same-event sensitivity: only units where BOTH shifted
        # windows and the primary are non-NaN — never compare means
        # across different event sets
        paired = {}
        for tag in ("unc_m3", "unc_p3", "diag_m7", "diag_p7"):
            k = f"{name}_{tag}"
            if k in prim.columns:
                sub = prim.dropna(subset=[name, k])
                if len(sub):
                    dd = sub[k] - sub[name]
                    se_d = float(dd.std(ddof=1)) / np.sqrt(len(dd)) if len(dd) > 1 else None
                    paired[tag] = {"n": int(len(sub)),
                                   "primary_mean": round(float(sub[name].mean()), 3),
                                   "shifted_mean": round(float(sub[k].mean()), 3),
                                   "mean_delta": round(float(dd.mean()), 3),
                                   "se_delta": round(se_d, 3) if se_d else None}
        summ[name]["paired_same_event"] = paired
        # era-matched sensitivity (pre-2001 units, reference <=2000)
        if f"{name}_era_matched" in prim.columns:
            zem = pd.to_numeric(prim[f"{name}_era_matched"], errors="coerce").dropna()
            summ[name]["era_matched_n"] = int(len(zem))
            summ[name]["era_matched_mean_z"] = round(float(zem.mean()), 3) if len(zem) else None
        # era-stratified breakdown (frozen v19 requirement)
        for era in ("post2000", "pre2001"):
            ze = pd.to_numeric(prim[prim["era"] == era][name], errors="coerce").dropna()
            summ[name][f"{era}_n"] = int(len(ze))
            summ[name][f"{era}_mean_z"] = round(float(ze.mean()), 3) if len(ze) else None
            summ[name][f"{era}_hw"] = round(1.96 * float(ze.std(ddof=1)) / np.sqrt(len(ze)), 3) if len(ze) > 1 else None
    qs = bh_fdr(np.array(pvals)) if pvals else []
    for n_, q in zip(pnames, qs):
        summ[n_]["q_value_BH"] = float(q)

    hw = summ[PRIMARY]["ci_half_width"]
    verdict = "ESTIMABLE" if hw is not None and hw <= 0.5 else \
              ("DESCRIPTIVE_ONLY" if hw is not None else "NOT_ESTIMABLE")
    # verified signoff: must target THIS decision bytes AND episode map,
    # be APPROVED, carry an owner-role approver, and scope naming
    # eligibility — an unrelated APPROVED file must not satisfy the gate
    dec_sha = _sha(a.decision)
    ep_sha = _sha(a.episode_map)
    # predecessor chain must be the decision's own declared supersession —
    # not just any file passed on the command line
    dec_obj = json.loads(Path(a.decision).read_text())
    sup = str(dec_obj.get("supersedes", ""))
    declared_preds = []
    for p in a.decision_predecessor:
        if Path(p).stem in sup or Path(p).name in sup:
            declared_preds.append(Path(p))
    approved_ids = approved_record_ids(
        [Path(p) for p in a.owner_signoff], Path(a.decision),
        declared_preds, ep_sha)
    strata_pending = []
    for u in epmap["units"]:
        for mid in u["member_ids"]:
            if f"icimod_hmaglofdb_v1_3_0:1.3.0:{mid}" not in approved_ids:
                strata_pending.append(u.get("era", "?"))
                break
    era_pending = bool(strata_pending)
    out = {"schema": "P5_ROUTE_B_RESULT_V2",
           "supersedes": "armc_routeb_result_v0..v14 lineage (v0/v0b/v1 nonconforming; v2-v13 exploratory iterations)",
           "cohort_approval": ("PENDING_STRATA:" + ",".join(sorted(set(strata_pending)))
                               if era_pending else "APPROVED_ALL_STRATA"),
           "owner_signoffs": [_sha(p) for p in a.owner_signoff],
           "approval_note": ("pre-2001 stratum records are owner-approved rule-qualified "
                             "candidates pending explicit decision-v1 signoff; treat those "
                             "12 units as exploratory stratum until then" if era_pending else None),
           "claim_scope": "descriptive_only_no_event_risk_odds",
           "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
           "run_provenance": {"code_file": _sha(__file__),
                      "argv": sys.argv,
                      "python": sys.version.split()[0],
                      "platform": __import__("platform").platform()},
           "inputs": {"inventory": [_sha(p) for p in a.inventory],
                      "episode_map": _sha(a.episode_map),
                      "decision": _sha(a.decision),
                      "decision_predecessors": [_sha(p) for p in a.decision_predecessor],
                      "protocol": _sha(a.protocol),
                      "transform_contract": _sha(a.transform_contract)},
           "payload_sha256": payload_shas,
           "unit_rule": "earliest member primary; latest member sensitivity",
           "primary_units_n": int(len(prim)), "total_units": int(len(ut)),
           "member_rows_n": len(member_rows),
           "missingness": missing,
           "cluster_note": "3 basin clusters — cluster-robust SE is diagnostic only, not inferential",
           "verdict": verdict,
           "summary_by_exposure": summ,
           "unit_rows": unit_rows, "member_rows": member_rows}
    write_once_json(a.out, out, indent=2)
    write_once_sidecar(a.out)
    print(json.dumps({"verdict": verdict, "n_primary_units": len(prim),
                      "tp": summ[PRIMARY], "missing": len(missing)}, indent=2))


if __name__ == "__main__":
    main()
