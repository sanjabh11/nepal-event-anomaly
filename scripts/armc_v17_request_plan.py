"""V17 request-plan generator — exact, executable, no network.

Builds the frozen request inventory for the per-lake lane:
  chunk  = (event_box, raw_variable) covering the event's calendar
           month across all years 2001-2025 (one request per pair)
  boxes  = the 14 analysis units from armc_event_episode_mapping_v0.json
  vars   = 13 raw variables (9 single-level + 4 pressure@500 hPa)
  grid   = ERA5 0.25 deg (Earthmover icechunk); box = +-0.25 deg around
           event coords -> 3x3 cells nominal (documented, not "ERA5-Land")

Exit contract: emits the request manifest + byte estimate; a companion
test suite validates it (counts, months, snapshot, caps) before any
retrieval is authorized.
"""
import argparse, json, sys, hashlib, datetime
from pathlib import Path
sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from p5_safe_io import write_once_json, write_once_sidecar

SINGLE_VARS = ["t2m", "d2m", "tp", "sf", "sd", "u10", "v10", "sp", "cape", "tcwv"]
# rh_daily and pdd_daily are DERIVED (t2m/d2m/sp), not requested raw
PRESSURE_VARS = [{"var": v, "level_hpa": 500} for v in ("z", "t", "q", "w")]
YEARS = list(range(2001, 2026))
SNAPSHOT = "ZFKDHBCTBVHVXM3BQFV0"
GRID_DEG = 0.25
BOX_HALF_DEG = 0.25          # -> 0.5deg x 0.5deg box, ~3x3 cells nominal
HOURS_PER_MONTH = 31 * 24
BYTES_PER_CELL_HOUR = 4
DAYS_IN_MONTH = {4: 30, 5: 31, 6: 30, 7: 31, 8: 31, 9: 30}

def _sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def build_plan(mapping: dict) -> dict:
    reqs = []
    for u in mapping["units"]:
        box = {"lat_min": round(u["lat"] - BOX_HALF_DEG, 4),
               "lat_max": round(u["lat"] + BOX_HALF_DEG, 4),
               "lon_min": round(u["lon"] - BOX_HALF_DEG, 4),
               "lon_max": round(u["lon"] + BOX_HALF_DEG, 4)}
        for v in SINGLE_VARS:
            reqs.append({"unit_id": u["unit_id"], "lake": u["lake"],
                         "group": "single/temporal", "var": v, "level": None,
                         "box": box, "month": u["month"], "years": YEARS})
        for pv in PRESSURE_VARS:
            reqs.append({"unit_id": u["unit_id"], "lake": u["lake"],
                         "group": "pressure/temporal", "var": pv["var"],
                         "level_hpa": pv["level_hpa"],
                         "box": box, "month": u["month"], "years": YEARS})
    n_req = len(reqs)
    cells = 9  # 3x3 nominal at 0.25deg
    hrs = sum(DAYS_IN_MONTH[u["month"]] * 24 * len(YEARS) for u in mapping["units"])
    est_bytes = n_req and int(sum(
        DAYS_IN_MONTH[u["month"]] * 24 * len(YEARS) * cells * BYTES_PER_CELL_HOUR
        for u in mapping["units"]) * len(SINGLE_VARS + PRESSURE_VARS) * 1.15)  # +15% meta
    return {"schema": "P5_V17_REQUEST_PLAN_V0",
            "claim_scope": "research_only_no_operational_authorization",
            "generated_utc": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            "episode_mapping_sha256": _sha(_MAPPING_PATH) if _MAPPING_PATH else None,
            "chunk_semantics": "one request per (unit_box, variable); covers event month x 25 years",
            "snapshot": SNAPSHOT, "grid_deg": GRID_DEG,
            "box_rule": "+-0.25deg around event coords = 0.5deg box, ~3x3 ERA5 cells",
            "raw_variable_count": len(SINGLE_VARS) + len(PRESSURE_VARS),
            "derived_feature_note": "rh_daily/pdd_daily/theta derived at frame build, not requested",
            "n_units": len(mapping["units"]), "n_requests": n_req,
            "est_bytes": est_bytes, "est_mb": round(est_bytes / 1e6, 1),
            "requests": reqs}

_MAPPING_PATH = None

def main():
    global _MAPPING_PATH
    ap = argparse.ArgumentParser()
    ap.add_argument("--episode-map", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    _MAPPING_PATH = a.episode_map
    mapping = json.loads(Path(a.episode_map).read_text())
    plan = build_plan(mapping)
    write_once_json(a.out, plan, indent=2)
    write_once_sidecar(a.out)
    print(json.dumps({"units": plan["n_units"], "requests": plan["n_requests"],
                      "est_mb": plan["est_mb"]}, indent=2))

if __name__ == "__main__":
    main()
