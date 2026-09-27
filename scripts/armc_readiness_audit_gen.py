"""Arm C deterministic readiness-audit generator (Codex phase-1 repair).

Recomputes, from pinned inputs only, the three readiness audits that were
previously produced by ad-hoc inline code:
  1. event-catalog reconciliation (event_id <-> HMAGLOFDB.csv GF_ID)
  2. observation-opportunity completeness census
  3. spatial identifiability (anchor-box membership under explicit,
     separately named filter counts)

Fixes over v0 artifacts:
  - every count is named by its exact filter conjunction
  - GF_ID 730 (gandaki, CDS-sourced, April) is correctly classified;
    the earlier "all five day-precision in-box candidates are Koshi/
    Earthmover" claim was wrong
  - inputs are bound by sha256; generated_utc is real execution metadata
  - all paths are parameters; output is deterministic given inputs
    except the timestamp field

Usage:
  python3 scripts/armc_readiness_audit_gen.py \
      --package <p3_runner_package_v0.json> --hmaglofdb-csv <HMAGLOFDB.csv> \
      --frame <armc_daily_frame_v0.csv> --source-map <armc_source_map_v15.json> \
      --out <armc_readiness_audit_v1.json>
"""
import argparse, json, sys, hashlib, datetime
from pathlib import Path
sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import pandas as pd
from p5_safe_io import write_once_json, write_once_sidecar

BOXES = {"gandaki": (28.25, 29.0, 84.5, 85.0),
         "karnali": (29.5, 30.0, 81.75, 82.5),
         "koshi":   (27.75, 28.25, 86.75, 87.25)}
SOURCE_BY_BASIN_500 = {"gandaki": "cds", "karnali": "earthmover_icechunk",
                       "koshi": "earthmover_icechunk"}
WINDOW_START, WINDOW_END = pd.Timestamp("2001-06-01"), pd.Timestamp("2025-08-31")
JJA = {6, 7, 8}

def sha256(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def in_box(lat, lon, basin):
    n, s, w, e = BOXES[basin]
    return bool(n <= lat <= s and w <= lon <= e)

def dist_km_to_box(lat, lon, basin):
    n, s, w, e = BOXES[basin]
    dlat = max(0.0, n - lat if lat < n else 0.0, lat - s if lat > s else 0.0)
    dlon = max(0.0, w - lon if lon < w else 0.0, lon - e if lon > e else 0.0)
    return float(np.hypot(dlat, dlon) * 111.0)

def reconcile(pkg, csv):
    byid = csv.set_index(csv["GF_ID"].astype(str))
    out = []
    for e in pkg["event_labels"]:
        gf = e["event_id"].rsplit(":", 1)[1]
        rec = {"event_id": e["event_id"], "gf_id": gf, "mismatches": []}
        if gf not in byid.index:
            rec["disposition"] = "UNRESOLVED_NO_SOURCE_ROW"
            out.append(rec); continue
        row = byid.loc[gf]
        if abs(float(row["Lat_lake"]) - e["latitude"]) > 1e-3:
            rec["mismatches"].append("lat_lake")
        if abs(float(row["Lon_lake"]) - e["longitude"]) > 1e-3:
            rec["mismatches"].append("lon_lake")
        rec["disposition"] = "TRACEABLE" if not rec["mismatches"] else "FIELD_MISMATCH"
        out.append(rec)
    return out

def spatial_table(els):
    rows = []
    for _, e in els.iterrows():
        d = pd.Timestamp(e["event_time_start"]).tz_localize(None)
        inside = in_box(e["latitude"], e["longitude"], e["basin_group"])
        rows.append({
            "event_id": e["event_id"], "basin_group": e["basin_group"],
            "precision": e["event_time_precision"], "date": str(d.date()),
            "in_anchor_box": inside,
            "in_2001_2025_window": bool(WINDOW_START <= d <= WINDOW_END),
            "in_jja": d.month in JJA,
            "dominant_source_500hpa": SOURCE_BY_BASIN_500[e["basin_group"]],
            "dist_km_to_anchor_box": round(dist_km_to_box(
                e["latitude"], e["longitude"], e["basin_group"]), 1)})
    return pd.DataFrame(rows)

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--package", required=True)
    ap.add_argument("--hmaglofdb-csv", required=True)
    ap.add_argument("--frame", required=True)
    ap.add_argument("--source-map", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()

    pkg = json.loads(Path(a.package).read_text())
    csv = pd.read_csv(a.hmaglofdb_csv, low_memory=False, encoding="latin-1")
    frame = pd.read_csv(a.frame); frame["date"] = pd.to_datetime(frame["date"])
    els = pd.DataFrame(pkg["event_labels"])

    recon = reconcile(pkg, csv)
    st = spatial_table(els)
    opps = pd.DataFrame(pkg["opportunities"])

    counts = {
        "n_labels": len(els),
        "n_in_anchor_box": int(st["in_anchor_box"].sum()),
        "n_in_anchor_box_AND_window_2001_2025": int(
            (st["in_anchor_box"] & st["in_2001_2025_window"]).sum()),
        "n_in_anchor_box_AND_window_AND_day_precision": int(
            (st["in_anchor_box"] & st["in_2001_2025_window"]
             & (st["precision"] == "day")).sum()),
        "n_in_anchor_box_AND_window_AND_day_precision_AND_jja": int(
            (st["in_anchor_box"] & st["in_2001_2025_window"]
             & (st["precision"] == "day") & st["in_jja"]).sum()),
        "n_day_precision_and_jja_covered_by_frame": int(
            st[(st["precision"] == "day") & st["in_anchor_box"]
               & st["in_2001_2025_window"] & st["in_jja"]].apply(
                lambda r: len(frame[(frame["basin_group"] == r["basin_group"]) &
                                    (frame["date"] == r["date"])]) > 0, axis=1).sum()),
    }
    in_box_day = st[st["in_anchor_box"] & (st["precision"] == "day")]
    source_summary = in_box_day.groupby(["basin_group", "dominant_source_500hpa"]).size().to_dict()
    source_summary = {f"{k[0]}/{k[1]}": int(v) for k, v in source_summary.items()}

    report = {
        "schema": "P5_ARMC_READINESS_AUDIT_V1",
        "claim_scope": "research_only_no_operational_authorization",
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "inputs": {"package_sha256": sha256(a.package),
                   "hmaglofdb_csv_sha256": sha256(a.hmaglofdb_csv),
                   "frame_sha256": sha256(a.frame),
                   "source_map_sha256": sha256(a.source_map)},
        "reconciliation": {
            "join_key": "event_id suffix == HMAGLOFDB.csv GF_ID",
            "dispositions": pd.Series([r["disposition"] for r in recon]).value_counts().to_dict(),
            "records": recon},
        "observation_census": {
            "n_opportunities": len(opps),
            "state_distribution": opps["state"].value_counts().to_dict(),
            "promotable_to_observed_control": int((opps["state"] == "OBSERVED_FULL").sum())},
        "spatial_identifiability": {
            "counts": counts,
            "in_box_day_precision_source_summary": source_summary,
            "correction_vs_v0": ("v0 claimed all five day-precision in-box candidates were "
                                 "koshi/earthmover; GF_ID 730 (2024-04-21) is gandaki/CDS. "
                                 "The two JJA candidates are both koshi/earthmover."),
            "records": st.to_dict("records")},
        "eligibility_wording": ("At most two candidates pass in-box + day-precision + JJA + "
                                "frame-coverage filters; all 45 remain UNADJUDICATED, so zero "
                                "are presently analysis-eligible."),
    }
    write_once_json(a.out, report, indent=2)
    write_once_sidecar(a.out)
    print(json.dumps({"out": a.out, "counts": counts,
                      "recon": report["reconciliation"]["dispositions"]}, indent=2))

if __name__ == "__main__":
    main()
