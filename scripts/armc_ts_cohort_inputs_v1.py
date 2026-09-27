"""Derive analyzer-contract inputs for the Tien Shan thermal cohort.

Reads the FROZEN stub (p5-writeup-2026-09-26/tienshan_thermal_prereg_stub_v1.json)
and HMAGLOFDB.csv, and emits:

  * tienshan_episode_map_v1.json  - one unit per stub lake_cluster;
      unit.member_ids = [primary_gf_id] ONLY, because the sealed analyzer takes
      the earliest-listed member as the analyzed event and the stub's repeat
      model declares the MOST RECENT day-dated event as primary. All member
      rows still enter the decision doc and the inventory's washout selections.
  * tienshan_adjudication_decision_v1.json - P5_EVENT_ADJUDICATION_V1 schema;
      every member row ELIGIBLE by cohort declaration.

HMAGLOFDB GF_IDs are not unique across countries; member rows are resolved by
(GF_ID, unit country) and must be unique and day-dated, else fail closed.
"""
from __future__ import annotations

import argparse
import csv
import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402


def sha256_file(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def build(stub: dict, db_rows: dict):
    if stub.get("schema") != "P5_TIENSHAN_THERMAL_PREREG_STUB_V1":
        raise ValueError("unexpected stub schema")
    units, decisions = [], []
    ordered = sorted(stub["units"], key=lambda u: u["primary_date"])
    for i, su in enumerate(ordered, 1):
        members = []
        for g in su["member_gf_ids"]:
            r = db_rows[(g, su["country"])]
            if r["Month"] in ("NA", "") or r["Day"] in ("NA", ""):
                raise ValueError(f"{su['lake_cluster']} member {g} not day-dated")
            members.append(r)
        primary_row = db_rows[(su["primary_gf_id"], su["country"])]
        pdate = f"{primary_row['Year_exact']}-{int(primary_row['Month']):02d}-{int(primary_row['Day']):02d}"
        if pdate != su["primary_date"]:
            raise ValueError(f"{su['lake_cluster']}: stub primary_date {su['primary_date']} != HMAGLOFDB {pdate}")
        boxes = {(float(r["Lat_lake"]), float(r["Lon_lake"])) for r in members}
        units.append({
            "unit_id": f"ts_{i:02d}",
            "member_ids": [su["primary_gf_id"]],
            "gf_ids": sorted(su["member_gf_ids"], key=int),
            "lake": su["lake_cluster"],
            "lake_names": [su["lake_name"]],
            "basin": su["country"],
            "countries": [su["country"]],
            "era": "pre2001" if int(su["primary_date"][:4]) < 2001 else "satellite",
            "gorkha_window": False,
            "driver": primary_row["Driver_GLOF"],
            "lat": float(primary_row["Lat_lake"]),
            "lon": float(primary_row["Lon_lake"]),
            "member_coords": {g: [float(r["Lat_lake"]), float(r["Lon_lake"])]
                              for g, r in zip(su["member_gf_ids"], members)},
            "n_members": su["n_member_rows"],
            "repeat_model": "most_recent_day_dated (stub cohort_rules)",
            "all_member_gf_ids": su["member_gf_ids"],
            "lake_type": su["lake_type"],
            "_member_coord_spread": len(boxes),
        })
        for g, r in zip(su["member_gf_ids"], members):
            y, mo, d = int(r["Year_exact"]), int(r["Month"]), int(r["Day"])
            end = (_dt.date(y, mo, d) + _dt.timedelta(days=1)).isoformat()
            decisions.append({
                "event_id": f"HMAGLOFDB:{g}",
                "adjudication": {
                    "disposition": "ELIGIBLE",
                    "event_time_interval": {
                        "start": f"{y}-{mo:02d}-{d:02d}T00:00:00Z",
                        "end": f"{end}T00:00:00Z",
                        "precision": "day",
                    },
                    "basis": "stub cohort member row; eligibility declared by tienshan_thermal_prereg_stub_v1",
                },
                "local": {"lat": float(r["Lat_lake"]), "lon": float(r["Lon_lake"]),
                          "lake": r["Lake_name"], "country": r["Country"],
                          "province": r.get("Province", "")},
                "provenance": {"gf_id": g, "lake_cluster": su["lake_cluster"],
                               "lake_type": r["Lake_type"], "driver": r["Driver_GLOF"],
                               "is_primary": g == su["primary_gf_id"]},
            })
    episode_map = {
        "schema": "P5_EVENT_EPISODE_MAPPING_V0",
        "derivation": "synthesized from tienshan_thermal_prereg_stub_v1 units; "
                      "member_ids restricted to the declared primary (most-recent rule) "
                      "so the sealed analyzer's earliest-member selection yields the stub primary",
        "heldout": True,
        "units": units,
    }
    decision = {
        "schema": "P5_EVENT_ADJUDICATION_V1",
        "derivation": "synthesized from tienshan_thermal_prereg_stub_v1 + HMAGLOFDB; "
                      "disposition ELIGIBLE is the cohort declaration",
        "events": decisions,
    }
    return episode_map, decision


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stub", required=True)
    ap.add_argument("--hmaglofdb", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    stub = json.loads(Path(a.stub).read_text())
    rows = {}
    with open(a.hmaglofdb, encoding="cp1252") as f:
        for r in csv.DictReader(f):
            rows.setdefault((r["GF_ID"], r["Country"]), []).append(r)
    db_rows = {}
    for k, rr in rows.items():
        if len(rr) > 1:
            raise ValueError(f"ambiguous HMAGLOFDB rows for {k}")
        db_rows[k] = rr[0]
    em, dec = build(stub, db_rows)
    out = Path(a.out_dir)
    for name, doc in (("tienshan_episode_map_v1.json", em),
                      ("tienshan_adjudication_decision_v1.json", dec)):
        doc["stub_sha256"] = sha256_file(a.stub)
        doc["hmaglofdb_sha256"] = sha256_file(a.hmaglofdb)
        write_once_json(out / name, doc, indent=2)
        write_once_sidecar(out / name)
        print("wrote", out / name)
    print(json.dumps({"units": len(em["units"]), "events": len(dec["events"])}))


if __name__ == "__main__":
    raise SystemExit(main())
