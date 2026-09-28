"""Fail-closed territory evidence qualification for India Phase 0.

The current SoI-derived mirror can qualify only a source-relative spatial
screen.  It cannot qualify a lake/event as India-administered.  This version
does not issue administrative qualifications at all; a future, separately
reviewed administrative-evidence contract is required before downstream
territory labels can change from UNASSESSED.

No network activity or acquisition is performed here.  Reviewer identity is
attribution, not cryptographic authentication.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402
import india_territory_classify as territory  # noqa: E402
import validate_india_source_intake as intake  # noqa: E402

ASSESSMENT_SCHEMA = "INDIA_TERRITORY_QUALIFICATION_ASSESSMENT_V1"
DECISION_SCHEMA = "INDIA_TERRITORY_DECISION_V1"
DECISION_STATES = ("QUALIFIED", "NOT_QUALIFIED")
SOURCE_RELATIVE_SCOPE = "SOURCE_RELATIVE_PHASE0_SCOPING_ONLY"
ADMINISTRATION_SCOPE = "INDIA_ADMINISTERED_TERRITORY"
# This implementation only has evidence for source-relative spatial
# screening.  The administrative scope is deliberately not issuable here;
# it requires a separate evidence contract and owner review.
QUALIFICATION_SCOPES = (SOURCE_RELATIVE_SCOPE,)
HEX_SHA256 = re.compile(r"^[0-9a-f]{64}$")
REQUIRED_CROSS_CHECKS = {"same_lineage", "independent_osm"}
ASSESSMENT_FIELDS = {
    "schema", "version", "claim_scope", "boundary_dir",
    "component_digests", "qualification_capability",
    "source_authenticity_verified", "disputed_areas_resolved",
    "checks", "cross_checks", "territory_artifact", "problems",
    "recommendation", "limits", "authority",
}
DECISION_FIELDS = {
    "schema", "version", "claim_scope", "decision_state",
    "qualification_scope", "reviewer", "reviewer_identity_binding",
    "decision_rationale",
    "assessment_artifact", "assessment_sha256", "component_digests",
    "authority",
}
ADMIN_CAPABILITY = "AUTHENTICATED_ADMINISTRATION_BOUNDARY"
SOURCE_RELATIVE_CAPABILITY = "SOURCE_RELATIVE_ONLY"


def sha256_file(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sidecar_problems(path: Path) -> list[str]:
    sidecar = Path(str(path) + ".sha256")
    if not path.is_file() or path.is_symlink():
        return [f"artifact missing: {path.name}"]
    if not sidecar.is_file() or sidecar.is_symlink():
        return [f"artifact sidecar missing: {sidecar.name}"]
    tokens = sidecar.read_text(encoding="utf-8").split()
    if (len(tokens) != 2 or not HEX_SHA256.fullmatch(tokens[0])
            or tokens[0] != sha256_file(path)
            or tokens[1] != path.name):
        return [f"artifact sidecar mismatch: {sidecar.name}"]
    return []


def _required_component_names() -> set[str]:
    return set(territory.SHP_COMPONENTS) | set(territory.OVERLAY_FILES.values())


def _component_check(boundary_dir: Path) -> tuple[list[str], dict[str, str]]:
    """Verify the exact expected boundary/overlay component set.

    SHA256SUMS.txt is a checksum list, not a source of authority.  Exact set
    equality prevents omitted or injected components from silently changing
    the geometry contract.
    """
    problems: list[str] = []
    boundary_dir = Path(boundary_dir).resolve()
    sums = boundary_dir / "SHA256SUMS.txt"
    if not sums.is_file():
        return ["SHA256SUMS.txt missing"], {}
    expected: dict[str, str] = {}
    for line_number, line in enumerate(sums.read_text(encoding="utf-8").splitlines(), 1):
        parts = line.split()
        if len(parts) != 2 or not HEX_SHA256.fullmatch(parts[0]):
            problems.append(f"invalid SHA256SUMS.txt line {line_number}")
            continue
        name = parts[1].lstrip("*")
        if name in expected:
            problems.append(f"duplicate checksum entry: {name}")
            continue
        expected[name] = parts[0]
    required = _required_component_names()
    if set(expected) != required:
        problems.append(
            "checksum component set mismatch: "
            f"missing={sorted(required - set(expected))}, "
            f"extra={sorted(set(expected) - required)}")
    digests: dict[str, str] = {}
    for name in sorted(required):
        f = boundary_dir / name
        if not f.is_file() or f.is_symlink():
            problems.append(f"component missing or not a regular file: {name}")
            continue
        got = sha256_file(f)
        digests[name] = got
        if expected.get(name) != got:
            problems.append(f"component digest mismatch: {name}")
    return problems, digests


def _validate_assessment(doc: dict) -> list[str]:
    if not isinstance(doc, dict) or doc.get("schema") != ASSESSMENT_SCHEMA:
        return ["unexpected assessment schema"]
    problems: list[str] = []
    if set(doc) != ASSESSMENT_FIELDS:
        problems.append("assessment fields must exactly match V1")
    if doc.get("version") != 1 or isinstance(doc.get("version"), bool):
        problems.append("assessment version must be 1")
    if doc.get("claim_scope") != "research_only_phase0_qualification_assessment":
        problems.append("unexpected assessment claim_scope")
    if not isinstance(doc.get("boundary_dir"), str) or not doc["boundary_dir"].strip():
        problems.append("boundary_dir is required")
    digests = doc.get("component_digests")
    if (not isinstance(digests, dict)
            or set(digests) != _required_component_names()
            or any(not isinstance(v, str) or not HEX_SHA256.fullmatch(v)
                   for v in digests.values())):
        problems.append("component_digests must bind the exact required component set")
    if doc.get("qualification_capability") != SOURCE_RELATIVE_CAPABILITY:
        problems.append("only source-relative assessment capability is supported")
    if not isinstance(doc.get("source_authenticity_verified"), bool):
        problems.append("source_authenticity_verified must be boolean")
    if not isinstance(doc.get("disputed_areas_resolved"), bool):
        problems.append("disputed_areas_resolved must be boolean")
    source_only = doc.get("qualification_capability") == SOURCE_RELATIVE_CAPABILITY
    if source_only and (doc.get("source_authenticity_verified")
                        or doc.get("disputed_areas_resolved")):
        problems.append("source-relative assessment cannot assert admin qualification")
    if doc.get("source_authenticity_verified") is not False:
        problems.append("source authenticity is not established by this assessor")
    if doc.get("disputed_areas_resolved") is not False:
        problems.append("disputed-area administration is not resolved by this assessor")
    if not isinstance(doc.get("checks"), dict):
        problems.append("checks must be an object")
    cross_checks = doc.get("cross_checks")
    cross_check_failure = False
    if not isinstance(cross_checks, dict):
        problems.append("cross_checks must be an object")
        cross_check_failure = True
    elif set(cross_checks) != REQUIRED_CROSS_CHECKS:
        problems.append("required cross-check set is incomplete or unexpected")
        cross_check_failure = True
    else:
        for name, check in cross_checks.items():
            if not isinstance(check, dict):
                problems.append(f"{name}: cross-check must be an object")
                continue
            status = check.get("status")
            if status == "missing":
                cross_check_failure = True
                if set(check) != {"status"}:
                    problems.append(f"{name}: malformed missing cross-check")
                continue
            if (status not in {"checked", "invalid", "not_comparable"}
                    or not isinstance(check.get("path"), str)
                    or not check["path"].strip()
                    or not isinstance(check.get("file"), str)
                    or Path(check["file"]).name != check["file"]
                    or not isinstance(check.get("sha256"), str)
                    or not HEX_SHA256.fullmatch(check["sha256"])):
                problems.append(f"{name}: invalid or unbound required cross-check")
                cross_check_failure = True
                continue
            if status != "checked":
                cross_check_failure = True
            if status == "checked" and (
                    isinstance(check.get("iou_equal_area"), bool)
                    or not isinstance(check.get("iou_equal_area"), (int, float))
                    or not 0 <= check["iou_equal_area"] <= 1):
                problems.append(f"{name}: checked cross-check lacks valid IoU")
                cross_check_failure = True
    if not isinstance(doc.get("territory_artifact"), dict):
        problems.append("territory_artifact must be an object")
    assessment_problems = doc.get("problems")
    if not isinstance(assessment_problems, list) or any(
            not isinstance(v, str) for v in assessment_problems):
        problems.append("problems must be a list of strings")
        assessment_problems = ["invalid assessment problems field"]
    if cross_check_failure and not assessment_problems:
        problems.append("failed or missing required cross-check lacks a problem record")
    expected_recommendation = (
        "NOT_QUALIFIED" if assessment_problems else "SOURCE_RELATIVE_ONLY")
    if doc.get("recommendation") != expected_recommendation:
        problems.append("recommendation does not match capability and problems")
    if not isinstance(doc.get("limits"), list) or any(
            not isinstance(v, str) for v in doc.get("limits", [])):
        problems.append("limits must be a list of strings")
    if doc.get("authority") != dict(intake.AUTHORITY_FLAGS):
        problems.append("authority flags must all be present and false")
    return problems


def assess(boundary_dir, territory_artifact=None, extra_geojson=None) -> dict:
    """Record mechanical geometry checks without promoting source semantics."""
    import geopandas as gpd
    from shapely.geometry import Point

    boundary_dir = Path(boundary_dir).resolve()
    problems, digests = _component_check(boundary_dir)
    checks: dict = {}
    soi = None
    if not problems:
        try:
            g = gpd.read_file(boundary_dir / "IndiaBoundary.shp")
            checks["declared_crs"] = str(g.crs)
            checks["feature_count"] = len(g)
            checks["fc_types"] = sorted({str(v) for v in g.get("FC_Type", [])})
            if g.crs is None or g.crs.to_epsg() != 3857:
                problems.append("boundary CRS must resolve to EPSG:3857")
            if len(g) == 0 or any(
                    geom is None or geom.is_empty or not geom.is_valid
                    or geom.geom_type not in {"Polygon", "MultiPolygon"}
                    for geom in g.geometry):
                problems.append("boundary must contain valid polygon geometries")
            if not problems:
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
                        problems.append(f"smoke check failed: {name} inside boundary")
                for name in ("Delhi", "Leh", "Srinagar"):
                    if not smoke.get(name):
                        problems.append(f"smoke check failed: {name} outside boundary")
        except Exception as exc:
            problems.append(f"boundary geometry could not be qualified: {exc}")

    cross: dict = {}
    if extra_geojson:
        for variant, raw_path in sorted(extra_geojson.items()):
            p = Path(raw_path).resolve()
            if not p.is_file() or p.is_symlink():
                problems.append(f"cross-check input missing: {variant}")
                cross[variant] = {"status": "missing"}
                continue
            sidecar_problems = _sidecar_problems(p)
            if sidecar_problems:
                problems.append(
                    f"cross-check sidecar invalid ({variant}): "
                    + "; ".join(sidecar_problems))
            try:
                other = gpd.read_file(p)
                if (other.crs is None or len(other) == 0 or any(
                        geom is None or geom.is_empty or not geom.is_valid
                        or geom.geom_type not in {"Polygon", "MultiPolygon"}
                        for geom in other.geometry)):
                    raise ValueError("cross-check must be valid polygon geometry with CRS")
                if soi is None:
                    cross[variant] = {
                        "file": p.name, "path": str(p),
                        "sha256": sha256_file(p), "status": "not_comparable"}
                    continue
                # Area overlap is computed in an equal-area CRS, not degrees.
                base_eq = gpd.GeoSeries([soi], crs="EPSG:4326").to_crs(6933).iloc[0]
                other_eq = other.to_crs(6933).union_all()
                inter = base_eq.intersection(other_eq).area
                union = base_eq.union(other_eq).area
                cross[variant] = {
                    "file": p.name, "path": str(p),
                    "sha256": sha256_file(p),
                    "iou_equal_area": round(inter / union, 6) if union else None,
                    "status": "checked",
                }
            except Exception as exc:
                problems.append(f"cross-check invalid ({variant}): {exc}")
                cross[variant] = {
                    "file": p.name, "path": str(p),
                    "sha256": sha256_file(p), "status": "invalid"}

    terr = {}
    if territory_artifact is not None:
        path = Path(territory_artifact).resolve()
        sidecar_problems = _sidecar_problems(path)
        if sidecar_problems:
            problems.append("territory artifact input is missing")
        else:
            try:
                t = json.loads(path.read_text(encoding="utf-8"))
                terr = {"artifact": path.name, "path": str(path.resolve()),
                        "sha256": sha256_file(path),
                        "summary": t.get("summary"), "status": "checked"}
            except (OSError, json.JSONDecodeError) as exc:
                problems.append(f"territory artifact is unreadable: {exc}")
    else:
        terr = {"artifact": None, "path": None, "sha256": None,
                "summary": None, "status": "not_supplied"}

    # Every named comparison is a required input, not an optional success.
    cross = {name: {"status": "missing"} for name in sorted(
        REQUIRED_CROSS_CHECKS - set(extra_geojson or {}))} | cross
    for name in sorted(set(extra_geojson or {}) - REQUIRED_CROSS_CHECKS):
        problems.append(f"unexpected cross-check input: {name}")
    if set(cross) != REQUIRED_CROSS_CHECKS:
        problems.append("required cross-check inputs are missing")

    capability = SOURCE_RELATIVE_CAPABILITY
    assessment = {
        "schema": ASSESSMENT_SCHEMA, "version": 1,
        "claim_scope": "research_only_phase0_qualification_assessment",
        "boundary_dir": str(boundary_dir),
        "component_digests": digests,
        "qualification_capability": capability,
        "source_authenticity_verified": False,
        "disputed_areas_resolved": False,
        "checks": checks,
        "cross_checks": cross,
        "territory_artifact": terr,
        "problems": problems,
        "recommendation": "NOT_QUALIFIED" if problems else "SOURCE_RELATIVE_ONLY",
        "limits": [
            "The retained DataMeet bytes are a derivative, not authenticated official SoI vector bytes.",
            "A political-claim geometry and point-in-polygon result do not establish de facto administration.",
            "This assessment cannot authorize IN_COUNTRY or OUTSIDE territory labels.",
            "Reviewer identity is an assertion and is not cryptographically authenticated.",
        ],
        "authority": dict(intake.AUTHORITY_FLAGS),
    }
    # Assert producer/validator parity before the caller seals the assessment.
    validation = _validate_assessment(assessment)
    if validation:
        assessment["problems"].extend(validation)
        assessment["recommendation"] = "NOT_QUALIFIED"
    return assessment


def _recompute_assessment(assessment: dict) -> tuple[dict | None, list[str]]:
    """Rebuild every mechanically derived assessment field from live inputs."""
    cross_checks = assessment.get("cross_checks")
    if not isinstance(cross_checks, dict):
        return None, ["assessment cross_checks must be an object"]
    paths = {}
    for name, check in cross_checks.items():
        if not isinstance(check, dict):
            return None, [f"{name}: cross-check must be an object"]
        if check.get("status") == "missing":
            if set(check) != {"status"}:
                return None, [f"{name}: malformed missing cross-check"]
            continue
        if not isinstance(check.get("path"), str):
            return None, [f"{name}: cross-check path is missing"]
        paths[name] = check["path"]
    artifact = assessment.get("territory_artifact")
    if not isinstance(artifact, dict):
        return None, ["assessment territory_artifact must be an object"]
    artifact_path = artifact.get("path")
    if artifact.get("status") == "not_supplied":
        if artifact_path is not None:
            return None, ["unsupplied territory artifact has a path"]
        artifact_path = None
    elif not isinstance(artifact_path, str) or not artifact_path:
        return None, ["territory artifact path is missing"]
    recomputed = assess(assessment.get("boundary_dir"), artifact_path, paths)
    if recomputed != assessment:
        differing = sorted(
            key for key in set(recomputed) | set(assessment)
            if recomputed.get(key) != assessment.get(key))
        return recomputed, [
            "assessment recomputation mismatch: " + ", ".join(differing)]
    return recomputed, []


def verify_assessment(assessment_path) -> list[str]:
    assessment_path = Path(assessment_path)
    problems = _sidecar_problems(assessment_path)
    if problems:
        return problems
    try:
        assessment = json.loads(assessment_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"assessment is unreadable: {exc}"]
    problems = _validate_assessment(assessment)
    if problems:
        return ["assessment validation failed: " + "; ".join(problems)]
    _, problems = _recompute_assessment(assessment)
    return problems


def decide(assessment_path, decision_state, reviewer, rationale, out_path,
           scope_note):
    """Create a typed decision; free-text can no longer widen its scope."""
    assessment_path = Path(assessment_path)
    problems = verify_assessment(assessment_path)
    if problems:
        return problems
    try:
        assessment = json.loads(assessment_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"assessment is unreadable: {exc}"]
    if decision_state not in DECISION_STATES:
        return ["decision_state must be QUALIFIED or NOT_QUALIFIED"]
    if scope_note not in QUALIFICATION_SCOPES:
        return ["scope must be one of the exact typed qualification scopes"]
    if decision_state == "QUALIFIED":
        if assessment.get("problems"):
            return ["cannot QUALIFY an assessment with unresolved problems"]
        if scope_note == SOURCE_RELATIVE_SCOPE and (
                assessment.get("qualification_capability") != SOURCE_RELATIVE_CAPABILITY):
            return ["assessment capability does not match source-relative scope"]
    if not isinstance(reviewer, dict):
        return ["reviewer must be a typed owner-review object"]
    doc = {
        "schema": DECISION_SCHEMA, "version": 1,
        "claim_scope": "research_only_phase0_territory_decision",
        "decision_state": decision_state,
        "qualification_scope": scope_note,
        "reviewer": reviewer,
        "reviewer_identity_binding": "ASSERTED_NOT_CRYPTOGRAPHIC",
        "decision_rationale": rationale,
        "assessment_artifact": assessment_path.name,
        "assessment_sha256": sha256_file(assessment_path),
        "component_digests": assessment["component_digests"],
        "authority": dict(intake.AUTHORITY_FLAGS),
    }
    problems = validate_decision(doc)
    if problems:
        return problems
    write_once_json(out_path, doc)
    write_once_sidecar(out_path)
    return []


def validate_decision(doc: dict) -> list[str]:
    if not isinstance(doc, dict) or doc.get("schema") != DECISION_SCHEMA:
        return ["unexpected decision schema"]
    problems = []
    if set(doc) != DECISION_FIELDS:
        problems.append("decision fields must exactly match V1")
    if doc.get("version") != 1 or isinstance(doc.get("version"), bool):
        problems.append("decision version must be 1")
    if doc.get("claim_scope") != "research_only_phase0_territory_decision":
        problems.append("unexpected decision claim_scope")
    if doc.get("decision_state") not in DECISION_STATES:
        problems.append("decision_state must be QUALIFIED or NOT_QUALIFIED")
    if doc.get("qualification_scope") not in QUALIFICATION_SCOPES:
        problems.append("qualification_scope must be a supported exact value")
    reviewer = doc.get("reviewer")
    if (not isinstance(reviewer, dict) or set(reviewer) != {"id", "role"}
            or not isinstance(reviewer.get("id"), str)
            or not reviewer["id"].strip()
            or reviewer.get("role") != "owner"):
        problems.append("reviewer must have a non-empty id and exact role owner")
    if doc.get("reviewer_identity_binding") != "ASSERTED_NOT_CRYPTOGRAPHIC":
        problems.append("reviewer identity limitation must be explicit")
    for f in ("decision_rationale", "assessment_artifact"):
        if not isinstance(doc.get(f), str) or not doc[f].strip():
            problems.append(f"{f} required")
    if (not isinstance(doc.get("assessment_artifact"), str)
            or Path(doc["assessment_artifact"]).name != doc["assessment_artifact"]):
        problems.append("assessment_artifact must be a basename")
    if (not isinstance(doc.get("assessment_sha256"), str)
            or not HEX_SHA256.fullmatch(doc["assessment_sha256"])):
        problems.append("assessment_sha256 must be lowercase SHA-256")
    components = doc.get("component_digests")
    if (not isinstance(components, dict)
            or set(components) != _required_component_names()
            or any(not isinstance(v, str) or not HEX_SHA256.fullmatch(v)
                   for v in components.values())):
        problems.append("component_digests must bind every boundary component")
    if (doc.get("decision_state") == "QUALIFIED"
            and doc.get("qualification_scope") != SOURCE_RELATIVE_SCOPE):
        problems.append("this decision path cannot qualify administrative territory")
    if doc.get("authority") != dict(intake.AUTHORITY_FLAGS):
        problems.append("authority flags must all be present and false")
    return problems


def verify_decision(decision_path) -> list[str]:
    """Resolve and verify decision, assessment, sidecars, and live components."""
    decision_path = Path(decision_path)
    problems = _sidecar_problems(decision_path)
    if problems:
        return problems
    try:
        doc = json.loads(decision_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"decision is unreadable: {exc}"]
    problems = validate_decision(doc)
    if problems:
        return problems
    assessment_path = decision_path.parent / doc["assessment_artifact"]
    problems = verify_assessment(assessment_path)
    if problems:
        return problems
    if sha256_file(assessment_path) != doc["assessment_sha256"]:
        return ["assessment digest does not match decision"]
    try:
        assessment = json.loads(assessment_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"assessment is unreadable: {exc}"]
    if doc["component_digests"] != assessment["component_digests"]:
        return ["decision component digests differ from assessment"]
    if doc["decision_state"] == "QUALIFIED":
        scope = doc["qualification_scope"]
        if assessment.get("problems") or assessment.get(
                "recommendation") != "SOURCE_RELATIVE_ONLY":
            return ["qualified decision requires a clean source-relative assessment"]
        if scope == SOURCE_RELATIVE_SCOPE and (
                assessment["qualification_capability"] != SOURCE_RELATIVE_CAPABILITY):
            return ["source-relative scope does not match assessment capability"]
        if scope != SOURCE_RELATIVE_SCOPE:
            return ["decision cannot authorize administrative territory"]
    return []


def _absolute_path(path) -> Path:
    return Path(os.path.abspath(os.fspath(path)))


def _decision_chain_paths(decision_path, decision_doc):
    """Resolve the exact files consumed by decision verification."""
    files: set[Path] = set()
    directories: set[Path] = set()

    def add_file(path, *, sidecar=False):
        resolved = _absolute_path(path)
        files.add(resolved)
        directories.add(resolved.parent)
        if sidecar:
            files.add(_absolute_path(Path(str(resolved) + ".sha256")))

    add_file(decision_path, sidecar=True)
    assessment_name = decision_doc.get("assessment_artifact")
    if (not isinstance(assessment_name, str)
            or Path(assessment_name).name != assessment_name):
        raise ValueError("assessment_artifact must be a basename")
    assessment_path = _absolute_path(Path(decision_path).parent / assessment_name)
    add_file(assessment_path, sidecar=True)
    assessment = json.loads(assessment_path.read_text(encoding="utf-8"))
    boundary_dir = assessment.get("boundary_dir")
    if not isinstance(boundary_dir, str) or not boundary_dir.strip():
        raise ValueError("assessment boundary_dir is missing")
    boundary_dir = _absolute_path(boundary_dir)
    directories.add(boundary_dir)
    add_file(boundary_dir / "SHA256SUMS.txt")
    for name in sorted(_required_component_names()):
        add_file(boundary_dir / name)

    checks = assessment.get("cross_checks")
    if isinstance(checks, dict):
        for check in checks.values():
            if isinstance(check, dict) and isinstance(check.get("path"), str):
                add_file(check["path"], sidecar=True)
    territory_artifact = assessment.get("territory_artifact")
    if (isinstance(territory_artifact, dict)
            and isinstance(territory_artifact.get("path"), str)):
        add_file(territory_artifact["path"], sidecar=True)
    return sorted(files), sorted(directories)


def _decision_chain_content_digests(decision_path):
    """Hash the decision's full evidence chain for run-scoped cache checks."""
    decision_path = _absolute_path(decision_path)
    try:
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        paths, _ = _decision_chain_paths(decision_path, decision)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return None, [f"decision verification inputs are unreadable: {exc}"]
    digests = {}
    for path in paths:
        if path.is_symlink() or not path.is_file():
            return None, [f"decision verification input missing or unsafe: {path.name}"]
        try:
            digests[str(path)] = sha256_file(path)
        except OSError as exc:
            return None, [f"decision verification input unreadable ({path.name}): {exc}"]
    return digests, []


def _decision_chain_stat_signature(decision_path):
    """Cheap same-run invalidation signal; final checks still compare hashes."""
    decision_path = _absolute_path(decision_path)
    try:
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
        files, directories = _decision_chain_paths(decision_path, decision)
    except (OSError, json.JSONDecodeError, ValueError):
        files = [decision_path, _absolute_path(str(decision_path) + ".sha256")]
        directories = [decision_path.parent]

    signature = []
    for path in sorted(set(files) | set(directories)):
        try:
            link_stat = path.lstat()
            target_stat = path.stat()
            signature.append((
                str(path), "present",
                link_stat.st_mode, link_stat.st_dev, link_stat.st_ino,
                link_stat.st_size, link_stat.st_mtime_ns, link_stat.st_ctime_ns,
                target_stat.st_mode, target_stat.st_dev, target_stat.st_ino,
                target_stat.st_size, target_stat.st_mtime_ns,
                target_stat.st_ctime_ns,
            ))
        except OSError as exc:
            signature.append((str(path), "unavailable", type(exc).__name__,
                              getattr(exc, "errno", None)))
    return tuple(signature)


class TerritoryDecisionVerificationCache:
    """Per-validation cache; never persists trust across validation runs.

    The decision bytes are re-hashed on every lookup. Dependency metadata
    invalidates the cache during a run, and callers must invoke
    ``verify_unchanged`` before accepting the enclosing validation result;
    that final check compares SHA-256 for every dependency byte.
    """

    def __init__(self):
        self._entries = {}

    def verify(self, decision_path, expected_sha256):
        decision_path = _absolute_path(decision_path)
        key = (str(decision_path), expected_sha256)
        try:
            if sha256_file(decision_path) != expected_sha256:
                self._entries.pop(key, None)
                return ["territory evidence digest does not match decision bytes"], None
        except OSError as exc:
            self._entries.pop(key, None)
            return [f"territory decision is unreadable: {exc}"], None

        signature = _decision_chain_stat_signature(decision_path)
        cached = self._entries.get(key)
        if cached and cached["stat_signature"] == signature:
            return [], cached["decision"]
        self._entries.pop(key, None)

        before, snapshot_problems = _decision_chain_content_digests(decision_path)
        if snapshot_problems:
            return ["territory decision verification failed: "
                    + "; ".join(snapshot_problems)], None
        problems = verify_decision(decision_path)
        if problems:
            return ["territory decision verification failed: "
                    + "; ".join(problems)], None
        try:
            decision = json.loads(decision_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            return [f"territory decision is unreadable: {exc}"], None
        after, snapshot_problems = _decision_chain_content_digests(decision_path)
        if snapshot_problems:
            return ["territory decision verification failed: "
                    + "; ".join(snapshot_problems)], None
        if before != after or sha256_file(decision_path) != expected_sha256:
            return ["territory decision verification inputs changed during validation"], None
        after_signature = _decision_chain_stat_signature(decision_path)
        if signature != after_signature:
            return ["territory decision verification inputs changed during validation"], None
        self._entries[key] = {
            "decision": decision,
            "input_digests": after,
            "stat_signature": after_signature,
            "path": decision_path,
        }
        return [], decision

    def verify_unchanged(self):
        """Fail the enclosing validation if any cached chain changed mid-run."""
        problems = []
        for entry in self._entries.values():
            current, snapshot_problems = _decision_chain_content_digests(
                entry["path"])
            if snapshot_problems:
                problems.append("territory decision verification inputs changed "
                                "during validation: " + "; ".join(snapshot_problems))
            elif current != entry["input_digests"]:
                problems.append("territory decision verification inputs changed "
                                "during validation")
        return problems


def verify_territory_evidence(evidence, evidence_dir, *, require_administration,
                              verifier=None):
    """Validate a frame/adjudication reference against resolved decision bytes."""
    problems: list[str] = []
    expected_fields = {"artifact", "artifact_sha256", "decision_state", "binding"}
    if not isinstance(evidence, dict) or set(evidence) != expected_fields:
        return ["territory_evidence must contain the exact typed reference fields"]
    name = evidence.get("artifact")
    digest = evidence.get("artifact_sha256")
    if (not isinstance(name, str) or not name or Path(name).name != name
            or name in {".", ".."}):
        return ["territory_evidence artifact must be a basename"]
    if not isinstance(digest, str) or not HEX_SHA256.fullmatch(digest):
        return ["territory_evidence artifact_sha256 must be lowercase SHA-256"]
    if evidence.get("binding") != "sha256":
        return ["territory_evidence binding must be sha256"]
    if evidence.get("decision_state") != "QUALIFIED":
        return ["territory_evidence decision_state must be QUALIFIED"]
    if evidence_dir is None:
        return ["territory evidence directory is required for byte verification"]
    decision_path = Path(evidence_dir) / name
    if verifier is None:
        problems = verify_decision(decision_path)
        if problems:
            return ["territory decision verification failed: "
                    + "; ".join(problems)]
        if sha256_file(decision_path) != digest:
            return ["territory evidence digest does not match decision bytes"]
        decision = json.loads(decision_path.read_text(encoding="utf-8"))
    else:
        problems, decision = verifier.verify(decision_path, digest)
        if problems:
            return problems
    if decision.get("decision_state") != evidence.get("decision_state"):
        return ["territory evidence decision_state does not match decision bytes"]
    required_scope = ADMINISTRATION_SCOPE if require_administration else None
    if required_scope and decision.get("qualification_scope") != required_scope:
        return ["source-relative qualification cannot establish territory status"]
    if require_administration:
        return ["administrative territory qualification requires a separate reviewed evidence contract"]
    return []


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
    d.add_argument("--reviewer-role", required=True, choices=("owner",))
    d.add_argument("--rationale", required=True)
    d.add_argument("--scope-note", required=True,
                   choices=QUALIFICATION_SCOPES)
    d.add_argument("--out", required=True)
    v = sub.add_parser("verify")
    v.add_argument("--decision", required=True)
    args = ap.parse_args()
    if args.cmd == "assess":
        extra = {}
        for value in args.cross_check:
            if "=" not in value:
                print("ASSESSMENT_FAIL: cross-check must be variant=path")
                return 2
            variant, path = value.split("=", 1)
            if not variant or variant in extra:
                print("ASSESSMENT_FAIL: cross-check variant must be unique")
                return 2
            extra[variant] = path
        doc = assess(args.boundary_dir, args.territory_artifact, extra)
        write_once_json(args.out, doc)
        write_once_sidecar(args.out)
        print(json.dumps({"status": "ASSESSMENT_SEALED",
                          "problems": doc["problems"],
                          "recommendation": doc["recommendation"],
                          "qualification_capability": doc["qualification_capability"]},
                         indent=2))
        return 0 if not doc["problems"] else 1
    if args.cmd == "decide":
        problems = decide(args.assessment, args.state,
                          {"id": args.by, "role": args.reviewer_role},
                          args.rationale, args.out, args.scope_note)
        if problems:
            for problem in problems:
                print("DECISION_FAIL:", problem)
            return 1
        print("DECISION_SEALED:", args.out)
        return 0
    problems = verify_decision(args.decision)
    if problems:
        for problem in problems:
            print("DECISION_FAIL:", problem)
        return 1
    print("DECISION_VERIFY_OK:", args.decision)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
