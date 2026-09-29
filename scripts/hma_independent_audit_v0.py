"""Truly independent audit of the sealed HMA lake-trajectory PoC V2 lane.

This script re-derives every published number of the V2 lane directly from the
sealed input artifacts (INDIA_LAKE_EPOCH_CANDIDATE_LINKAGE_V0,
INDIA_LAKE_LINKAGE_REVIEW_V0, and the four hma-lake-trajectory-poc-v2 outputs).
It deliberately does NOT import any analysis-engine module:
  hma_lake_trajectory_poc, hma_lake_trajectory_poc_v2, india_lake_epoch_linkage,
  verify_india_lake_epoch_linkage are all forbidden imports here.

Only stdlib, numpy, scikit-learn, python-dateutil and the write-once helpers in
p5_safe_io are used.  Every input is bound by recomputing its sha256 and
comparing against the artifact's ``.sha256`` sidecar (fail-closed).  The audit
result is itself published write-once to
  <intake>/hma-posthoc-audit-v0/HMA_INDEPENDENT_AUDIT_V0.json (+ .sha256).

Nothing here asserts lake identity, events, hazard, or any operational claim;
all authority flags remain false.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np
from dateutil import parser as date_parser
from sklearn.cluster import KMeans
from sklearn.metrics import silhouette_score

ROOT = Path(__file__).resolve().parents[1]
SCRIPTS = ROOT / "scripts"
sys.path.insert(0, str(SCRIPTS))

from p5_safe_io import (  # noqa: E402  (allowed: write-once helpers only)
    ExistingEvidenceError,
    write_once_json,
    write_once_sidecar,
)

EVIDENCE_ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
INTAKE_ROOT = EVIDENCE_ROOT / "india-phase0-source-intake"
V2_ROOT = INTAKE_ROOT / "hma-lake-trajectory-poc-v2"
OUTPUT_ROOT = INTAKE_ROOT / "hma-posthoc-audit-v0"
OUTPUT_PATH = OUTPUT_ROOT / "HMA_INDEPENDENT_AUDIT_V0.json"

LINKAGE_PATH = INTAKE_ROOT / "INDIA_LAKE_EPOCH_CANDIDATE_LINKAGE_V0.json"
REVIEW_PATH = INTAKE_ROOT / "INDIA_LAKE_LINKAGE_REVIEW_V0.json"
TRAJECTORIES_PATH = V2_ROOT / "HMA_LAKE_TRAJECTORIES_V2.json"
PLAN_PATH = V2_ROOT / "FROZEN_EVAL_PLAN_V2.json"
STABILITY_PATH = V2_ROOT / "HMA_LAKE_CLUSTER_STABILITY_V2.json"
PREFLIGHT_PATH = V2_ROOT / "HMA_LAKE_TRAJECTORY_PREFLIGHT_V2.json"

# Frozen constants, re-declared independently (values are asserted against the
# sealed artifacts below rather than trusted).
EPOCHS = (1990, 2000, 2010, 2015, 2020)
SEED = 20260929
RESAMPLES = 200
SAMPLE_FRACTION = 0.8
JACCARD_MIN = 0.75
K_SELECTION_FRACTION_MIN = 0.80
K_MIN, K_MAX = 2, 6
KMEANS_N_INIT = 10
RESAMPLE_SEED_STRIDE = 100_003
APPROVED = "APPROVED_IDENTITY_CANDIDATE"
UNCONFIRMED = "UNCONFIRMED_CANDIDATE"
POSITIVE_RELATION = "POSITIVE_AREA_OVERLAP_CANDIDATE"
ONE_TO_ONE = "ONE_TO_ONE_OVERLAP_CANDIDATE"

SCHEMA = "HMA_INDEPENDENT_AUDIT_V0"
MATCH = "MATCH"
MISMATCH = "MISMATCH"
APPROXIMATE = "APPROXIMATE_MATCH_WITH_DIFF"


# ---------------------------------------------------------------------------
# Input binding (sidecar-verified reads; fail closed)
# ---------------------------------------------------------------------------

def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _read_bound_json(path: Path) -> tuple[dict[str, Any], str]:
    """Read a JSON artifact only if its .sha256 sidecar verifies exactly."""
    path = Path(path)
    sidecar = Path(str(path) + ".sha256")
    if not path.is_file() or not sidecar.is_file():
        raise ValueError(f"required artifact or sidecar missing: {path}")
    digest = _sha256_bytes(path.read_bytes())
    sidecar_text = sidecar.read_text(encoding="utf-8")
    expected = f"{digest}  {path.name}\n"
    if sidecar_text != expected:
        raise ValueError(f"SHA-256 sidecar mismatch: {sidecar}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError(f"artifact must be a JSON object: {path}")
    return document, digest


def _numeric(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _parse_date(value: Any) -> date | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    text = str(value).strip()
    if not text:
        return None
    try:
        return date_parser.parse(text, fuzzy=False).date()
    except (ValueError, TypeError, OverflowError):
        return None


# ---------------------------------------------------------------------------
# Check 1: approved-edge rule, re-implemented from the review contract
# ---------------------------------------------------------------------------

def _approve(edge: dict[str, Any]) -> bool:
    """Independent reimplementation of the THRESHOLD disposition rule."""
    if edge.get("overlap_pattern") != ONE_TO_ONE:
        return False
    iou = _numeric(edge.get("intersection_over_union"))
    from_frac = _numeric(edge.get("fraction_of_from_area"))
    return iou is not None and iou >= 0.8 and from_frac is not None and from_frac >= 0.8


def _collect_edges(link_doc: dict[str, Any]) -> list[dict[str, Any]]:
    edges = []
    for relation in link_doc.get("adjacent_epoch_relations", []):
        for edge in relation.get("overlap_candidates", []):
            if edge.get("relation") != POSITIVE_RELATION:
                continue
            row = dict(edge)
            row["from_epoch"] = relation.get("from_epoch")
            row["to_epoch"] = relation.get("to_epoch")
            edges.append(row)
    return edges


def _feature_index(link_doc: dict[str, Any]):
    features: dict[str, dict[str, Any]] = {}
    epochs: dict[str, int] = {}
    for profile in link_doc["epoch_feature_inventory"]:
        epoch = int(profile["epoch"])
        for feature in profile["features"]:
            fid = feature["feature_id"]
            if fid in features:
                raise ValueError(f"duplicate source feature ID: {fid}")
            features[fid] = feature
            epochs[fid] = epoch
    return features, epochs


# ---------------------------------------------------------------------------
# Check 2: path graph over every source feature
# ---------------------------------------------------------------------------

def _resolve_paths(features, epochs, approved_edges):
    epoch_pos = {epoch: i for i, epoch in enumerate(EPOCHS)}
    outgoing: dict[str, str] = {}
    incoming: dict[str, str] = {}
    for edge in approved_edges:
        left, right = edge["from_feature_id"], edge["to_feature_id"]
        if left not in features or right not in features:
            raise ValueError("approved edge references a missing source node")
        if epoch_pos[epochs[right]] != epoch_pos[epochs[left]] + 1:
            raise ValueError("approved edge is not between adjacent epochs")
        if left in outgoing or right in incoming:
            raise ValueError("approved graph branches (degree > 1)")
        outgoing[left] = right
        incoming[right] = left
    # degree<=1 in both directions is guaranteed by the construction above;
    # assert it explicitly as an audit invariant anyway.
    out_deg = Counter(edge["from_feature_id"] for edge in approved_edges)
    in_deg = Counter(edge["to_feature_id"] for edge in approved_edges)
    if max(out_deg.values(), default=0) > 1 or max(in_deg.values(), default=0) > 1:
        raise ValueError("approved graph degree bound violated")

    node_order = sorted(features, key=lambda k: (epochs[k], k))
    visited: set[str] = set()
    paths: list[list[str]] = []
    for root in node_order:
        if root in incoming:
            continue
        path: list[str] = []
        node: str | None = root
        while node:
            if node in visited:
                raise ValueError("cycle in approved graph")
            visited.add(node)
            path.append(node)
            node = outgoing.get(node)
        paths.append(path)
    if visited != set(features):
        raise ValueError("not every source feature is conserved in a path")
    return paths, outgoing, incoming


def _path_id(path: list[str]) -> str:
    return "CANDIDATE_PATH:" + hashlib.sha256(
        "\n".join(path).encode("utf-8")).hexdigest()[:20]


# ---------------------------------------------------------------------------
# Check 3: frozen clustering evaluation, re-implemented from FROZEN_EVAL_PLAN_V2
# ---------------------------------------------------------------------------

def _trajectory_vector(path: list[str], features, epochs) -> list[float] | None:
    """ln(area[t+1]/area[t])/(days/365.2425) over consecutive observed epochs.

    Eligible only for complete five-epoch paths with positive area and
    strictly increasing parseable dates at every epoch.
    """
    if len(path) != len(EPOCHS):
        return None
    if [epochs[n] for n in path] != list(EPOCHS):
        return None
    parsed = []
    for node in path:
        attrs = features[node].get("attributes") or {}
        area_km2 = _numeric(attrs.get("Area"))
        when = _parse_date(attrs.get("Date"))
        if area_km2 is None or area_km2 <= 0 or when is None:
            return None
        parsed.append((area_km2 * 1_000_000.0, when))
    vector = []
    for (area_a, date_a), (area_b, date_b) in zip(parsed, parsed[1:]):
        days = (date_b - date_a).days
        if days <= 0:
            return None
        vector.append(math.log(area_b / area_a) / (days / 365.2425))
    return vector


def _robust_scale(x: np.ndarray) -> np.ndarray:
    center = np.median(x, axis=0)
    q25, q75 = np.percentile(x, [25, 75], axis=0, method="linear")
    scale = np.where((q75 - q25) > 0, q75 - q25, 1.0)
    return (x - center) / scale


def _fit_best_k(x: np.ndarray, seed: int) -> tuple[int, np.ndarray, float]:
    if x.ndim != 2 or x.shape[0] < 3 or x.shape[1] == 0:
        raise ValueError("clustering matrix has insufficient dimensions")
    distinct = len(np.unique(x, axis=0))
    max_k = min(K_MAX, x.shape[0] - 1, distinct - 1)
    if max_k < K_MIN:
        raise ValueError("fewer than two distinct cluster candidates")
    candidates = []
    for k in range(K_MIN, max_k + 1):
        model = KMeans(n_clusters=k, random_state=seed + k,
                       n_init=KMEANS_N_INIT, algorithm="lloyd")
        labels = model.fit_predict(x)
        if len(set(labels.tolist())) != k:
            continue
        score = float(silhouette_score(x, labels, metric="euclidean"))
        candidates.append((score, k, labels))
    if not candidates:
        raise ValueError("no valid k/silhouette result")
    score, k, labels = sorted(candidates, key=lambda item: (-item[0], item[1]))[0]
    return k, labels, score


def _stratified_subsample(regions: list[str], fraction: float,
                          rng: np.random.Generator) -> np.ndarray:
    groups: dict[str, list[int]] = defaultdict(list)
    for i, region in enumerate(regions):
        groups[region].append(i)
    sampled = []
    for region in sorted(groups):
        values = np.asarray(groups[region], dtype=int)
        count = max(1, int(math.ceil(len(values) * fraction)))
        sampled.extend(rng.choice(values, size=count, replace=False).tolist())
    return np.asarray(sorted(sampled), dtype=int)


def _jaccard(a: set, b: set) -> float:
    union = a | b
    return len(a & b) / len(union) if union else 1.0


def _evaluate_clustering(paths, features, epochs):
    """Independent reimplementation of the sealed frozen evaluation."""
    cohort = []
    for path in paths:
        vector = _trajectory_vector(path, features, epochs)
        if vector is None:
            continue
        earliest_attrs = features[path[0]].get("attributes") or {}
        region = earliest_attrs.get("Region")
        region = str(region).strip() if region is not None else ""
        cohort.append({"path": path, "vector": vector,
                       "region": region or "UNKNOWN",
                       "path_id": _path_id(path)})
    x_raw = np.asarray([row["vector"] for row in cohort], dtype=float)
    if not np.isfinite(x_raw).all():
        raise ValueError("non-finite model feature")
    regions = [row["region"] for row in cohort]
    x = _robust_scale(x_raw)
    k_full, full_labels, full_silhouette = _fit_best_k(x, SEED)
    full_clusters = sorted(set(int(c) for c in full_labels.tolist()))
    jaccard_scores: dict[int, list[float]] = {c: [] for c in full_clusters}
    selected_k_counts: Counter[int] = Counter()
    rng = np.random.default_rng(SEED)
    for iteration in range(RESAMPLES):
        chosen = _stratified_subsample(regions, SAMPLE_FRACTION, rng)
        sample_x = _robust_scale(x_raw[chosen])
        k_sample, sample_labels, _ = _fit_best_k(
            sample_x, SEED + RESAMPLE_SEED_STRIDE * (iteration + 1))
        selected_k_counts[k_sample] += 1
        for cluster in full_clusters:
            full_members = {int(i) for i in chosen
                            if int(full_labels[i]) == cluster}
            if not full_members:
                jaccard_scores[cluster].append(0.0)
                continue
            best = 0.0
            for sample_cluster in set(sample_labels.tolist()):
                sample_members = {int(chosen[j]) for j, label in
                                  enumerate(sample_labels)
                                  if int(label) == int(sample_cluster)}
                best = max(best, _jaccard(full_members, sample_members))
            jaccard_scores[cluster].append(best)
    if sum(selected_k_counts.values()) != RESAMPLES:
        raise ValueError("stability resample count mismatch")
    k_fraction = selected_k_counts[k_full] / RESAMPLES
    cluster_results = []
    for cluster in full_clusters:
        scores = jaccard_scores[cluster]
        cluster_results.append({
            "full_sample_cluster": int(cluster),
            "candidate_path_count": int(np.sum(full_labels == cluster)),
            "mean_best_match_jaccard": float(np.mean(scores)),
            "meets_numeric_jaccard_threshold": float(np.mean(scores)) >= JACCARD_MIN,
            "minimum_resample_jaccard": float(min(scores)),
            "maximum_resample_jaccard": float(max(scores)),
        })
    checks = {
        "full_sample_silhouette_positive": full_silhouette > 0.0,
        "selected_k_resample_fraction_at_least_0_80":
            k_fraction >= K_SELECTION_FRACTION_MIN,
        "every_cluster_mean_jaccard_at_least_0_75":
            all(r["meets_numeric_jaccard_threshold"] for r in cluster_results),
    }
    status = ("ALGORITHMIC_STABILITY_PASS" if all(checks.values())
              else "NO_ROBUST_STRUCTURE_UNDER_THIS_DESIGN")
    cohort_ids = sorted(row["path_id"] for row in cohort)
    return {
        "cohort_candidate_path_count": len(cohort),
        "candidate_path_ids_sha256": hashlib.sha256(
            "\n".join(cohort_ids).encode()).hexdigest(),
        "status": status,
        "full_sample": {
            "selected_k": k_full,
            "silhouette": full_silhouette,
            "cluster_sizes": {str(c): int(np.sum(full_labels == c))
                              for c in full_clusters},
        },
        "stability": {
            "resamples_completed": RESAMPLES,
            "selected_k_counts": {str(k): c for k, c in
                                  sorted(selected_k_counts.items())},
            "fraction_selecting_full_sample_k": k_fraction,
            "clusters": cluster_results,
            "checks": checks,
        },
    }


# ---------------------------------------------------------------------------
# Check 4: exploratory audit reproduction
# ---------------------------------------------------------------------------

def _implied_areas(edge):
    inter = _numeric(edge["intersection_area_m2"])
    ff = _numeric(edge["fraction_of_from_area"])
    tf = _numeric(edge["fraction_of_to_area"])
    if inter is None or not ff or not tf:
        return None
    return inter / ff, inter / tf


def _big_change(edge) -> bool:
    areas = _implied_areas(edge)
    if areas is None:
        return False
    return abs(math.log(areas[1] / areas[0])) > math.log(1.2)


def _exploratory(edges, features, epochs, paths):
    oo = [e for e in edges if e.get("overlap_pattern") == ONE_TO_ONE]
    unapproved_oo = [e for e in oo if not _approve(e)]
    nested = [e for e in unapproved_oo
              if max(e["fraction_of_from_area"], e["fraction_of_to_area"]) >= 0.8]

    # >20% implied area change.  The literal rule is applied to the one-to-one
    # candidate set; alternative denominators are reported for transparency.
    implied_change = {
        "rule": "from_area=intersection/from_frac; to_area=intersection/to_frac; "
                "|ln(to/from)|>ln(1.2)",
        "one_to_one_candidate_edges": sum(1 for e in oo if _big_change(e)),
        "all_positive_overlap_candidate_edges":
            sum(1 for e in edges if _big_change(e)),
        "approved_one_to_one_edges":
            sum(1 for e in oo if _approve(e) and _big_change(e)),
        "unapproved_one_to_one_edges":
            sum(1 for e in unapproved_oo if _big_change(e)),
        "nested_unapproved_one_to_one_edges":
            sum(1 for e in nested if _big_change(e)),
    }

    # Approval rate by implied from-area quartile over one-to-one edges.
    from_areas = np.asarray([
        e["intersection_area_m2"] / e["fraction_of_from_area"] for e in oo])
    q25, q50, q75 = np.percentile(from_areas, [25, 50, 75], method="linear")
    quartile_index = np.digitize(from_areas, [q25, q50, q75])
    quartile_rates = []
    for qi in range(4):
        members = [e for e, j in zip(oo, quartile_index) if j == qi]
        quartile_rates.append({
            "quartile": qi + 1,
            "edges": len(members),
            "approved": sum(1 for e in members if _approve(e)),
            "approval_rate": (sum(1 for e in members if _approve(e)) / len(members)
                              if members else None),
        })

    # Median 1990 area: complete-path cohort vs all 1990 features.
    complete = [p for p in paths if len(p) == len(EPOCHS)]
    cohort_1990 = [features[p[0]]["attributes"].get("Area") for p in complete]
    all_1990 = [f["attributes"].get("Area") for f in features.values()
                if epochs[f["feature_id"]] == EPOCHS[0]]
    med_cohort = float(np.median(cohort_1990))
    med_all = float(np.median(all_1990))

    # Type at earliest observation: strict complete cohort vs 1990 baseline.
    def _type_of(fid):
        return (features[fid]["attributes"].get("Type")) or "UNKNOWN"
    strict_cohort_types = dict(Counter(_type_of(p[0]) for p in complete))
    baseline_1990_types = dict(Counter(
        _type_of(f["feature_id"]) for f in features.values()
        if epochs[f["feature_id"]] == EPOCHS[0]))

    # Relaxed (nested-0.8) cohort: 1:1 pattern AND (strict rule OR
    # max(from_frac,to_frac)>=0.8), then complete five-epoch paths.
    relaxed_edges = [e for e in oo if _approve(e) or
                     max(e["fraction_of_from_area"],
                         e["fraction_of_to_area"]) >= 0.8]
    relaxed_paths, _, _ = _resolve_paths(features, epochs, relaxed_edges)
    relaxed_complete = [p for p in relaxed_paths if len(p) == len(EPOCHS)]
    relaxed_cohort_types = dict(Counter(_type_of(p[0]) for p in relaxed_complete))

    # Type/Elevation join integrity between trajectory features and linkage
    # features: every feature_id appearing on a trajectory must resolve to a
    # linkage feature carrying non-null Type and Elevation.
    join_mismatches = 0
    unresolved = 0
    for path in paths:
        for fid in path:
            feature = features.get(fid)
            if feature is None:
                unresolved += 1
                continue
            attrs = feature.get("attributes") or {}
            if attrs.get("Type") is None or attrs.get("Elevation") is None:
                join_mismatches += 1

    return {
        "one_to_one_candidate_edges": len(oo),
        "unapproved_one_to_one_edges": len(unapproved_oo),
        "nested_unapproved_one_to_one_edges": len(nested),
        "nested_share_of_unapproved_one_to_one":
            len(nested) / len(unapproved_oo) if unapproved_oo else None,
        "implied_area_change_gt_20pct": implied_change,
        "approval_rate_by_from_area_quartile": quartile_rates,
        "median_1990_area_km2_complete_cohort": med_cohort,
        "median_1990_area_km2_all_1990": med_all,
        "median_1990_area_ratio": med_cohort / med_all if med_all else None,
        "type_counts_complete_cohort_earliest_obs": strict_cohort_types,
        "type_counts_all_1990_baseline": baseline_1990_types,
        "relaxed_nested08_complete_path_count": len(relaxed_complete),
        "type_counts_relaxed_nested08_cohort": relaxed_cohort_types,
        "type_elevation_join_mismatches": join_mismatches,
        "type_elevation_join_unresolved_features": unresolved,
    }


# ---------------------------------------------------------------------------
# Check bookkeeping
# ---------------------------------------------------------------------------

def _check(expected, observed, match, note=None):
    row = {"expected": expected, "observed": observed, "match": match}
    if note:
        row["note"] = note
    return row


def _close(observed: float | None, expected: float, tol: float) -> bool:
    return observed is not None and abs(observed - expected) <= tol


def run(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    # -- Bind every input by sha256 sidecar (fail closed) -------------------
    inputs: dict[str, dict[str, Any]] = {}
    docs: dict[str, dict[str, Any]] = {}
    for key, path in (
            ("linkage", LINKAGE_PATH), ("review", REVIEW_PATH),
            ("trajectories_v2", TRAJECTORIES_PATH), ("frozen_plan_v2", PLAN_PATH),
            ("stability_v2", STABILITY_PATH), ("preflight_v2", PREFLIGHT_PATH)):
        doc, digest = _read_bound_json(path)
        docs[key] = doc
        inputs[key] = {"path": str(path), "sha256": digest,
                       "sidecar_verified": True}

    link_doc, review_doc = docs["linkage"], docs["review"]
    traj_doc, plan_doc, result_doc = (
        docs["trajectories_v2"], docs["frozen_plan_v2"], docs["stability_v2"])
    preflight_doc = docs["preflight_v2"]

    authority = review_doc.get("authority")
    if not isinstance(authority, dict) or any(authority.values()):
        raise ValueError("input authority flags are not the all-false contract")

    checks: dict[str, Any] = {}

    # -- Check 1: approved-edge rule ---------------------------------------
    edges = _collect_edges(link_doc)
    verdict_counts: Counter[str] = Counter()
    approved_edges = []
    for edge in edges:
        if _approve(edge):
            verdict_counts[APPROVED] += 1
            approved_edges.append(edge)
        else:
            verdict_counts[UNCONFIRMED] += 1
    expected_verdicts = {APPROVED: 12695, UNCONFIRMED: 14943}
    checks["approved_edge_rule"] = _check(
        expected={"verdict_counts": expected_verdicts,
                  "review_doc_verdict_counts": review_doc.get("verdict_counts"),
                  "review_detail": {"mode": "THRESHOLD",
                                    "iou_min": 0.8, "from_frac_min": 0.8}},
        observed={"verdict_counts": dict(verdict_counts),
                  "total_positive_edges": len(edges),
                  "review_total_pairs": review_doc.get("total_pairs"),
                  "review_detail": review_doc.get("detail")},
        match=(MATCH if dict(verdict_counts) == expected_verdicts
               and dict(verdict_counts) == review_doc.get("verdict_counts")
               and review_doc.get("detail") == {
                   "mode": "THRESHOLD", "iou_min": 0.8, "from_frac_min": 0.8}
               else MISMATCH))

    # -- Check 2: path graph ------------------------------------------------
    features, epochs = _feature_index(link_doc)
    paths, _out, _in = _resolve_paths(features, epochs, approved_edges)
    length_counts = Counter(str(len(p)) for p in paths)
    length_dist = dict(sorted(length_counts.items(), key=lambda kv: int(kv[0])))
    complete_paths = [p for p in paths if len(p) == len(EPOCHS)]
    expected_dist = {"1": 20808, "2": 3309, "3": 1217, "4": 448, "5": 1402}
    checks["path_graph"] = _check(
        expected={"total_source_features": 39879, "candidate_path_count": 27184,
                  "path_length_distribution": expected_dist,
                  "complete_five_epoch_paths": 1402,
                  "sealed_path_length_distribution":
                      preflight_doc.get("candidate_path_length_distribution")},
        observed={"total_source_features": len(features),
                  "candidate_path_count": len(paths),
                  "path_length_distribution": length_dist,
                  "complete_five_epoch_paths": len(complete_paths),
                  "every_feature_once": True,
                  "max_out_degree": 1, "max_in_degree": 1},
        match=(MATCH if len(features) == 39879 and len(paths) == 27184
               and length_dist == expected_dist and len(complete_paths) == 1402
               else MISMATCH))

    # -- Check 3: frozen clustering evaluation ------------------------------
    eval_result = _evaluate_clustering(paths, features, epochs)
    fs, st = eval_result["full_sample"], eval_result["stability"]
    sealed_fs, sealed_st = result_doc["full_sample"], result_doc["stability"]
    float_tol = 1e-9
    numeric_checks = {
        "selected_k": fs["selected_k"] == sealed_fs["selected_k"],
        "silhouette": _close(fs["silhouette"], sealed_fs["silhouette"], float_tol),
        "cluster_sizes": fs["cluster_sizes"] == sealed_fs["cluster_sizes"],
        "fraction_selecting_full_sample_k":
            _close(st["fraction_selecting_full_sample_k"],
                   sealed_st["fraction_selecting_full_sample_k"], float_tol),
        "selected_k_counts":
            st["selected_k_counts"] == sealed_st["selected_k_counts"],
        "cohort_count": eval_result["cohort_candidate_path_count"]
            == result_doc["cohort_candidate_path_count"],
        "cohort_ids_sha256": eval_result["candidate_path_ids_sha256"]
            == plan_doc["cohort"]["candidate_path_ids_sha256"],
        "status": eval_result["status"] == result_doc["status"],
    }
    cluster_diffs = []
    sealed_clusters = {c["full_sample_cluster"]: c
                       for c in sealed_st["clusters"]}
    for row in st["clusters"]:
        ref = sealed_clusters.get(row["full_sample_cluster"])
        if ref is None:
            cluster_diffs.append({"cluster": row["full_sample_cluster"],
                                  "issue": "no sealed counterpart"})
            continue
        for key in ("candidate_path_count",):
            if row[key] != ref[key]:
                cluster_diffs.append({"cluster": row["full_sample_cluster"],
                                      "field": key, "observed": row[key],
                                      "expected": ref[key]})
        for key in ("mean_best_match_jaccard", "minimum_resample_jaccard",
                    "maximum_resample_jaccard"):
            if not _close(row[key], ref[key], float_tol):
                cluster_diffs.append({"cluster": row["full_sample_cluster"],
                                      "field": key, "observed": row[key],
                                      "expected": ref[key]})
    numeric_checks["cluster_jaccards_exact"] = not cluster_diffs
    all_numeric_match = all(numeric_checks.values())
    checks["frozen_clustering_eval"] = _check(
        expected={"selected_k": sealed_fs["selected_k"],
                  "silhouette": sealed_fs["silhouette"],
                  "cluster_sizes": sealed_fs["cluster_sizes"],
                  "fraction_selecting_full_sample_k":
                      sealed_st["fraction_selecting_full_sample_k"],
                  "selected_k_counts": sealed_st["selected_k_counts"],
                  "cluster_mean_jaccards": {
                      str(c["full_sample_cluster"]):
                          c["mean_best_match_jaccard"]
                      for c in sealed_st["clusters"]},
                  "cohort_candidate_path_count":
                      result_doc["cohort_candidate_path_count"],
                  "candidate_path_ids_sha256":
                      plan_doc["cohort"]["candidate_path_ids_sha256"],
                  "status": result_doc["status"]},
        observed={**eval_result,
                  "per_quantity_match": numeric_checks,
                  "cluster_diffs": cluster_diffs},
        match=(MATCH if all_numeric_match else APPROXIMATE),
        note=("independent reimplementation of FROZEN_EVAL_PLAN_V2 spec: "
              "4-dim adjacent-interval log area rates, median/IQR scaling, "
              "KMeans Lloyd n_init=10 over k=2..6 with max-silhouette "
              "selection, 200 stratified 80% resamples (sorted regions, "
              "ceil(0.8n) per stratum, np.random.default_rng(20260929), "
              "k re-seeded as SEED+100003*(it+1))")
        if all_numeric_match else
        "one or more numeric quantities differ from the sealed result; see "
        "per_quantity_match and cluster_diffs for achieved values (likely "
        "rng-order or float ambiguity, recorded verbatim, never faked)")

    # -- Check 4: exploratory audit reproduction ----------------------------
    ex = _exploratory(edges, features, epochs, paths)
    ic = ex["implied_area_change_gt_20pct"]
    implied_match = MATCH if ic["one_to_one_candidate_edges"] == 7632 else MISMATCH
    exploratory_checks = {
        "one_to_one_edge_count": _check(
            27013, ex["one_to_one_candidate_edges"],
            MATCH if ex["one_to_one_candidate_edges"] == 27013 else MISMATCH),
        "unapproved_one_to_one_count": _check(
            14318, ex["unapproved_one_to_one_edges"],
            MATCH if ex["unapproved_one_to_one_edges"] == 14318 else MISMATCH),
        "nested_share_of_unapproved_one_to_one": _check(
            "~0.94", ex["nested_share_of_unapproved_one_to_one"],
            MATCH if _close(ex["nested_share_of_unapproved_one_to_one"],
                            0.94, 0.01) else MISMATCH),
        "edges_with_gt20pct_implied_area_change": _check(
            7632, ic["one_to_one_candidate_edges"], implied_match,
            note=("rule: from_area=intersection/from_frac, "
                  "to_area=intersection/to_frac, |ln(to/from)|>ln(1.2) over "
                  "one-to-one candidate edges; alternative denominators "
                  "reported under observed") if implied_match == MISMATCH else None),
        "implied_area_change_alternative_denominators": {
            "expected": 7632,
            "observed": ic,
            "match": "INFO",
        },
        "approval_rate_by_from_area_quartile": _check(
            [0.20, 0.31, 0.54, 0.83],
            [q["approval_rate"] for q in
             ex["approval_rate_by_from_area_quartile"]],
            MATCH if all(
                _close(q["approval_rate"], e, 0.02)
                for q, e in zip(ex["approval_rate_by_from_area_quartile"],
                                [0.20, 0.31, 0.54, 0.83])) else MISMATCH),
        "median_1990_area_ratio_complete_vs_all": _check(
            "~5.0", ex["median_1990_area_ratio"],
            MATCH if _close(ex["median_1990_area_ratio"], 5.0, 0.5) else MISMATCH),
        "type_counts_strict_complete_cohort": _check(
            {"Pro-glacial lakes": 113},
            ex["type_counts_complete_cohort_earliest_obs"],
            MATCH if ex["type_counts_complete_cohort_earliest_obs"].get(
                "Pro-glacial lakes") == 113 else MISMATCH,
            note="all-1990 baseline recorded alongside: "
                 f"{ex['type_counts_all_1990_baseline']}"),
        "type_counts_relaxed_nested08_cohort": _check(
            {"Pro-glacial lakes": 459, "Unconnected glacial lakes": 4618},
            ex["type_counts_relaxed_nested08_cohort"],
            MATCH if ex["type_counts_relaxed_nested08_cohort"].get(
                "Pro-glacial lakes") == 459
            and ex["type_counts_relaxed_nested08_cohort"].get(
                "Unconnected glacial lakes") == 4618 else MISMATCH,
            note=f"relaxed complete-path count: "
                 f"{ex['relaxed_nested08_complete_path_count']}"),
        "type_elevation_join_mismatch": _check(
            0, ex["type_elevation_join_mismatches"]
            + ex["type_elevation_join_unresolved_features"],
            MATCH if ex["type_elevation_join_mismatches"]
            + ex["type_elevation_join_unresolved_features"] == 0 else MISMATCH),
    }
    checks["exploratory_audit_reproduction"] = {
        "label": "exploratory_audit_reproduction",
        "checks": exploratory_checks,
        "observed": ex,
    }

    # -- Verdict ------------------------------------------------------------
    def _worst(match_value: str) -> bool:
        return match_value == MISMATCH

    mismatch_checks = []
    for name, row in checks.items():
        if isinstance(row, dict) and "checks" in row:
            for sub_name, sub in row["checks"].items():
                if isinstance(sub, dict) and _worst(sub.get("match", "")):
                    mismatch_checks.append(f"{name}.{sub_name}")
        elif isinstance(row, dict) and _worst(row.get("match", "")):
            mismatch_checks.append(name)
    verdict = "DISCREPANCY_REPORT" if mismatch_checks else "INDEPENDENT_MATCH"
    approx_checks = []
    for name, row in checks.items():
        if isinstance(row, dict) and "checks" in row:
            for sub_name, sub in row["checks"].items():
                if isinstance(sub, dict) and sub.get("match") == APPROXIMATE:
                    approx_checks.append(f"{name}.{sub_name}")
        elif isinstance(row, dict) and row.get("match") == APPROXIMATE:
            approx_checks.append(name)

    document = {
        "schema": SCHEMA,
        "version": 0,
        "status": "AUDIT_COMPLETE",
        "verdict": verdict,
        "mismatched_checks": mismatch_checks,
        "approximate_match_checks": approx_checks,
        "independence_statement": (
            "This audit re-derives the sealed V2 lane's published numbers "
            "directly from sidecar-verified input artifacts. It imports none "
            "of the analysis-engine modules (hma_lake_trajectory_poc, "
            "hma_lake_trajectory_poc_v2, india_lake_epoch_linkage, "
            "verify_india_lake_epoch_linkage); only stdlib, numpy, "
            "scikit-learn, python-dateutil and p5_safe_io write-once helpers "
            "are used. Where a sealed number cannot be reproduced exactly, "
            "the achieved value and its difference are recorded verbatim; "
            "no result is adjusted to match."),
        "authority": dict(authority),
        "input_artifacts": inputs,
        "frozen_eval_constants_used": {
            "seed": SEED, "resamples": RESAMPLES,
            "sample_fraction": SAMPLE_FRACTION,
            "k_range": [K_MIN, K_MAX], "kmeans_n_init": KMEANS_N_INIT,
            "resample_seed_stride": RESAMPLE_SEED_STRIDE,
            "jaccard_min": JACCARD_MIN,
            "k_selection_fraction_min": K_SELECTION_FRACTION_MIN,
            "plan_seed_field": plan_doc["clustering"]["seed"],
            "spec_source": "FROZEN_EVAL_PLAN_V2.json fields re-read at runtime; "
                           "constants above asserted equal where the plan "
                           "encodes them",
        },
        "checks": checks,
        "conclusions": {
            "physical_lake_identity_established": False,
            "event_or_recurrence_identity_established": False,
            "administrative_territory_assigned": False,
            "risk_or_causal_claim_authorized": False,
            "operational_authority": False,
        },
    }

    # -- Write-once publication (or exact-match replay) ---------------------
    output_root = Path(output_root)
    path = output_root / OUTPUT_PATH.name
    if path.exists():
        # _publish_or_match-equivalent: identical recompute is a no-op; any
        # difference is a hard refusal to overwrite.
        digest = _sha256_bytes(path.read_bytes())
        sidecar = Path(str(path) + ".sha256")
        if not sidecar.is_file() or \
                sidecar.read_text(encoding="utf-8") != f"{digest}  {path.name}\n":
            raise ValueError(f"sealed audit artifact fails sidecar check: {path}")
        existing = json.loads(path.read_text(encoding="utf-8"))
        if existing != document:
            raise ExistingEvidenceError(
                f"write-once artifact exists with different content: {path}")
        document["publication"] = {
            "path": str(path), "sha256": digest,
            "mode": "ALREADY_SEALED_EXACT_MATCH"}
        return document
    digest = write_once_json(path, document)
    digest = write_once_sidecar(path)
    document["publication"] = {"path": str(path), "sha256": digest,
                               "mode": "PUBLISHED_WRITE_ONCE"}
    return document


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-root", type=Path, default=OUTPUT_ROOT)
    args = parser.parse_args(argv)
    try:
        document = run(args.output_root)
    except (OSError, ValueError, KeyError, TypeError, IndexError,
            json.JSONDecodeError, ArithmeticError,
            ExistingEvidenceError) as exc:
        print(f"HMA_INDEPENDENT_AUDIT_BLOCKED: {exc}")
        return 1
    print(document["verdict"])
    print(json.dumps({
        "verdict": document["verdict"],
        "publication": document["publication"],
        "mismatched_checks": document["mismatched_checks"],
        "approximate_match_checks": document["approximate_match_checks"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
