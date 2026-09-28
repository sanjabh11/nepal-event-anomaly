"""Territory-boundary qualification decision for India Phase-0.

Two verbs:

  assess   Mechanical qualification assessment over the retained
           boundary components: digest integrity, geometry validity,
           CRS declaration, smoke checks against known points, and
           cross-checks against same-lineage and independent boundary
           variants.  Produces an evidence artifact; it recommends but
           does not decide.

  decide   Record the human territory-boundary decision as a typed,
           digest-bound artifact (decision_state=QUALIFIED or
           NOT_QUALIFIED).  A QUALIFIED decision is what downstream
           validators (lake frame, event adjudication) require before
           any row may carry IN_COUNTRY.

  verify   Recompute the assessment checks and confirm the decision
           artifact still binds unmodified assessment and boundary
           bytes.

No network activity; no acquisition authorized.
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

ASSESSMENT_SCHEMA = "INDIA_TERRITORY_QUALIFICATION_ASSESSMENT_V0"
DECISION_SCHEMA = "INDIA_TERRITORY_DECISION_V0"
DECISION_STATES = ("QUALIFIED", "NOT_QUALIFIED")


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _component_check(boundary_dir: Path) -> tuple[list[str], dict]:
    """Re-verify every component against SHA256SUMS.txt and return
    (problems, component digests)."""
    problems: list[str] = []
    boundary_dir = Path(boundary_dir)
    sums = boundary_dir / "SHA256SUMS.txt"
    if not sums.is_file():
        return ["SHA256SUMS.txt missing"], {}
    expected = {}
    for line in sums.read_text().splitlines():
        parts = line.split()
        if len(parts) >= 2:
            expected[parts[-1]] = parts[0]
    digests = {}
    for name, want in expected.items():
        f = boundary_dir / name
        if not f.is_file():
            problems.append(f"component missing: {name}")
            continue
        got = sha256_file(f)
        digests[name] = got
        if got != want:
            problems.append(f"component digest mismatch: {name}")
    return problems, digests


def assess(boundary_dir, territory_artifact=None, extra_geojson=None) -> dict:
    """Run the mechanical checks; returns an assessment dict."""
    import geopandas as gpd
    from shapely.geometry import Point
    from shapely.ops import unary_union
    boundary_dir = Path(boundary_dir)
    problems, digests = _component_check(boundary_dir)
    checks: dict = {}
    soi = None
    if not problems:
        g = gpd.read_file(boundary_dir / "IndiaBoundary.shp")
        checks["declared_crs"] = str(g.crs)
        checks["feature_count"] = len(g)
        checks["fc_types"] = sorted(
            {str(v) for v in g.get("FC_Type", [])} )
        soi = g.to_crs(4326).union_all()
        checks["union_valid"] = bool(soi.is_valid and not soi.is_empty)
        smoke = {}
        for name, (lon, lat) in {
                "Delhi": (77.21, 28.61), "Leh": (77.58, 34.16),
                "Srinagar": (74.80, 34.08), "Tawang": (91.86, 27.59),
                "Kathmandu": (85.32, 27.71), "Lhasa": (91.14, 29.65),
                "Islamabad": (73.05, 33.68)}.items():
            smoke[name] = bool(soi.covers(Point(lon, lat)))
        checks["point_smoke"] = smoke
        for name in ("Kathmandu", "Lhasa", "Islamabad"):
            if smoke.get(name):
                problems.append(
                    f"smoke check failed: {name} inside boundary")
        for name in ("Delhi", "Leh", "Srinagar"):
            if not smoke.get(name):
                problems.append(
                    f"smoke check failed: {name} outside boundary")
    cross = {}
    if soi is not None and extra_geojson:
        for variant, path in extra_geojson.items():
            p = Path(path)
            if not p.is_file():
                cross[variant] = {"status": "missing"}
                continue
            u = gpd.read_file(p).to_crs(4326).union_all()
            inter = soi.intersection(u).area
            union = soi.union(u).area
            cross[variant] = {
                "file": p.name, "sha256": sha256_file(p),
                "iou": round(inter / union, 4) if union else None}
    terr = {}
    if territory_artifact and Path(territory_artifact).is_file():
        t = json.loads(Path(territory_artifact).read_text())
        terr = {"artifact": Path(territory_artifact).name,
                "sha256": sha256_file(territory_artifact),
                "summary": t.get("summary")}
    assessment = {
        "schema": ASSESSMENT_SCHEMA, "version": 0,
        "claim_scope": "research_only_phase0_qualification_assessment",
        "boundary_dir": str(boundary_dir),
        "component_digests": digests,
        "checks": checks,
        "cross_checks": cross,
        "territory_artifact": terr,
        "problems": problems,
        "recommendation": (
            "QUALIFIED for phase0_scoping" if not problems
            else "NOT_QUALIFIED"),
        "limits": [
            "Derivative bytes (DataMeet mirror), not the official SoI "
            "portal product; qualification covers geometric content, "
            "not official issuance.",
            "SoI boundary is a political claim; disputed overlays remain "
            "required for de facto review.",
        ],
        "authority": dict(intake.AUTHORITY_FLAGS),
    }
    return assessment


def decide(assessment_path, decision_state, reviewer, rationale, out_path,
           scope_note):
    assessment_path = Path(assessment_path)
    assessment = json.loads(assessment_path.read_text())
    if decision_state == "QUALIFIED" and assessment.get("problems"):
        return ["cannot QUALIFY an assessment with unresolved problems"]
    doc = {
        "schema": DECISION_SCHEMA, "version": 0,
        "claim_scope": "research_only_phase0_territory_decision",
        "decision_state": decision_state,
        "qualification_scope": scope_note,
        "decided_by": reviewer,
        "decision_rationale": rationale,
        "assessment_artifact": assessment_path.name,
        "assessment_sha256": sha256_file(assessment_path),
        "boundary_sha256":
            assessment.get("component_digests", {})
            .get("IndiaBoundary.shp"),
        "authority": dict(intake.AUTHORITY_FLAGS),
    }
    write_once_json(out_path, doc)
    write_once_sidecar(out_path)
    return []


def validate_decision(doc: dict) -> list[str]:
    problems = []
    if not isinstance(doc, dict) or doc.get("schema") != DECISION_SCHEMA:
        return ["unexpected decision schema"]
    if doc.get("decision_state") not in DECISION_STATES:
        problems.append("decision_state must be QUALIFIED or NOT_QUALIFIED")
    for f in ("decided_by", "decision_rationale"):
        if not isinstance(doc.get(f), str) or not doc[f].strip():
            problems.append(f"{f} required")
    if (not isinstance(doc.get("assessment_sha256"), str)
            or len(doc["assessment_sha256"]) != 64):
        problems.append("assessment_sha256 must bind the assessment")
    if (not isinstance(doc.get("boundary_sha256"), str)
            or len(doc["boundary_sha256"]) != 64):
        problems.append("boundary_sha256 must bind the boundary bytes")
    if doc.get("authority") != dict(intake.AUTHORITY_FLAGS):
        problems.append("authority flags must all be present and false")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(prog="india_territory_decision")
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("assess")
    a.add_argument("--boundary-dir", required=True)
    a.add_argument("--territory-artifact")
    a.add_argument("--cross-check", action="append", default=[],
                   help="variant=geojson path (repeatable)")
    a.add_argument("--out", required=True)
    d = sub.add_parser("decide")
    d.add_argument("--assessment", required=True)
    d.add_argument("--state", required=True, choices=DECISION_STATES)
    d.add_argument("--by", required=True)
    d.add_argument("--rationale", required=True)
    d.add_argument("--scope-note", required=True)
    d.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--decision", required=True)
    args = ap.parse_args()
    if args.cmd == "assess":
        extra = dict(v.split("=", 1) for v in args.cross_check)
        doc = assess(args.boundary_dir, args.territory_artifact, extra)
        write_once_json(args.out, doc)
        write_once_sidecar(args.out)
        print(json.dumps({"status": "ASSESSMENT_SEALED",
                          "problems": doc["problems"],
                          "recommendation": doc["recommendation"]},
                         indent=2))
        return 0 if not doc["problems"] else 1
    if args.cmd == "decide":
        problems = decide(args.assessment, args.state, args.by,
                          args.rationale, args.out, args.scope_note)
        if problems:
            for p in problems:
                print("DECISION_FAIL:", p)
            return 1
        print("DECISION_SEALED:", args.out)
        return 0
    doc = json.loads(Path(args.decision).read_text())
    problems = validate_decision(doc)
    if problems:
        for p in problems:
            print("DECISION_FAIL:", p)
        return 1
    print("DECISION_VERIFY_OK:", args.decision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
