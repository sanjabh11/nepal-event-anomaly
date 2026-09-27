"""HMA held-out selector inventory builder (Option-A pooled locking test).

Implements the frozen protocol p5-hma-heldout-protocol-v1.json:

  * one 2x2-cell 0.25-degree box per lake cluster: the two ERA5 centers
    bracketing the lake coordinate on each axis (on-center tie -> {c, c+0.25});
    every member of a cluster must resolve to the SAME box, else fail closed
  * per member event: calendar month of the event + the preceding month,
    fetched as full months over all lane years
  * years 2001-2025, extended to 1940-2025 for units containing a pre-2001
    member (era-matched reference)
  * climatology-style selections only (months already cover every window;
    no spillover groups are needed)

Schema matches the adjm/v17 inventory shape so the Earthmover worker family
reads it unchanged.
"""
from __future__ import annotations

import argparse
import calendar
import hashlib
import json
import math
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402

GRID_DEG = 0.25
YEARS_POST2000 = list(range(2001, 2026))
YEARS_FULL = list(range(1940, 2026))
SNAPSHOT = "ZFKDHBCTBVHVXM3BQFV0"
SINGLE_VARS = ("tp", "tcwv", "cape", "t2m", "sp", "sf")
PRESSURE_VARS = ("t",)
BYTE_CAP = 400_000_000
BYTES_PER_VALUE = 4          # float32 payloads
PLANNING_OVERHEAD_FACTOR = 1.4
SHA256_RE_LEN = 64


def sha256_file(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def _stable_id(prefix: str, value: object) -> str:
    enc = json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    return f"{prefix}-{hashlib.sha256(enc).hexdigest()[:12]}"


def bracketing_centers(x: float) -> list[float]:
    """The two 0.25-degree centers bracketing x; exact-center -> {c, c+0.25}."""
    i = math.floor(x / GRID_DEG)
    lo, hi = round(i * GRID_DEG, 8), round((i + 1) * GRID_DEG, 8)
    if abs(x - lo) < 1e-9:            # x sits on `lo`
        return [lo, hi]
    if abs(x - hi) < 1e-9:            # x sits on `hi`
        return [hi, round(hi + GRID_DEG, 8)]
    return [lo, hi]


def unit_box(lat: float, lon: float) -> dict:
    lats = bracketing_centers(lat)
    lons = bracketing_centers(lon)
    return {
        "event_box": {"lat_min": lats[0], "lat_max": lats[1],
                      "lon_min": lons[0], "lon_max": lons[1]},
        "grid_selection": {
            "rule": "two 0.25-degree ERA5 centers bracketing the lake coordinate on each axis",
            "latitudes_descending": sorted(lats, reverse=True),
            "longitudes_ascending": sorted(lons),
            "cell_count": 4,
            "source_coordinate_verification": "REQUIRED_BEFORE_EXTRACTION_NOT_YET_VERIFIED",
        },
    }


def build_inventory(episode_map: dict, manifest: dict, *, protocol_sha256: str,
                    episode_map_sha256: str, decision_sha256: str,
                    manifest_sha256: str) -> dict:
    if len(protocol_sha256) != SHA256_RE_LEN:
        raise ValueError("protocol sha256 required")
    if episode_map.get("schema") != "P5_EVENT_EPISODE_MAPPING_V0" or not episode_map.get("heldout"):
        raise ValueError("unexpected episode map")
    events_by_gf = {e["gf_id"]: e for e in manifest["events"]}

    groups = {}   # (box_key, month, years_key) -> selection row
    for unit in episode_map["units"]:
        mids = unit["member_ids"]
        # fail closed if members resolve to different boxes
        boxes = {json.dumps(unit_box(events_by_gf[m]["lat"], events_by_gf[m]["lon"])["event_box"],
                            sort_keys=True) for m in mids}
        if len(boxes) != 1:
            raise ValueError(f"{unit['unit_id']}: members do not resolve to one 2x2 box: {boxes}")
        geo = unit_box(float(unit["lat"]), float(unit["lon"]))
        years = YEARS_FULL if any(events_by_gf[m]["era"] == "pre2001" for m in mids) else YEARS_POST2000
        months = sorted({mm for m in mids
                         for mm in (int(events_by_gf[m]["date"][5:7]),
                                    (int(events_by_gf[m]["date"][5:7]) - 1) or 12)})
        for month in months:
            event_members = [m for m in mids if int(events_by_gf[m]["date"][5:7]) == month]
            preceding_members = [m for m in mids
                                 if ((int(events_by_gf[m]["date"][5:7]) - 1) or 12) == month]
            key = (json.dumps(geo["event_box"], sort_keys=True), month, len(years))
            g = groups.setdefault(key, {
                "selection_id": _stable_id("hma", {"box": geo["event_box"], "month": month,
                                                  "n_years": len(years)}),
                "event_box": geo["event_box"],
                "grid_selection": geo["grid_selection"],
                "calendar_month": month,
                "years": years,
                "analysis_unit_ids": set(),
                "member_ids": set(),
                "role_detail": {"event_month_members": set(), "preceding_month_members": set()},
            })
            g["analysis_unit_ids"].add(unit["unit_id"])
            g["member_ids"].update(event_members)
            g["member_ids"].update(preceding_members)
            g["role_detail"]["event_month_members"].update(event_members)
            g["role_detail"]["preceding_month_members"].update(preceding_members)

    sels = []
    for g in sorted(groups.values(), key=lambda g: g["selection_id"]):
        sels.append({
            "selection_id": g["selection_id"],
            "event_box": g["event_box"],
            "grid_selection": g["grid_selection"],
            "calendar_month": g["calendar_month"],
            "years": g["years"],
            "analysis_unit_ids": sorted(g["analysis_unit_ids"]),
            "member_ids": sorted(g["member_ids"], key=int),
            "role": "event_month_and_preceding_month",
            "role_detail": {k: sorted(v, key=int) for k, v in g["role_detail"].items()},
        })

    hours = 0
    for s in sels:
        month = s["calendar_month"]
        hours += sum(calendar.monthrange(y, month)[1] for y in s["years"]) * 24 + len(s["years"])
    n_vars = len(SINGLE_VARS) + len(PRESSURE_VARS)
    raw_bytes = hours * 4 * n_vars * BYTES_PER_VALUE
    estimate = math.ceil(raw_bytes * PLANNING_OVERHEAD_FACTOR)
    if estimate >= BYTE_CAP:
        raise ValueError(f"planning estimate {estimate} exceeds cap {BYTE_CAP}; refusing to emit inventory")

    return {
        "schema": "P5_ARMC_HMA_SELECTOR_INVENTORY_V1",
        "status": "FROZEN_PROTOCOL_BOUND",
        "claim_scope": "research_only_no_operational_authorization",
        "protocol_sha256": protocol_sha256,
        "episode_map_sha256": episode_map_sha256,
        "decision_sha256": decision_sha256,
        "cohort_manifest_sha256": manifest_sha256,
        "snapshot": SNAPSHOT,
        "source": "earthmover_icechunk",
        "grid_deg": GRID_DEG,
        "box_rule": "2x2 cells per lake cluster; two centers bracketing the coordinate per axis",
        "months_rule": "event calendar month + preceding month",
        "years_rule": "2001-2025; 1940-2025 for units with a pre-2001 member",
        "raw_variables": list(SINGLE_VARS) + [f"{v}@500hPa" for v in PRESSURE_VARS],
        "n_selections": len(sels),
        "n_selection_years": sum(len(s["years"]) for s in sels),
        "estimate": {
            "hours": hours, "cells_per_selection": 4, "n_variables": n_vars,
            "bytes_per_value": BYTES_PER_VALUE, "overhead_factor": PLANNING_OVERHEAD_FACTOR,
            "estimated_bytes": estimate,
            "estimated_mb_decimal": round(estimate / 1_000_000, 2),
            "cap_bytes": BYTE_CAP, "below_cap_on_model_estimate": estimate < BYTE_CAP,
        },
        "climatology_selections": sels,
        "antecedent_spillover_selections": [],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--episode-map", required=True)
    ap.add_argument("--decision", required=True)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--protocol", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    episode_map = json.loads(Path(a.episode_map).read_text())
    manifest = json.loads(Path(a.manifest).read_text())
    inv = build_inventory(
        episode_map, manifest,
        protocol_sha256=sha256_file(a.protocol),
        episode_map_sha256=sha256_file(a.episode_map),
        decision_sha256=sha256_file(a.decision),
        manifest_sha256=sha256_file(a.manifest),
    )
    out = Path(a.out)
    write_once_json(out, inv, indent=2)
    write_once_sidecar(out)
    print(json.dumps({"out": str(out), "n_selections": inv["n_selections"],
                      "estimated_mb": inv["estimate"]["estimated_mb_decimal"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
