"""Tien Shan thermal selector inventory builder (frozen stub + amendment v1).

Same box/months/years rules as the HMA held-out plan (copied from the stub),
with two cohort-specific differences:

  * only variable: t2m (declared E6 estimand; no other variable is fetched)
  * months are the union over ALL member rows (each member event's calendar
    month + preceding month), read from the derived decision doc, so every
    member date contributes to the analyzer's box washout
  * years 2001-2025; 1940-2025 when ANY member event is pre-2001
  * aggregate cap 100 MB (amendment)

Schema matches the adjm/hma inventory shape so the Earthmover worker family
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
import armc_request_plan_hma_v1 as hma  # noqa: E402  # shared box/id helpers

SNAPSHOT = "ZFKDHBCTBVHVXM3BQFV0"
VARIABLES = ("t2m",)
BYTE_CAP = 100_000_000
PLANNING_OVERHEAD_FACTOR = 1.4


def sha256_file(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def build(episode_map: dict, decision: dict, *, amendment_sha256: str,
          episode_map_sha256: str, decision_sha256: str, stub_sha256: str) -> dict:
    if episode_map.get("schema") != "P5_EVENT_EPISODE_MAPPING_V0" or not episode_map.get("heldout"):
        raise ValueError("unexpected episode map")
    ev = {e["event_id"].rsplit(":", 1)[1]: e for e in decision["events"]}
    # member dates per cluster from the decision doc
    members_by_cluster = {}
    for e in decision["events"]:
        c = e["provenance"]["lake_cluster"]
        d = e["adjudication"]["event_time_interval"]["start"][:10]
        g = e["event_id"].rsplit(":", 1)[1]
        members_by_cluster.setdefault(c, {})[g] = d

    groups = {}
    for unit in episode_map["units"]:
        cluster = unit["lake"]
        members = members_by_cluster[cluster]
        # every member must resolve to one identical box
        boxes = {json.dumps(hma.unit_box(unit["member_coords"][g][0], unit["member_coords"][g][1])["event_box"],
                            sort_keys=True) for g in members}
        if len(boxes) != 1:
            raise ValueError(f"{unit['unit_id']}: members do not resolve to one 2x2 box: {boxes}")
        geo = hma.unit_box(float(unit["lat"]), float(unit["lon"]))
        years = hma.YEARS_FULL if any(int(d[:4]) < 2001 for d in members.values()) else hma.YEARS_POST2000
        months = sorted({mm for d in members.values()
                         for mm in (int(d[5:7]), (int(d[5:7]) - 1) or 12)})
        for month in months:
            event_members = [g for g, d in members.items() if int(d[5:7]) == month]
            preceding_members = [g for g, d in members.items() if ((int(d[5:7]) - 1) or 12) == month]
            key = (json.dumps(geo["event_box"], sort_keys=True), month, len(years))
            g = groups.setdefault(key, {
                "selection_id": hma._stable_id("ts", {"box": geo["event_box"], "month": month,
                                                      "n_years": len(years)}),
                "event_box": geo["event_box"], "grid_selection": geo["grid_selection"],
                "calendar_month": month, "years": years,
                "analysis_unit_ids": set(), "member_ids": set(),
                "role_detail": {"event_month_members": set(), "preceding_month_members": set()},
            })
            g["analysis_unit_ids"].add(unit["unit_id"])
            g["member_ids"].update(event_members)
            g["member_ids"].update(preceding_members)
            g["role_detail"]["event_month_members"].update(event_members)
            g["role_detail"]["preceding_month_members"].update(preceding_members)

    sels = [{
        "selection_id": g["selection_id"], "event_box": g["event_box"],
        "grid_selection": g["grid_selection"], "calendar_month": g["calendar_month"],
        "years": g["years"], "analysis_unit_ids": sorted(g["analysis_unit_ids"]),
        "member_ids": sorted(g["member_ids"], key=int),
        "role": "event_month_and_preceding_month",
        "role_detail": {k: sorted(v, key=int) for k, v in g["role_detail"].items()},
    } for g in sorted(groups.values(), key=lambda g: g["selection_id"])]

    hours = sum(sum(calendar.monthrange(y, s["calendar_month"])[1] for y in s["years"]) * 24
                + len(s["years"]) for s in sels)
    raw_bytes = hours * 4 * len(VARIABLES) * hma.BYTES_PER_VALUE
    estimate = math.ceil(raw_bytes * PLANNING_OVERHEAD_FACTOR)
    if estimate >= BYTE_CAP:
        raise ValueError(f"planning estimate {estimate} exceeds cap {BYTE_CAP}; refusing to emit inventory")

    return {
        "schema": "P5_ARMC_TIENSHAN_SELECTOR_INVENTORY_V1",
        "status": "FROZEN_AMENDMENT_BOUND",
        "claim_scope": "research_only_no_operational_authorization",
        "amendment_sha256": amendment_sha256,
        "stub_sha256": stub_sha256,
        "episode_map_sha256": episode_map_sha256,
        "decision_sha256": decision_sha256,
        "snapshot": SNAPSHOT,
        "source": "earthmover_icechunk",
        "grid_deg": 0.25,
        "box_rule": "2x2 cells per lake cluster; two centers bracketing the coordinate per axis",
        "months_rule": "union over member events: event calendar month + preceding month",
        "years_rule": "2001-2025; 1940-2025 for units with any pre-2001 member",
        "raw_variables": list(VARIABLES),
        "n_selections": len(sels),
        "n_selection_years": sum(len(s["years"]) for s in sels),
        "estimate": {"hours": hours, "cells_per_selection": 4, "n_variables": len(VARIABLES),
                     "bytes_per_value": hma.BYTES_PER_VALUE,
                     "overhead_factor": PLANNING_OVERHEAD_FACTOR,
                     "estimated_bytes": estimate,
                     "estimated_mb_decimal": round(estimate / 1_000_000, 2),
                     "cap_bytes": BYTE_CAP, "below_cap_on_model_estimate": estimate < BYTE_CAP},
        "climatology_selections": sels,
        "antecedent_spillover_selections": [],
    }


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--episode-map", required=True)
    ap.add_argument("--decision", required=True)
    ap.add_argument("--stub", required=True)
    ap.add_argument("--amendment", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    inv = build(json.loads(Path(a.episode_map).read_text()),
                json.loads(Path(a.decision).read_text()),
                amendment_sha256=sha256_file(a.amendment),
                episode_map_sha256=sha256_file(a.episode_map),
                decision_sha256=sha256_file(a.decision),
                stub_sha256=sha256_file(a.stub))
    out = Path(a.out)
    write_once_json(out, inv, indent=2)
    write_once_sidecar(out)
    print(json.dumps({"out": str(out), "n_selections": inv["n_selections"],
                      "estimated_mb": inv["estimate"]["estimated_mb_decimal"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
