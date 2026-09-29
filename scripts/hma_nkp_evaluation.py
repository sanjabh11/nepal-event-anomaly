"""NKP: known-groups positive-control + persistence evaluation (frozen plan).

Sequence, fixed before computation and sealed in FROZEN_NKP_PLAN_V0:
  H1 instrument recoverability — does the nested-candidate cohort recover
     the literature-known contrast that pro-glacial lakes grow faster than
     unconnected lakes (stratified AUC, grid-cell block bootstrap, placebo)?
  H2 within-lake persistence — are non-overlapping interval growth rates
     positively rank-correlated (shared-endpoint noise avoided)?
  H3 (only if H1 AND H2 pass) — structure beyond a copula null: the
     production gate is not specific (GATE_NOT_SPECIFIC_UNDER_DECLARED_NULLS),
     so H3 compares observed stability stats against the GCAL null envelope.

This is an instrument/lane validation study.  It makes no event, territory,
causal, hazard, forecast, or operational claim; candidate paths are not
confirmed lake identities.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from pathlib import Path
from typing import Any

import numpy as np
from threadpoolctl import threadpool_limits

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import hma_lake_trajectory_poc as v1  # noqa: E402
import india_lake_epoch_linkage as linkage  # noqa: E402
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402

E = v1.INTAKE_ROOT
V3_ROOT = E / "hma-lake-trajectory-poc-v3"
TRAJ_V3 = V3_ROOT / "HMA_LAKE_TRAJECTORIES_V3.json"
REVIEW_V1 = E / "INDIA_LAKE_LINKAGE_REVIEW_V1.json"
GCAL_ROOT = E / "hma-gate-calibration-v0"
GCAL_RESULT = GCAL_ROOT / "HMA_GATE_CALIBRATION_V0.json"
GCAL_COPULA = GCAL_ROOT / "GCAL_PARTIAL_N1_GAUSSIAN_COPULA.json"
SHP_1990 = (E / "observation-inventory-figshare" / "Glacial_Lake_Inventory"
            / "GlacialLake_20220726" / "GlacialLake_1990.shp")
SCHEMA_PLAN = "FROZEN_NKP_PLAN_V0"
SCHEMA_RESULT = "HMA_NKP_RESULT_V0"
SEED = 27182818
BOOTSTRAPS = 2000
PLACEBOS = 200
CELL_M = 100_000.0          # 100 km block-bootstrap cells (Albers metres)
MIN_PATHS = 4000
MIN_PROGLACIAL = 300
AUC_FLOOR = 0.55
RHO_FLOOR = 0.10
PLACEBO_CENTER_TOL = 0.02
INTERVAL_PAIRS = ((0, 2), (0, 3), (1, 3))   # non-overlapping epoch pairs


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _bound_json(path: Path) -> dict[str, Any]:
    sidecar = Path(f"{path}.sha256")
    expected = f"{_sha256(path)}  {path.name}\n"
    if not sidecar.is_file() or sidecar.read_text() != expected:
        raise ValueError(f"sidecar verification failed: {path}")
    return json.loads(path.read_text())


def _publish_or_match(path: Path, document: dict[str, Any]) -> str:
    if path.exists():
        digest = _sha256(path)
        if Path(f"{path}.sha256").read_text() != f"{digest}  {path.name}\n":
            raise ValueError(f"sidecar verification failed: {path}")
        if json.loads(path.read_text()) != document:
            raise FileExistsError(
                f"write-once artifact exists with different content: {path}")
        return digest
    write_once_json(path, document)
    return write_once_sidecar(path)


def _cohort() -> list[dict[str, Any]]:
    doc = _bound_json(TRAJ_V3)
    if doc.get("schema") != "HMA_LAKE_TRAJECTORIES_V3":
        raise ValueError("expected HMA_LAKE_TRAJECTORIES_V3")
    rows = []
    for row in doc["trajectories"]:
        if not row.get("all_five_epochs_present"):
            continue
        vec = v1._trajectory_vector(row)
        if vec is None:
            continue
        cov = row.get("earliest_observation_covariates") or {}
        rows.append({
            "id": row["candidate_path_id"],
            "rates": vec,
            "mean_rate": float(np.mean(vec)),
            "type": str(cov.get("type_source") or "UNKNOWN"),
            "region": str(cov.get("region_source") or "UNKNOWN"),
            "elevation": cov.get("elevation_m"),
            "area_m2_1990": next(
                (o.get("area_m2") for o in row["observations"]
                 if o["epoch"] == 1990), None),
            "root_feature_id": row["source_feature_ids"][0],
        })
    return rows


def _centroids_1990() -> dict[str, tuple[float, float]]:
    """100 km bootstrap cells need coordinates; read the 1990 shapefile once
    via the linkage loader (same CRS/count/pinned-hash guards).  The member
    digests are bound in the sealed linkage artifact's source bundle."""
    link_doc = _bound_json(v1.LINKAGE_PATH)
    member_hashes = link_doc["inputs"]["figshare"]["member_sha256"]
    features, _, _ = linkage.load_epoch_features(
        v1.EVIDENCE_ROOT, 1990, member_hashes)
    return {f.feature_id: (f.geometry.centroid.x, f.geometry.centroid.y)
            for f in features}


def _strata(rows: list[dict[str, Any]]) -> list[str]:
    areas = np.array([r["area_m2_1990"] for r in rows], float)
    elevs = np.array([r["elevation"] if r["elevation"] is not None else np.nan
                      for r in rows], float)
    aq = np.quantile(areas, [0.25, 0.5, 0.75])
    eq = np.nanquantile(elevs, [1 / 3, 2 / 3])
    out = []
    for r in rows:
        sq = int(np.searchsorted(aq, r["area_m2_1990"], side="right"))
        el = r["elevation"]
        et = -1 if el is None else int(np.searchsorted(eq, el, side="right"))
        out.append(f"{r['region']}|A{sq}|E{et}")
    return out


def _cells(rows: list[dict[str, Any]],
           cents: dict[str, tuple[float, float]]) -> list[str]:
    cells = []
    for r in rows:
        c = cents.get(r["root_feature_id"])
        if c is None:
            cells.append("NO_GEOMETRY")
        else:
            cells.append(f"{int(c[0] // CELL_M)}:{int(c[1] // CELL_M)}")
    return cells


def _auc(pos: np.ndarray, neg: np.ndarray) -> float:
    """P(pos > neg) via ranks; ties 0.5."""
    n1, n0 = len(pos), len(neg)
    if n1 == 0 or n0 == 0:
        return float("nan")
    from scipy.stats import rankdata
    ranks = rankdata(np.concatenate([pos, neg]))
    return float((ranks[:n1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def _stratified_auc(rows, strata, idx=None) -> float:
    idx = np.arange(len(rows)) if idx is None else np.asarray(idx)
    num = den = 0.0
    by_stratum: dict[str, list[int]] = {}
    for i in idx:
        by_stratum.setdefault(strata[i], []).append(i)
    for members in by_stratum.values():
        pos = np.array([rows[i]["mean_rate"] for i in members
                        if rows[i]["type"] == "Pro-glacial lakes"])
        neg = np.array([rows[i]["mean_rate"] for i in members
                        if rows[i]["type"] == "Unconnected glacial lakes"])
        if len(pos) < 2 or len(neg) < 2:
            continue
        w = min(len(pos), len(neg))
        num += w * _auc(pos, neg)
        den += w
    return num / den if den else float("nan")


def _block_bootstrap_auc(rows, strata, cells, rng) -> np.ndarray:
    uniq = sorted(set(cells))
    idx = np.arange(len(rows))
    cell_of = {c: np.array([i for i in idx if cells[i] == c]) for c in uniq}
    stats = []
    for _ in range(BOOTSTRAPS):
        draw = rng.choice(len(uniq), size=len(uniq), replace=True)
        sample = np.concatenate([cell_of[uniq[d]] for d in draw])
        stats.append(_stratified_auc(rows, strata, sample))
    return np.asarray(stats)


def _spearman(a, b) -> float:
    from scipy.stats import spearmanr
    r = spearmanr(a, b)
    return float(r.statistic if hasattr(r, "statistic") else r[0])


def freeze_plan() -> dict[str, Any]:
    rows = _cohort()
    pro = sum(1 for r in rows if r["type"] == "Pro-glacial lakes")
    plan = {
        "schema": SCHEMA_PLAN, "version": 0,
        "status": "FROZEN_BEFORE_NKP_COMPUTATION",
        "purpose": ("Instrument recoverability (H1) then within-lake "
                    "persistence (H2) then, only if both pass, structure "
                    "beyond a calibrated copula-null envelope (H3). "
                    "Not a GLOF or event claim."),
        "inputs": {
            "trajectories_v3_sha256": _sha256(TRAJ_V3),
            "linkage_review_v1_sha256": _sha256(REVIEW_V1),
            "gcal_result_sha256": _sha256(GCAL_RESULT),
            "gcal_copula_null_sha256": _sha256(GCAL_COPULA),
        },
        "cohort": {
            "definition": ("complete five-epoch candidate paths with "
                           "positive finite area + strictly increasing "
                           "actual acquisition dates at every epoch"),
            "candidate_path_count": len(rows),
            "pro_glacial_count": pro,
            "minimum_complete_paths": MIN_PATHS,
            "minimum_pro_glacial": MIN_PROGLACIAL,
            "underpowered_stop": ("cohort or pro-glacial count below "
                                  "minimums => UNDERPOWERED_STOP, no tests"),
        },
        "h1": {
            "question": ("can the measurement/linkage system recover the "
                         "known contrast pro-glacial > unconnected growth"),
            "response": "mean annualized ln area growth over the 4 adjacent"
                        " intervals (same definition as V2 features)",
            "groups": {"pos": "Pro-glacial lakes",
                       "neg": "Unconnected glacial lakes",
                       "excluded": "all other Type values"},
            "strata": "region(earliest) x 1990-area quartile x elevation tercile",
            "statistic": ("min(n)-weighted mean of within-stratum AUC over "
                          "strata with >=2 of each class"),
            "bootstrap": f"{BOOTSTRAPS} resamples of {CELL_M/1000:.0f} km "
                         "grid cells (Albers metres) drawn with replacement",
            "pass_rule": f"bootstrap 95% CI lower bound > {AUC_FLOOR}",
            "placebo": (f"{PLACEBOS} label shuffles within strata; placebo "
                        f"median AUC must lie in 0.5 +/- {PLACEBO_CENTER_TOL} "
                        "and observed AUC must exceed placebo 95th pct"),
        },
        "h2": {
            "question": ("non-overlapping interval growth rates positively "
                         "rank-correlated (persistence of the lake signal)"),
            "pairs": ["I1990-2000 x I2010-2015", "I1990-2000 x I2015-2020",
                      "I2000-2010 x I2015-2020"],
            "statistic": "Spearman rho pooled over complete paths",
            "bootstrap": "same grid-cell block bootstrap, 95% CI per pair",
            "pass_rule": f"rho >= {RHO_FLOOR} and CI excludes 0 in >=2 of "
                         "3 pairs",
        },
        "h3": {
            "gated_on": "H1 and H2 both pass",
            "question": ("stability statistics exceed the GCAL copula-null "
                         "envelope (calibrated comparison, not the raw "
                         "gate)"),
            "procedure": ("run the frozen production gate on the V3 "
                          "eligible cohort; compare frac_k and min cluster "
                          "mean Jaccard against N1_GAUSSIAN_COPULA "
                          "replicate distributions; pass only if both "
                          "exceed the null 95th percentile"),
            "note": "raw-gate pass alone is void after GCAL",
        },
        "kill_rules": [
            "H1 fail => NO_RECOVERABLE_CONTRAST; close NKP",
            "H1 pass + H2 fail => CONTRAST_ONLY; no clustering claim",
            "placebo off-centre => INSTRUMENT_INVALID; audit before retry",
            "any structure claim requires H3's calibrated envelope, not "
            "the raw frozen gate",
        ],
        "no_post_hoc_changes": True,
        "seed": SEED,
        "authority": dict(linkage.AUTHORITY_FLAGS),
        "event_association_branch": "DORMANT",
    }
    v1._false_authority(plan, "nkp plan")
    out = V3_ROOT / f"{SCHEMA_PLAN}.json"
    sha = _publish_or_match(out, plan)
    return {"status": "FROZEN_NKP_PLAN_SEALED", "sha256": sha,
            "cohort": len(rows), "pro_glacial": pro}


def _evaluate_h1(rows, strata, cells, rng) -> dict[str, Any]:
    auc = _stratified_auc(rows, strata)
    boots = _block_bootstrap_auc(rows, strata, cells, rng)
    lo, hi = np.nanpercentile(boots, [2.5, 97.5])
    placebo_aucs = []
    for _ in range(PLACEBOS):
        perm = list(range(len(rows)))
        # shuffle type labels within strata
        by_s: dict[str, list[int]] = {}
        for i in perm:
            by_s.setdefault(strata[i], []).append(i)
        shuffled = [r["type"] for r in rows]
        for members in by_s.values():
            types = [rows[i]["type"] for i in members]
            rng.shuffle(types)
            for i, t in zip(members, types):
                shuffled[i] = t
        fake = [dict(r, type=shuffled[i]) for i, r in enumerate(rows)]
        placebo_aucs.append(_stratified_auc(fake, strata))
    placebo_aucs = np.asarray(placebo_aucs)
    return {
        "auc": auc,
        "bootstrap_ci_95": [float(lo), float(hi)],
        "placebo_median": float(np.nanmedian(placebo_aucs)),
        "placebo_p95": float(np.nanpercentile(placebo_aucs, 95)),
        "passed": bool(lo > AUC_FLOOR
                       and abs(np.nanmedian(placebo_aucs) - 0.5)
                       <= PLACEBO_CENTER_TOL
                       and auc > np.nanpercentile(placebo_aucs, 95)),
    }


def _evaluate_h2(rows, cells, rng) -> dict[str, Any]:
    rates = np.array([r["rates"] for r in rows])
    pairs = {}
    uniq = sorted(set(cells))
    cell_of = {c: np.array([i for i, c2 in enumerate(cells) if c2 == c])
               for c in uniq}
    for (i, j) in INTERVAL_PAIRS:
        rho = _spearman(rates[:, i], rates[:, j])
        boot = []
        for _ in range(BOOTSTRAPS):
            draw = rng.choice(len(uniq), size=len(uniq), replace=True)
            sample = np.concatenate([cell_of[uniq[d]] for d in draw])
            boot.append(_spearman(rates[sample, i], rates[sample, j]))
        lo, hi = np.nanpercentile(boot, [2.5, 97.5])
        pairs[f"I{i}xI{j}"] = {"rho": rho,
                              "bootstrap_ci_95": [float(lo), float(hi)]}
    passes = sum(1 for v in pairs.values()
                 if v["rho"] >= RHO_FLOOR and v["bootstrap_ci_95"][0] > 0)
    return {"pairs": pairs, "pairs_passing": passes, "passed": passes >= 2}


def _evaluate_h3(cohort_doc, null_reps) -> dict[str, Any]:
    stub = {"trajectory_artifact": {"sha256": _sha256(TRAJ_V3)},
            "stability_design": _bound_json(
                E / "hma-lake-trajectory-poc-v2"
                / "FROZEN_EVAL_PLAN_V2.json")["stability_design"]}
    with threadpool_limits(limits=1):
        res = v1.evaluate(cohort_doc, stub)
    null_frac = np.array([r["fraction_selecting_full_sample_k"]
                          for r in null_reps])
    null_jac = np.array([r["min_cluster_mean_jaccard"] for r in null_reps])
    obs_frac = res["stability"]["fraction_selecting_full_sample_k"]
    obs_jac = min(c["mean_best_match_jaccard"]
                  for c in res["stability"]["clusters"])
    q95f, q95j = (float(np.quantile(null_frac, 0.95)),
                  float(np.quantile(null_jac, 0.95)))
    return {
        "raw_gate_status": res["status"],
        "observed_frac_k": obs_frac, "null_frac_k_q95": q95f,
        "observed_min_jaccard": obs_jac, "null_min_jaccard_q95": q95j,
        "passed": bool(obs_frac > q95f and obs_jac > q95j),
    }


def run() -> dict[str, Any]:
    plan = _bound_json(V3_ROOT / f"{SCHEMA_PLAN}.json")
    rows = _cohort()
    rng = np.random.default_rng(SEED)
    result: dict[str, Any] = {
        "schema": SCHEMA_RESULT, "version": 0,
        "frozen_plan_sha256": _sha256(V3_ROOT / f"{SCHEMA_PLAN}.json"),
        "authority": dict(linkage.AUTHORITY_FLAGS),
        "event_association_branch": "DORMANT",
        "candidate_paths_are_not_confirmed_lakes": True,
    }
    pro = sum(1 for r in rows if r["type"] == "Pro-glacial lakes")
    result["cohort"] = {"complete_paths": len(rows),
                        "pro_glacial": pro,
                        "unconnected": sum(
                            1 for r in rows
                            if r["type"] == "Unconnected glacial lakes")}
    if len(rows) < MIN_PATHS or pro < MIN_PROGLACIAL:
        result.update({"status": "UNDERPOWERED_STOP",
                       "h1": None, "h2": None, "h3": "NOT_RUN"})
    else:
        cents = _centroids_1990()
        cells = _cells(rows, cents)
        strata = _strata(rows)
        h1 = _evaluate_h1(rows, strata, cells, rng)
        result["h1"] = h1
        if not h1["passed"]:
            result.update({"status": "NO_RECOVERABLE_CONTRAST",
                           "h2": "NOT_RUN_H1_FAILED", "h3": "NOT_RUN"})
        else:
            h2 = _evaluate_h2(rows, cells, rng)
            result["h2"] = h2
            if not h2["passed"]:
                result.update({"status": "CONTRAST_ONLY_NO_PERSISTENCE",
                               "h3": "NOT_RUN"})
            else:
                doc = _bound_json(TRAJ_V3)
                null = _bound_json(GCAL_COPULA)["replicates"]
                result["h3"] = _evaluate_h3(doc, null)
                result["status"] = (
                    "STRUCTURE_BEYOND_COPULA_NULL"
                    if result["h3"]["passed"]
                    else "CONTRAST_AND_PERSISTENCE_NO_PARTITION_STRUCTURE")
    out = V3_ROOT / f"{SCHEMA_RESULT}.json"
    sha = _publish_or_match(out, result)
    return {"status": "NKP_RESULT_SEALED", "verdict": result["status"],
            "sha256": sha}


_H3_STATE = {
    "UNDERPOWERED_STOP": "NOT_RUN",
    "NO_RECOVERABLE_CONTRAST": "NOT_RUN",
    "CONTRAST_ONLY_NO_PERSISTENCE": "NOT_RUN",
    "CONTRAST_AND_PERSISTENCE_NO_PARTITION_STRUCTURE": "RAN",
    "STRUCTURE_BEYOND_COPULA_NULL": "RAN",
}


def _check_sealed_metadata(sealed: dict[str, Any],
                           cohort_counts: dict[str, int],
                           plan_sha256: str) -> list[str]:
    """Pure provenance/metadata validation — tamper-probeable."""
    problems: list[str] = []
    if sealed.get("schema") != SCHEMA_RESULT:
        problems.append("result schema mismatch")
    if sealed.get("frozen_plan_sha256") != plan_sha256:
        problems.append("result does not bind current frozen plan bytes")
    if sealed.get("authority") != dict(linkage.AUTHORITY_FLAGS):
        problems.append("authority flags not all false")
    if sealed.get("event_association_branch") != "DORMANT":
        problems.append("event_association_branch must be DORMANT")
    if sealed.get("candidate_paths_are_not_confirmed_lakes") is not True:
        problems.append("candidate-semantics disclaimer missing")
    cohort = sealed.get("cohort", {})
    for key in ("complete_paths", "pro_glacial", "unconnected"):
        if cohort.get(key) != cohort_counts.get(key):
            problems.append(f"cohort.{key} differs from recompute")
    status = sealed.get("status")
    h3 = sealed.get("h3")
    if status not in _H3_STATE:
        problems.append(f"unknown sealed status {status!r}")
        return problems
    if (_H3_STATE[status] == "NOT_RUN") != (h3 == "NOT_RUN"):
        problems.append("H3 state inconsistent with status")
    h1 = sealed.get("h1")
    if status == "UNDERPOWERED_STOP":
        if h1 is not None or sealed.get("h2") != "NOT_RUN":
            problems.append("underpowered stop must carry h1=null,h2=NOT_RUN")
    elif not isinstance(h1, dict):
        problems.append("h1 block missing for non-stop status")
    elif status == "NO_RECOVERABLE_CONTRAST" and h1.get("passed") is not False:
        problems.append("NO_RECOVERABLE_CONTRAST requires h1.passed=false")
    elif status != "NO_RECOVERABLE_CONTRAST" and h1.get("passed") is not True:
        problems.append("post-H1 statuses require h1.passed=true")
    if status in ("CONTRAST_ONLY_NO_PERSISTENCE",
                  "CONTRAST_AND_PERSISTENCE_NO_PARTITION_STRUCTURE",
                  "STRUCTURE_BEYOND_COPULA_NULL"):
        h2 = sealed.get("h2")
        if not isinstance(h2, dict) or "pairs" not in h2:
            problems.append("h2 block missing where status requires it")
        else:
            expected_h2 = status != "CONTRAST_ONLY_NO_PERSISTENCE"
            if h2.get("passed") is not expected_h2:
                problems.append(f"h2.passed must be {expected_h2} "
                                f"for status {status}")
    return problems


def verify() -> dict[str, Any]:
    """Adversarial replay: every provenance/metadata binding must verify
    before the numeric recompute is even attempted."""
    plan_path = V3_ROOT / f"{SCHEMA_PLAN}.json"
    plan = _bound_json(plan_path)
    plan_sha = _sha256(plan_path)
    inputs = plan.get("inputs", {})
    problems: list[str] = []
    for key, path in (
            ("trajectories_v3_sha256", TRAJ_V3),
            ("linkage_review_v1_sha256", REVIEW_V1),
            ("gcal_result_sha256", GCAL_RESULT),
            ("gcal_copula_null_sha256", GCAL_COPULA)):
        if not path.is_file():
            problems.append(f"input artifact absent: {path.name}")
        elif inputs.get(key) != _sha256(path):
            problems.append(f"plan input {key} does not bind current bytes")
    if problems:
        return {"status": "NKP_VERIFY_MISMATCH", "replay": "BINDING_FAILURE",
                "problems": problems}
    sealed = _bound_json(V3_ROOT / f"{SCHEMA_RESULT}.json")
    rows = _cohort()
    counts = {
        "complete_paths": len(rows),
        "pro_glacial": sum(1 for r in rows
                         if r["type"] == "Pro-glacial lakes"),
        "unconnected": sum(1 for r in rows
                         if r["type"] == "Unconnected glacial lakes"),
    }
    problems = _check_sealed_metadata(sealed, counts, plan_sha)
    if problems:
        return {"status": "NKP_VERIFY_MISMATCH", "replay": "BINDING_FAILURE",
                "problems": problems}
    # Provenance verified — now deterministic numeric replay.
    rng = np.random.default_rng(SEED)
    cents = _centroids_1990()
    cells = _cells(rows, cents)
    strata = _strata(rows)
    recomputed = {"h1": _evaluate_h1(rows, strata, cells, rng)}
    if recomputed["h1"]["passed"]:
        recomputed["h2"] = _evaluate_h2(rows, cells, rng)
    same = json.dumps(recomputed, sort_keys=True) == json.dumps(
        {k: sealed[k] for k in recomputed}, sort_keys=True)
    if not same:
        return {"status": "NKP_VERIFY_MISMATCH", "replay": "NUMERIC_MISMATCH"}
    return {"status": "NKP_VERIFY_OK", "replay": "EXACT_MATCH",
            "provenance_checks": "plan+inputs+cohort+status+authority",
            "sealed_status": sealed["status"]}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    sub.add_parser("run")
    sub.add_parser("verify")
    args = ap.parse_args(argv)
    try:
        result = {"plan": freeze_plan, "run": run,
                  "verify": verify}[args.cmd]()
    except (OSError, ValueError, KeyError, TypeError, IndexError,
            json.JSONDecodeError, ArithmeticError) as exc:
        print(f"NKP_{args.cmd.upper()}_BLOCKED: {exc}")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
