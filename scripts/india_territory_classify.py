"""Territory classification for the India Phase-0 lake frame.

Classifies every extracted lake row against the retained Survey-of-India
external boundary (SoI-derived via the DataMeet mirror under
data/survey-of-india/india-boundary) and three disputed-territory
polygons (PoK, Shaksgam, LSIB western sector).

Outputs a per-row territory artifact (write-once).  The lake frame
itself is sealed; territory is layered on top, bound by digests.

States:
  IN_COUNTRY        - inside the SoI boundary and outside every disputed
                      polygon: India-administered by evidence.
  DISPUTED_REVIEW   - inside the SoI claimed boundary but inside a known
                      disputed polygon (PoK, Shaksgam, Aksai Chin belt):
                      political claim, not de facto administration; must
                      not enter an India-administered denominator.
  OUTSIDE           - outside the SoI boundary entirely.
  UNASSESSED        - never emitted by this script; reserved for rows the
                      classifier cannot position (missing coordinates).

Honesty rule (owner decision 2026-09-28): an SoI political boundary is
not proof of de facto administration in disputed areas.  A lake inside
the SoI line but in PoK/Shaksgam/Aksai Chin is DISPUTED_REVIEW, never
IN_COUNTRY.  No substitution to GADM or Natural Earth is performed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402
import validate_india_source_intake as intake  # noqa: E402

SCHEMA = "INDIA_LAKE_TERRITORY_V0"
BOUNDARY_RELPATH = ("india-phase0-source-intake/territory-boundary"
                    "/IndiaBoundary.shp")
DISPUTED_RELPATHS = (
    "india-phase0-source-intake/territory-boundary/india-disputed-lsib.geojson",
    "india-phase0-source-intake/territory-boundary/pok-alhasan.geojson",
    "india-phase0-source-intake/territory-boundary/shaksgam-ne.geojson",
)
STATES = ("IN_COUNTRY", "DISPUTED_REVIEW", "OUTSIDE", "UNASSESSED")


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def classify(frame: dict, soi_union, disputed_union) -> dict:
    records = []
    for r in frame.get("records", []):
        loc = r.get("location", {})
        lat, lon = loc.get("latitude"), loc.get("longitude")
        if lat is None or lon is None:
            state = "UNASSESSED"
        else:
            from shapely.geometry import Point
            p = Point(lon, lat)
            if not soi_union.covers(p):
                state = "OUTSIDE"
            elif disputed_union.covers(p):
                state = "DISPUTED_REVIEW"
            else:
                state = "IN_COUNTRY"
        records.append({
            "source_record_id": r.get("source_record_id"),
            "source_lake_id": r.get("lake", {}).get("source_lake_id"),
            "basin": loc.get("basin"),
            "latitude": lat, "longitude": lon,
            "territory_state": state,
            "basis": ("SOI_EXTERNAL_BOUNDARY_VIA_DATAMEET + "
                      "DISPUTED_AREAS_LSIB_POK_SHAKSGAM"),
        })
    return {"records": records}


def build_artifact(frame_path, boundary_dir, out_path):
    import geopandas as gpd
    from shapely.ops import unary_union
    frame_path = Path(frame_path)
    boundary_dir = Path(boundary_dir)
    frame = json.loads(frame_path.read_text())
    soi = (gpd.read_file(boundary_dir / "IndiaBoundary.shp")
           .to_crs(4326).union_all())
    disputed = unary_union([gpd.read_file(boundary_dir / name).union_all()
                            for name in
                            ("india-disputed-lsib.geojson",
                             "pok-alhasan.geojson", "shaksgam-ne.geojson")])
    result = classify(frame, soi, disputed)
    counts = {}
    for rec in result["records"]:
        counts[rec["territory_state"]] = counts.get(
            rec["territory_state"], 0) + 1
    components = sorted(boundary_dir.iterdir())
    artifact = {
        "schema": SCHEMA, "version": 0,
        "claim_scope": "research_only_phase0_territory_classification",
        "sources": {
            "lake_frame": frame_path.name,
            "lake_frame_sha256": sha256_file(frame_path),
            "boundary": BOUNDARY_RELPATH,
            "boundary_sha256": sha256_file(
                boundary_dir / "IndiaBoundary.shp"),
            "disputed": {
                name: sha256_file(boundary_dir / Path(name).name)
                for name in DISPUTED_RELPATHS},
            "provenance": ("SoI external boundary via DataMeet mirror "
                           "(india-geodata repo, CC BY 4.0); SoI portal "
                           "download failed, derivative bytes used with "
                           "documented lineage"),
            "crs_note": ("IndiaBoundary.shp .prj declares EPSG:3857; "
                         "reprojected to EPSG:4326 for classification"),
        },
        "states": STATES,
        "method": ("point-in-polygon: inside SoI union -> IN_COUNTRY; "
                   "inside SoI union AND inside a disputed polygon -> "
                   "DISPUTED_REVIEW; otherwise OUTSIDE. Atlas basin names "
                   "are hydrologic, not territory, classifications."),
        "summary": {
            "lake_rows": len(result["records"]),
            "in_country": counts.get("IN_COUNTRY", 0),
            "disputed_review": counts.get("DISPUTED_REVIEW", 0),
            "outside": counts.get("OUTSIDE", 0),
            "unassessed": counts.get("UNASSESSED", 0),
        },
        "records": result["records"],
        "authority": dict(intake.AUTHORITY_FLAGS),
    }
    write_once_json(out_path, artifact)
    write_once_sidecar(out_path)
    return artifact


def validate_artifact(doc: dict) -> list[str]:
    problems = []
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return ["unexpected schema"]
    recs = doc.get("records")
    if not isinstance(recs, list) or not recs:
        problems.append("records must be a non-empty list")
        recs = []
    s = doc.get("summary", {})
    if not isinstance(s, dict):
        problems.append("summary must be an object")
        s = {}
    live = {k: 0 for k in STATES}
    for rec in recs:
        st = rec.get("territory_state")
        if st not in STATES:
            problems.append(f"unknown territory_state {st!r}")
            continue
        live[st] += 1
        if rec.get("latitude") is None or rec.get("longitude") is None:
            if st != "UNASSESSED":
                problems.append("coordinate-less row must be UNASSESSED")
    if s.get("lake_rows") != len(recs):
        problems.append("summary.lake_rows != len(records)")
    if s.get("in_country") != live["IN_COUNTRY"]:
        problems.append("summary.in_country != counted IN_COUNTRY")
    if s.get("disputed_review") != live["DISPUTED_REVIEW"]:
        problems.append("summary.disputed_review != counted")
    if s.get("outside") != live["OUTSIDE"]:
        problems.append("summary.outside != counted")
    if s.get("unassessed") != live["UNASSESSED"]:
        problems.append("summary.unassessed != counted")
    if doc.get("authority") != dict(intake.AUTHORITY_FLAGS):
        problems.append("authority flags must all be present and false")
    src = doc.get("sources", {})
    for key in ("lake_frame_sha256", "boundary_sha256"):
        if not isinstance(src.get(key), str) or len(src[key]) != 64:
            problems.append(f"sources.{key} must be a sha256")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", required=True)
    ap.add_argument("--boundary-dir", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--verify", help="revalidate an existing artifact")
    args = ap.parse_args()
    if args.verify:
        problems = validate_artifact(json.loads(
            Path(args.verify).read_text()))
        if problems:
            for p in problems:
                print("TERRITORY_FAIL:", p)
            return 1
        print("TERRITORY_OK:", args.verify)
        return 0
    artifact = build_artifact(args.frame, args.boundary_dir, args.out)
    problems = validate_artifact(artifact)
    if problems:
        for p in problems:
            print("TERRITORY_FAIL:", p)
        return 1
    print(json.dumps({"status": "TERRITORY_OK",
                      "summary": artifact["summary"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
