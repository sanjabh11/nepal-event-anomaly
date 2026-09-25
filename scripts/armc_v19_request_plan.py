"""V19 era-extension selector inventory — pre-2001 day-precision units.

Same schema/rules as the v1 planner, with one difference: climatology
years span the full ERA5 period 1940-2025 (era-matched reference for
pre-2001 events); era-stratified sensitivity is handled downstream by
the analyzer (pre-1979 pre-satellite flag is carried through).
"""
import argparse, json, sys, hashlib, datetime
from pathlib import Path
import pandas as pd
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import armc_v17_request_plan_v1 as v1
from p5_safe_io import write_once_json, write_once_sidecar

YEARS_EXT = list(range(1940, 2026))
v1.YEARS = tuple(YEARS_EXT)  # era extension: allow pre-2001 event/window years


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode-map", required=True)
    ap.add_argument("--decision", required=True)
    ap.add_argument("--protocol", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    mapping = json.loads(Path(a.episode_map).read_text())
    dec = json.loads(Path(a.decision).read_text())
    dec_v1 = dict(dec)
    if dec_v1.get("schema") == "P5_EVENT_ADJUDICATION_V2":
        dec_v1["schema"] = "P5_EVENT_ADJUDICATION_V1"  # structural superset
    eligible = v1._eligible_event_index(dec_v1)
    era_units = [u for u in mapping["units"] if u.get("era") == "pre2001"]
    clim_sels, spill_sels, events = [], [], []
    for u in era_units:
        geo = v1._unit_geometry(u)
        for mid in geo["member_ids"]:
            ev = v1._event_plan(u, geo, eligible[mid])
            events.append(ev)
    clim, spill = v1._build_selection_groups(events)
    for g in clim:
        g["years"] = YEARS_EXT          # era-matched climatology
        g["era_note"] = "reference spans 1940-2025; pre-1979 = pre-satellite ERA5"
    inv = {
        "schema": "P5_V19_SELECTOR_INVENTORY_V0",
        "claim_scope": "research_only_no_operational_authorization",
        "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "inputs": {"episode_map": hashlib.sha256(Path(a.episode_map).read_bytes()).hexdigest(),
                   "decision": hashlib.sha256(Path(a.decision).read_bytes()).hexdigest(),
                   "protocol": hashlib.sha256(Path(a.protocol).read_bytes()).hexdigest()},
        "snapshot": v1.SNAPSHOT, "source": "earthmover_icechunk",
        "grid_deg": v1.GRID_DEG,
        "raw_variables": {"single_level": list(v1.SINGLE_VARS),
                          "pressure_level_hpa_500": list(v1.PRESSURE_VARS)},
        "n_analysis_units": len(era_units),
        "n_event_members": len(events),
        "climatology_selections": clim,
        "antecedent_spillover_selections": spill,
        "event_windows": events,
    }
    write_once_json(a.out, inv, indent=2)
    write_once_sidecar(a.out)
    print(json.dumps({"units": len(era_units), "clim_groups": len(clim),
                      "spillovers": len(spill)}, indent=2))


if __name__ == "__main__":
    main()
