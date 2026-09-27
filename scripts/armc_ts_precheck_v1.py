"""Own-code completeness gate for the Tien Shan thermal lane (pre-inference).

Recomputes from the NetCDF payloads (no analyzer imports):
  * days -7..0 of every unit's analyzed event must be complete 24h days for t2m
  * same-month L=7 reference pool >= 100 after +-7d washout
  * +/-15 day-of-year L=7 pool >= 100 after washout (the amendment's second null)
  * payload/request sidecars verify; status labels correct; no tmp; cap <=100 MB
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import xarray as xr

CAP = 100_000_000
MIN_REF = 100
WASH = 7
HALF = 15


def sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def doy_dist(a, b):
    d = np.abs(a - b)
    return np.minimum(d, 366 - d)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--lane", required=True)
    ap.add_argument("--inventory", required=True)
    ap.add_argument("--episode-map", required=True)
    ap.add_argument("--decision", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    lane, out = Path(a.lane), Path(a.out)
    inv = json.loads(Path(a.inventory).read_text())
    ep = json.loads(Path(a.episode_map).read_text())
    dec = json.loads(Path(a.decision).read_text())
    sels = inv["climatology_selections"] + inv["antecedent_spillover_selections"]
    events = {e["event_id"].rsplit(":", 1)[1]: e for e in dec["events"]
              if e["adjudication"]["disposition"] == "ELIGIBLE"}
    failures, checks = [], {}

    n_pay = n_req = 0
    total_bytes = 0
    if list(lane.rglob("*.tmp.nc")):
        failures.append("tmp files remain")
    pd_dir = lane / "payload-v17_t2m/retrieval/payloads"
    rq_dir = lane / "payload-v17_t2m/retrieval/requests"
    for f in sorted(pd_dir.glob("*.nc")):
        n_pay += 1
        total_bytes += f.stat().st_size
        sc = Path(str(f) + ".sha256")
        if not sc.exists() or sc.read_text().split()[0] != sha(f):
            failures.append(f"payload sidecar fail {f.name}")
        rq = rq_dir / (f.stem + ".json")
        rqs = Path(str(rq) + ".sha256")
        if not rq.exists():
            failures.append(f"missing request {rq.name}")
            continue
        n_req += 1
        if not rqs.exists() or rqs.read_text().split()[0] != sha(rq):
            failures.append(f"request sidecar fail {rq.name}")
        if json.loads(rq.read_text()).get("status") != "PAYLOAD_DERIVED_FROM_EARTHMOVER":
            failures.append(f"bad status {rq.name}")
    if total_bytes > CAP:
        failures.append(f"cap exceeded: {total_bytes}")
    expected = inv["n_selections"] * len(inv["raw_variables"])
    checks.update(payloads=n_pay, request_records=n_req, payload_bytes=total_bytes,
                  expected_payloads=expected)
    if n_pay != expected:
        failures.append(f"payload count {n_pay} != expected {expected}")

    tp = {}
    for f in sorted(pd_dir.glob("*.nc")):
        sid = f.stem.replace("v17-", "").replace("-t2m", "")
        ds = xr.open_dataset(f)
        vt = pd.DatetimeIndex(ds["valid_time"].values, tz="UTC")
        tp[sid] = pd.Series(np.asarray(ds["t2m"].values).reshape(len(vt), -1).mean(axis=1), index=vt)

    per_unit = {}
    for u in ep["units"]:
        uid = u["unit_id"]
        pm = u["member_ids"][0] if len(u["member_ids"]) == 1 else sorted(
            u["member_ids"],
            key=lambda m: events[m]["adjudication"]["event_time_interval"]["start"])[0]
        d = pd.Timestamp(events[pm]["adjudication"]["event_time_interval"]["start"][:10])
        box = json.dumps(
            next(s["event_box"] for s in inv["climatology_selections"]
                 if pm in s["member_ids"] and s["calendar_month"] == d.month),
            sort_keys=True)
        sel_ids = [s["selection_id"] for s in sels
                   if json.dumps(s["event_box"], sort_keys=True) == box]
        merged = pd.concat([tp[i] for i in sel_ids if i in tp]).sort_index()
        merged = merged[~merged.index.duplicated(keep="first")]
        g = merged.dropna().groupby(merged.dropna().index.normalize())
        dly = g.mean()[g.count() == 24]
        dly.index = dly.index.tz_localize(None)
        dly = dly.sort_index().reindex(pd.date_range(dly.index.min(), dly.index.max(), freq="D"))
        wash = sorted({pd.Timestamp(events[m]["adjudication"]["event_time_interval"]["start"][:10])
                       for s in inv["climatology_selections"]
                       if json.dumps(s["event_box"], sort_keys=True) == box
                       for m in s["member_ids"] if m in events
                       and pd.Timestamp(events[m]["adjudication"]["event_time_interval"]["start"][:10]).month
                       == s["calendar_month"]})
        unit_fail = []
        for lag in range(-7, 1):
            if (d + pd.Timedelta(days=lag)) not in dly.index or pd.isna(dly.get(d + pd.Timedelta(days=lag))):
                unit_fail.append(f"day {lag:+d} incomplete")
        L = 7
        roll = dly.rolling(L, min_periods=L).mean()
        e_end = d - pd.Timedelta(days=1)          # E6 window -7..-1 ends d-1
        def n_ok(idxs):
            n = 0
            for t in idxs:
                if pd.isna(roll.loc[t]):
                    continue
                lo = t - pd.Timedelta(days=L - 1)
                if not any(wd - pd.Timedelta(days=WASH) <= t and lo <= wd + pd.Timedelta(days=WASH)
                           for wd in wash):
                    n += 1
            return n
        n_month = n_ok(roll.index[roll.index.month == e_end.month])
        n_doy = n_ok(roll.index[doy_dist(roll.index.dayofyear.to_numpy(), e_end.dayofyear) <= HALF])
        if n_month < MIN_REF:
            unit_fail.append(f"same-month n_ref={n_month} < {MIN_REF}")
        if n_doy < MIN_REF:
            unit_fail.append(f"doy n_ref={n_doy} < {MIN_REF}")
        per_unit[uid] = {"primary_member": pm, "event_date": str(d.date()),
                         "n_month_ref": n_month, "n_doy_ref": n_doy,
                         "status": "READY" if not unit_fail else "NOT_READY",
                         "failures": unit_fail}
        if unit_fail:
            failures.append(f"{uid}: {unit_fail}")
    checks["units"] = per_unit
    checks["n_ready"] = sum(1 for v in per_unit.values() if v["status"] == "READY")

    doc = {"schema": "P5_TIENSHAN_COVERAGE_CHECK_V1", "checks": checks,
           "failures": failures, "gate": "PASS" if not failures else "FAIL"}
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        out.write_text(json.dumps(doc, indent=1, sort_keys=True) + "\n")
        Path(str(out) + ".sha256").write_text(f"{sha(out)}  {out.name}\n")
    print(json.dumps({"gate": doc["gate"], "failures": failures[:10],
                      "payloads": n_pay, "bytes": total_bytes,
                      "units_ready": checks["n_ready"]}))
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
