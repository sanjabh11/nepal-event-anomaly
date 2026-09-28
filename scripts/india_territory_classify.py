"""Source-relative spatial relations for the India Phase-0 lake frame.

Computes where each extracted atlas row's point falls relative to the
retained Survey-of-India-DERIVED boundary mirror (DataMeet/india-geodata,
CC BY 4.0) and three disputed-territory overlays.  This is a spatial
relation, NOT an administrative determination: the mirror is not
verified against official SoI vector bytes, LSIB documents that its
lines are not de facto control, and point placement is not positional
accuracy.  Authoritative territory_status remains UNASSESSED until the
boundary meaning and source are qualified by a digest-bound decision.

States (source-relative only):
  INSIDE_SOI_CLAIM          - inside the mirrored SoI boundary, outside
                              every disputed overlay
  INSIDE_DISPUTED_OVERLAY   - inside the mirrored SoI boundary AND at
                              least one disputed overlay (PoK, Shaksgam,
                              LSIB western sector)
  OUTSIDE_SOI_CLAIM         - outside the mirrored boundary entirely
  UNASSESSED                - missing/invalid coordinates

Rows within PROXIMITY_THRESHOLD_M of the boundary carry
proximity_to_boundary=True so boundary-adjacent placement is never
treated as certain at the atlas's three-decimal coordinate precision.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402
import validate_india_source_intake as intake  # noqa: E402

SCHEMA = "INDIA_LAKE_TERRITORY_V1"
EVIDENCE_ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
BOUNDARY_DIRNAME = "india-phase0-source-intake/territory-boundary"

SHP_COMPONENTS = (
    "IndiaBoundary.cpg", "IndiaBoundary.dbf", "IndiaBoundary.prj",
    "IndiaBoundary.sbn", "IndiaBoundary.sbx", "IndiaBoundary.shp",
    "IndiaBoundary.shp.xml", "IndiaBoundary.shx")
OVERLAY_FILES = {
    "pok": "pok-alhasan.geojson",
    "shaksgam": "shaksgam-ne.geojson",
    "lsib_disputed": "india-disputed-lsib.geojson",
}
STATES = ("INSIDE_SOI_CLAIM", "INSIDE_DISPUTED_OVERLAY",
          "OUTSIDE_SOI_CLAIM", "UNASSESSED")
PROXIMITY_THRESHOLD_M = 1000.0  # ~10x the 3-decimal coordinate step


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _valid_coord(lat, lon) -> bool:
    return (isinstance(lat, (int, float)) and isinstance(lon, (int, float))
            and not isinstance(lat, bool) and not isinstance(lon, bool)
            and math.isfinite(lat) and math.isfinite(lon)
            and -90.0 <= lat <= 90.0 and -180.0 <= lon <= 180.0)


def load_geometries(boundary_dir: Path):
    """Load + validate the bound geometry; returns (soi_union_4326,
    overlays dict name->union_4326)."""
    import geopandas as gpd
    boundary_dir = Path(boundary_dir)
    shp = boundary_dir / "IndiaBoundary.shp"
    soi = gpd.read_file(shp)
    if str(soi.crs) != "EPSG:3857":
        raise ValueError(
            f"boundary CRS must be EPSG:3857 as declared in .prj; got {soi.crs}")
    if len(soi) == 0:
        raise ValueError("boundary shapefile is empty")
    soi84 = soi.to_crs(4326).union_all()
    if not soi84.is_valid or soi84.is_empty:
        raise ValueError("boundary union geometry invalid or empty")
    overlays = {}
    for name, fname in OVERLAY_FILES.items():
        g = gpd.read_file(boundary_dir / fname)
        if len(g) == 0:
            raise ValueError(f"overlay {fname} empty")
        u = g.to_crs(4326).union_all()
        if not u.is_valid or u.is_empty:
            raise ValueError(f"overlay {fname} geometry invalid")
        overlays[name] = u
    return soi84, overlays


def _proximity_flag(soi_union, lon, lat, overlays_hit):
    """True when the point lies within PROXIMITY_THRESHOLD_M of the
    SoI boundary line (degrees approximated at the row's latitude)."""
    deg = PROXIMITY_THRESHOLD_M / 111_320.0
    return soi_union.boundary.distance(
        __import__("shapely.geometry", fromlist=["Point"]).Point(lon, lat)
    ) < deg


def classify(frame: dict, soi_union, overlays: dict) -> list[dict]:
    from shapely.geometry import Point
    records = []
    for r in frame.get("records", []):
        loc = r.get("location", {})
        lat, lon = loc.get("latitude"), loc.get("longitude")
        membership = {}
        if _valid_coord(lat, lon):
            p = Point(lon, lat)
            inside = soi_union.covers(p)
            for name, geom in overlays.items():
                membership[name] = bool(inside and geom.covers(p))
            state = ("OUTSIDE_SOI_CLAIM" if not inside else
                     "INSIDE_DISPUTED_OVERLAY" if any(membership.values())
                     else "INSIDE_SOI_CLAIM")
            proximity = _proximity_flag(soi_union, lon, lat,
                                        membership) if inside else False
        else:
            state = "UNASSESSED"
            proximity = None
        records.append({
            "source_record_id": r.get("source_record_id"),
            "source_lake_id": r.get("lake", {}).get("source_lake_id"),
            "basin": loc.get("basin"),
            "latitude": lat, "longitude": lon,
            "spatial_relation": state,
            "overlay_membership": membership,
            "proximity_to_boundary_m_lt": PROXIMITY_THRESHOLD_M
                if proximity else None,
            "basis": ("soi_claim_mirror_polygon + disputed overlays; "
                      "spatial relation only, not administration"),
        })
    return records


def component_manifest(boundary_dir: Path) -> dict:
    boundary_dir = Path(boundary_dir)
    entries = {}
    for name in list(SHP_COMPONENTS) + list(OVERLAY_FILES.values()):
        f = boundary_dir / name
        if not f.is_file():
            raise FileNotFoundError(f"missing boundary component {name}")
        entries[name] = {"sha256": sha256_file(f),
                         "size_bytes": f.stat().st_size}
    return entries


def build_artifact(frame_path, boundary_dir, out_path):
    frame_path = Path(frame_path)
    boundary_dir = Path(boundary_dir)
    frame = json.loads(frame_path.read_text())
    soi, overlays = load_geometries(boundary_dir)
    records = classify(frame, soi, overlays)
    counts = {s: 0 for s in STATES}
    prox = 0
    for rec in records:
        counts[rec["spatial_relation"]] += 1
        prox += 1 if rec["proximity_to_boundary_m_lt"] else 0
    artifact = {
        "schema": SCHEMA, "version": 1,
        "claim_scope": "research_only_phase0_spatial_relation",
        "meaning": ("Counts are ATLAS ROW positions relative to the "
                    "selected source polygons. They are not canonical "
                    "lakes, not verified Indian territory, and not "
                    "administration claims."),
        "sources": {
            "lake_frame": frame_path.name,
            "lake_frame_sha256": sha256_file(frame_path),
            "boundary_dir": BOUNDARY_DIRNAME,
            "component_digests": component_manifest(boundary_dir),
            "provenance": ("SoI external boundary via DataMeet mirror "
                           "(india-geodata repo, CC BY 4.0); derivative "
                           "of official product, unverified against "
                           "official SoI bytes; SoI portal download "
                           "failed repeatedly"),
            "overlay_sources": {
                "pok": "pok-alhasan.geojson (DataMeet, CC BY 4.0)",
                "shaksgam": "shaksgam-ne.geojson (Natural Earth de-facto view)",
                "lsib_disputed": "india-disputed-lsib.geojson (US State Dept LSIB; lines are not de facto control)"},
            "crs_note": (".prj declares EPSG:3857; classification "
                         "reprojects to EPSG:4326"),
        },
        "states": STATES,
        "proximity_rule": {
            "threshold_m": PROXIMITY_THRESHOLD_M,
            "meaning": ("rows within the threshold are flagged; "
                        "three-decimal coordinates (~111m) plus 1:1M "
                        "generalisation mean near-boundary placement is "
                        "uncertain and must be resolved before any "
                        "territory decision"),
        },
        "records": records,
        "summary": {
            "atlas_rows": len(records),
            "inside_soi_claim_rows": counts["INSIDE_SOI_CLAIM"],
            "inside_disputed_overlay_rows": counts["INSIDE_DISPUTED_OVERLAY"],
            "outside_soi_claim_rows": counts["OUTSIDE_SOI_CLAIM"],
            "unassessed_rows": counts["UNASSESSED"],
            "proximity_flagged_rows": prox,
        },
        "authority": dict(intake.AUTHORITY_FLAGS),
    }
    write_once_json(out_path, artifact)
    write_once_sidecar(out_path)
    return artifact


def validate_artifact(doc: dict) -> list[str]:
    """Structural validation only (cheap): shape, vocab, count
    consistency.  Full recomputation lives in verify_artifact."""
    problems = []
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA:
        return ["unexpected schema"]
    recs = doc.get("records")
    if not isinstance(recs, list) or not recs:
        return ["records must be a non-empty list"]
    seen_ids = set()
    live = {s: 0 for s in STATES}
    for i, rec in enumerate(recs):
        sid = rec.get("source_record_id")
        if not isinstance(sid, str) or sid in seen_ids:
            problems.append(f"records[{i}] duplicate/missing source_record_id")
        seen_ids.add(sid)
        st = rec.get("spatial_relation")
        if st not in STATES:
            problems.append(f"records[{i}] unknown spatial_relation {st!r}")
            continue
        live[st] += 1
        om = rec.get("overlay_membership")
        if not isinstance(om, dict) or sorted(om) != sorted(OVERLAY_FILES):
            problems.append(f"records[{i}] overlay_membership must cover "
                            f"{sorted(OVERLAY_FILES)}")
        if st == "INSIDE_DISPUTED_OVERLAY" and not any(
                isinstance(v, bool) and v for v in (om or {}).values()):
            problems.append(
                f"records[{i}] INSIDE_DISPUTED_OVERLAY with no overlay true")
        if st == "INSIDE_SOI_CLAIM" and any((om or {}).values()):
            problems.append(
                f"records[{i}] INSIDE_SOI_CLAIM but overlay membership true")
        if not _valid_coord(rec.get("latitude"), rec.get("longitude")) \
                and st != "UNASSESSED":
            problems.append(f"records[{i}] invalid coords must be UNASSESSED")
    s = doc.get("summary", {})
    if s.get("atlas_rows") != len(recs):
        problems.append("summary.atlas_rows != len(records)")
    for key, state in (("inside_soi_claim_rows", "INSIDE_SOI_CLAIM"),
                       ("inside_disputed_overlay_rows",
                        "INSIDE_DISPUTED_OVERLAY"),
                       ("outside_soi_claim_rows", "OUTSIDE_SOI_CLAIM"),
                       ("unassessed_rows", "UNASSESSED")):
        if s.get(key) != live[state]:
            problems.append(f"summary.{key} != counted {state}")
    if doc.get("authority") != dict(intake.AUTHORITY_FLAGS):
        problems.append("authority flags must all be present and false")
    src = doc.get("sources", {})
    if not isinstance(src.get("lake_frame_sha256"), str) \
            or len(src["lake_frame_sha256"]) != 64:
        problems.append("sources.lake_frame_sha256 must be a sha256")
    cd = src.get("component_digests")
    expected = set(SHP_COMPONENTS) | set(OVERLAY_FILES.values())
    if not isinstance(cd, dict) or set(cd) != expected:
        problems.append("component_digests must bind all 11 components")
    else:
        for name, ent in cd.items():
            if not isinstance(ent.get("sha256"), str) \
                    or len(ent["sha256"]) != 64 \
                    or not isinstance(ent.get("size_bytes"), int):
                problems.append(f"component digest malformed: {name}")
    return problems


def verify_artifact(artifact_path: str | Path,
                    boundary_dir: str | Path | None = None) -> list[str]:
    """Independent recomputation: reload the bound frame and geometry
    bytes, recompute every row's relation, and compare row-for-row.
    Fails on any component digest mismatch, missing sidecar, stale
    frame digest, or record drift."""
    problems = []
    artifact_path = Path(artifact_path)
    doc = json.loads(artifact_path.read_text())
    problems.extend(validate_artifact(doc))
    if problems:
        return problems
    sidecar = Path(str(artifact_path) + ".sha256")
    if not sidecar.is_file():
        problems.append("sidecar missing")
    elif sha256_file(artifact_path) != sidecar.read_text().split()[0]:
        problems.append("sidecar digest mismatch")
    src = doc["sources"]
    frame_rel = src["lake_frame"]
    frame_path = artifact_path.parent / frame_rel
    if not frame_path.is_file():
        frame_path = EVIDENCE_ROOT / "india-phase0-source-intake" / frame_rel
    if not frame_path.is_file():
        return problems + [f"bound frame missing: {frame_rel}"]
    if sha256_file(frame_path) != src["lake_frame_sha256"]:
        return problems + ["bound frame digest mismatch"]
    bdir = Path(boundary_dir) if boundary_dir else EVIDENCE_ROOT / BOUNDARY_DIRNAME
    if not bdir.is_dir():
        return problems + [f"boundary dir missing: {bdir}"]
    for name, ent in src["component_digests"].items():
        f = bdir / name
        if not f.is_file():
            problems.append(f"component missing: {name}")
            continue
        if sha256_file(f) != ent["sha256"]:
            problems.append(f"component digest mismatch: {name}")
        if f.stat().st_size != ent["size_bytes"]:
            problems.append(f"component size mismatch: {name}")
    if problems:
        return problems
    frame = json.loads(frame_path.read_text())
    frame_ids = [r.get("source_record_id") for r in frame.get("records", [])]
    art_ids = [r["source_record_id"] for r in doc["records"]]
    if frame_ids != art_ids:
        return problems + ["record ids do not correspond 1:1 with bound frame"]
    soi, overlays = load_geometries(bdir)
    recomputed = classify(frame, soi, overlays)
    for i, (a, b) in enumerate(zip(doc["records"], recomputed)):
        if (a["spatial_relation"] != b["spatial_relation"]
                or a["overlay_membership"] != b["overlay_membership"]
                or a.get("latitude") != b.get("latitude")
                or a.get("longitude") != b.get("longitude")):
            problems.append(f"records[{i}] recomputation mismatch")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", required=False)
    ap.add_argument("--boundary-dir", required=False)
    ap.add_argument("--out", required=False)
    ap.add_argument("--verify", help="recompute-check an existing artifact")
    args = ap.parse_args()
    if args.verify:
        problems = verify_artifact(args.verify, args.boundary_dir)
        if problems:
            for p in problems:
                print("TERRITORY_FAIL:", p)
            return 1
        print("TERRITORY_VERIFY_OK:", args.verify)
        return 0
    if not (args.frame and args.boundary_dir and args.out):
        ap.error("--frame --boundary-dir --out required unless --verify")
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
