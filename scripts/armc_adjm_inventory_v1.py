"""Step 1b adjacent-month selector inventory (Nepal diagnostic re-analysis).

For every distinct (event_box, calendar_month m) climatology selection in
the sealed v17/v19 selector inventories, plan the PRECEDING month (m-1)
for the same years, box and grid.  (box, month) pairs that are already
held as climatology selections are skipped.  Output is a selector
inventory in the v17 schema subset consumed by the Earthmover driver.

Plan: p5-nepal-reanalysis-2026-09-26/plan/nepal_diagnostic_reanalysis_plan_v1.json
"""
import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p5_safe_io import write_once_json, write_once_sidecar


def _sha_file(p):
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _box_key(box):
    return json.dumps(box, sort_keys=True, separators=(",", ":"))


def plan_adjacent(inventories):
    clim = [s for inv in inventories for s in inv["climatology_selections"]]
    held = {(_box_key(s["event_box"]), s["calendar_month"]) for s in clim}
    out, seen = [], set()
    for s in clim:
        m = s["calendar_month"]
        if m == 1:
            raise ValueError(f"{s['selection_id']}: January event month has no in-year predecessor")
        key = (_box_key(s["event_box"]), m - 1)
        if key in held or key in seen:
            continue
        seen.add(key)
        sid = "adjm-" + hashlib.sha256(
            f"{key[0]}|{key[1]}".encode()).hexdigest()[:12]
        out.append({"selection_id": sid,
                    "calendar_month": m - 1,
                    "event_box": s["event_box"],
                    "grid_selection": s["grid_selection"],
                    "years": list(s["years"]),
                    "analysis_unit_ids": list(s.get("analysis_unit_ids", [])),
                    "member_ids": list(s["member_ids"]),
                    "source_selection_id": s["selection_id"],
                    "role": "adjacent_preceding_month"})
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--inventory", action="append", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    invs = [json.loads(Path(p).read_text()) for p in a.inventory]
    sels = plan_adjacent(invs)
    doc = {"schema": "P5_ARMC_ADJM_SELECTOR_INVENTORY_V1",
           "plan_sha256": _sha_file(a.plan),
           "source_inventories_sha256": [_sha_file(p) for p in a.inventory],
           "grid_deg": 0.25,
           "raw_variables": ["tp", "tcwv", "cape", "t2m", "sp", "sf", "t"],
           "n_selections": len(sels),
           "n_selection_years": sum(len(s["years"]) for s in sels),
           "climatology_selections": sels,
           "antecedent_spillover_selections": []}
    write_once_json(Path(a.out), doc, indent=2)
    write_once_sidecar(Path(a.out))
    print(json.dumps({"n_selections": len(sels),
                      "n_selection_years": doc["n_selection_years"]}))


if __name__ == "__main__":
    main()
