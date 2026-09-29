"""GCAL: calibrate the frozen V2 clustering gate against null/planted data.

The V2 stability gate returned NO_ROBUST_STRUCTURE_UNDER_THIS_DESIGN.  That
verdict is only meaningful if the gate is known to (a) reject structureless
data (Type-I control) and (b) accept genuinely separated structure (power).
This script freezes the calibration contract FIRST (``plan``), then runs
each declared configuration deterministically (``run --config``), and
finally aggregates the partial results into a sealed report (``seal``).
``verify`` re-checks the sealed lane read-only: every partial must be
bound to the current plan digest, carry the declared config/kind, exact
replicate indices and seeds, and a recomputed pass count/rate; the
sealed result's digests, rates, and verdict must re-derive exactly.

Synthetic cohorts reuse the production gate by constructing candidate-path
records and calling the V2 engine's evaluate(); the gate code under test is
therefore exactly the frozen production implementation.  No real data is
clustered beyond the already-sealed V2 inputs, no event labels are used,
and no acquisition or authority claim is made.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Callable

import numpy as np
from threadpoolctl import threadpool_limits

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import hma_lake_trajectory_poc as engine  # noqa: E402
import hma_lake_trajectory_poc_v2 as v2  # noqa: E402
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402

E = engine.INTAKE_ROOT
OUTPUT_ROOT = E / "hma-gate-calibration-v0"
V2_ROOT = E / "hma-lake-trajectory-poc-v2"
TRAJ_PATH = V2_ROOT / "HMA_LAKE_TRAJECTORIES_V2.json"
V2_PLAN_PATH = V2_ROOT / "FROZEN_EVAL_PLAN_V2.json"
V2_RESULT_PATH = V2_ROOT / "HMA_LAKE_CLUSTER_STABILITY_V2.json"
SCHEMA_PLAN = "FROZEN_GCAL_PLAN_V0"
SCHEMA_RESULT = "HMA_GATE_CALIBRATION_V0"
SCHEMA_PARTIAL = "HMA_GATE_CALIBRATION_PARTIAL_V0"
BASE_SEED = 31415926
REPLICATES = 6
TYPE_I_TARGET = 0.05
POWER_TARGET = 0.80
VERDICTS = (
    "GATE_NOT_SPECIFIC_UNDER_DECLARED_NULLS",
    "GATE_UNDERPOWERED_V2_INCONCLUSIVE_AT_TESTED_SCALE",
    "GATE_CALIBRATED_V2_BOUNDED_NEGATIVE",
)


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _bound_json(path: Path) -> dict[str, Any]:
    sidecar = Path(f"{path}.sha256")
    expected = f"{_sha256(path)}  {path.name}\n"
    if not sidecar.is_file() or sidecar.read_text() != expected:
        raise ValueError(f"sidecar verification failed: {path}")
    return json.loads(path.read_text())


def _load_cohort() -> tuple[np.ndarray, list[str]]:
    doc = _bound_json(TRAJ_PATH)
    rows = [r for r in doc["trajectories"] if r["cluster_evaluation_eligible"]]
    x = np.asarray([r["area_change_rate_features"] for r in rows], float)
    regions = []
    for row in rows:
        first = next(o for o in row["observations"]
                     if o["epoch"] == engine.linkage.EPOCHS[0])
        regions.append(str(first.get("region_source") or "UNKNOWN"))
    return x, regions


def _regions_for(n: int, real_regions: list[str],
                 rng: np.random.Generator) -> list[str]:
    labels, counts = np.unique(np.asarray(real_regions), return_counts=True)
    probs = counts / counts.sum()
    return rng.choice(labels, size=n, p=probs).tolist()


def _normal_scores(x: np.ndarray) -> np.ndarray:
    from scipy.stats import norm, rankdata
    z = np.empty_like(x)
    for j in range(x.shape[1]):
        z[:, j] = norm.ppf((rankdata(x[:, j]) - 0.5) / x.shape[0])
    return z


def _gen_n1_copula(x, regions, rng):
    z = _normal_scores(x)
    cov = np.corrcoef(z, rowvar=False)
    draws = rng.multivariate_normal(np.zeros(x.shape[1]), cov, size=len(x))
    from scipy.stats import norm
    out = np.empty_like(x)
    for j in range(x.shape[1]):
        q = norm.cdf(draws[:, j])
        out[:, j] = np.quantile(x[:, j], q)
    return out


def _gen_n2_isotropic(x, regions, rng):
    mu, sd = x.mean(0), x.std(0)
    return rng.normal(mu, np.where(sd > 0, sd, 1.0), size=x.shape)


def _gen_n3_anisotropic(x, regions, rng):
    return rng.multivariate_normal(x.mean(0), np.cov(x, rowvar=False),
                                   size=len(x))


def _gen_n4_region_stratified(x, regions, rng):
    out = np.empty_like(x)
    reg = np.asarray(regions)
    for label in np.unique(reg):
        mask = reg == label
        sub = x[mask]
        if len(sub) > x.shape[1] + 2:
            cov = np.cov(sub, rowvar=False)
            try:
                out[mask] = rng.multivariate_normal(sub.mean(0), cov,
                                                    size=int(mask.sum()))
                continue
            except ValueError:
                pass
        out[mask] = rng.multivariate_normal(x.mean(0),
                                            np.cov(x, rowvar=False),
                                            size=int(mask.sum()))
    return out


def _gen_n5_permuted(x, regions, rng):
    out = np.empty_like(x)
    for j in range(x.shape[1]):
        out[:, j] = x[rng.permutation(len(x)), j]
    return out


def _planted(delta: float, weight: float) -> Callable:
    def gen(x, regions, rng):
        cov = np.cov(x, rowvar=False)
        vals, vecs = np.linalg.eigh(cov)
        direction = vecs[:, int(np.argmax(vals))]
        mean_shift = delta * direction
        n = len(x)
        n_a = int(round(n * weight))
        a = rng.multivariate_normal(x.mean(0), cov, size=n_a)
        b = rng.multivariate_normal(x.mean(0) + mean_shift, cov,
                                    size=n - n_a)
        return np.vstack([a, b])
    return gen


CONFIGS: dict[str, dict[str, Any]] = {
    "N1_GAUSSIAN_COPULA": {
        "kind": "null", "generator": _gen_n1_copula,
        "description": "Gaussian copula preserving empirical marginals and "
                       "normal-score correlation; no planted clusters",
    },
    "N2_ISOTROPIC_GAUSSIAN": {
        "kind": "null", "generator": _gen_n2_isotropic,
        "description": "independent per-column Gaussians matched to data "
                       "marginals; no correlation, no clusters",
    },
    "N3_ANISOTROPIC_GAUSSIAN": {
        "kind": "null", "generator": _gen_n3_anisotropic,
        "description": "single Gaussian matched to global mean/covariance; "
                       "correlated but structureless",
    },
    "N4_REGION_STRATIFIED_ANISOTROPIC": {
        "kind": "null", "generator": _gen_n4_region_stratified,
        "description": "per-region Gaussian mean/covariance draws; region "
                       "confound channel without planted clusters",
    },
    "N5_COLUMN_PERMUTED": {
        "kind": "null", "generator": _gen_n5_permuted,
        "description": "independent column permutation; marginals kept, "
                       "correlation destroyed, no clusters",
    },
    "P_D1.5_W0.5": {"kind": "planted", "generator": _planted(1.5, 0.5),
                    "description": "two-component Gaussian mixture, "
                                   "separation 1.5 SD along dominant PC"},
    "P_D2.0_W0.5": {"kind": "planted", "generator": _planted(2.0, 0.5),
                    "description": "two-component Gaussian mixture, "
                                   "separation 2.0 SD along dominant PC"},
    "P_D3.0_W0.5": {"kind": "planted", "generator": _planted(3.0, 0.5),
                    "description": "two-component Gaussian mixture, "
                                   "separation 3.0 SD along dominant PC"},
    "P_D3.0_W0.2": {"kind": "planted", "generator": _planted(3.0, 0.2),
                    "description": "minority-cluster mixture, separation "
                                   "3.0 SD at weight 0.2"},
}


def _seed_for(config_index: int, replicate: int) -> int:
    return BASE_SEED + config_index * 1000 + replicate


def _synthetic_doc(x_syn: np.ndarray, regions: list[str]) -> dict[str, Any]:
    obs_template = [{"epoch": e, "source_feature_present": True,
                     "region_source": None} for e in engine.linkage.EPOCHS]
    trajectories = []
    for i, (vec, region) in enumerate(zip(x_syn, regions)):
        obs = [dict(o) for o in obs_template]
        obs[0]["region_source"] = region
        trajectories.append({
            "candidate_path_id": f"SYNTH:{i:05d}",
            "cluster_evaluation_eligible": True,
            "area_change_rate_features": [float(v) for v in vec],
            "observations": obs,
        })
    return {"trajectories": trajectories}


def _plan_stub() -> dict[str, Any]:
    plan = _bound_json(V2_PLAN_PATH)
    return {
        "trajectory_artifact": plan["trajectory_artifact"],
        "stability_design": plan["stability_design"],
    }


def freeze_plan(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    x, regions = _load_cohort()
    labels, counts = np.unique(np.asarray(regions), return_counts=True)
    plan = {
        "schema": SCHEMA_PLAN,
        "version": 0,
        "status": "FROZEN_BEFORE_CALIBRATION_RUNS",
        "purpose": ("Calibrate the frozen V2 stability gate: Type-I error "
                    "under structure-preserving and structure-destroying "
                    "nulls, power under planted two-cluster mixtures. No "
                    "new science claim; calibrates the instrument only."),
        "gate_under_test": {
            "engine": "hma_lake_trajectory_poc.evaluate",
            "engine_code_sha256": _sha256(Path(engine.__file__)),
            "v2_wrapper_sha256": _sha256(Path(v2.__file__)),
            "spec": _bound_json(V2_PLAN_PATH)["stability_design"],
        },
        "inputs": {
            "trajectories_v2_sha256": _sha256(TRAJ_PATH),
            "frozen_eval_plan_v2_sha256": _sha256(V2_PLAN_PATH),
            "v2_result_sha256": _sha256(V2_RESULT_PATH),
            "v2_result_status": _bound_json(V2_RESULT_PATH)["status"],
        },
        "cohort_reference": {
            "n": int(x.shape[0]), "d": int(x.shape[1]),
            "region_distribution": {
                str(k): int(v) for k, v in zip(labels, counts)},
        },
        "configurations": {
            name: {
                "kind": cfg["kind"],
                "description": cfg["description"],
                "replicates": REPLICATES,
                "seeds": [_seed_for(i, r) for r in range(REPLICATES)],
            }
            for i, (name, cfg) in enumerate(CONFIGS.items())
        },
        "numeric_contract": {
            "type_i_error_target_per_null": TYPE_I_TARGET,
            "power_target_at_declared_separation": POWER_TARGET,
            "gate_pass_means": "evaluate() returns "
                               "ALGORITHMIC_STABILITY_PASS",
            "decision_rules": [
                "any null family pass-rate > 0.05 => gate not specific "
                "under that dependence structure; V2 verdict cannot be "
                "read as structure evidence without gate redesign",
                "planted pass-rate < 0.80 at separation 2.0 => gate "
                "underpowered; V2 negative inconclusive at that scale",
                "all nulls <= 0.05 and power >= 0.80 => V2 negative is a "
                "calibrated bounded negative for this cohort",
            ],
            "no_post_hoc_threshold_tuning": True,
        },
        "interpretation_limits": [
            "Calibration characterizes the gate only; a calibrated pass "
            "would still not establish natural lake classes.",
            "Synthetic cohorts inherit the real cohort's size and region "
            "mix; they do not extend the result to partial or "
            "containment-linked cohorts.",
            "Seeds, configurations, and replicate counts are fixed here; "
            "any change requires a superseding plan version.",
        ],
        "authority": dict(engine.linkage.AUTHORITY_FLAGS),
        "event_association_branch": "DORMANT",
    }
    engine._false_authority(plan, "gcal plan")
    out = output_root / f"{SCHEMA_PLAN}.json"
    sha = _publish_or_match(out, plan)
    return {"status": "FROZEN_GCAL_PLAN_SEALED", "path": str(out),
            "sha256": sha}


def _publish_or_match(path: Path, document: dict[str, Any]) -> str:
    if path.exists():
        digest = _sha256(path)
        sidecar = Path(f"{path}.sha256")
        expected = f"{digest}  {path.name}\n"
        if not sidecar.is_file() or sidecar.read_text() != expected:
            raise ValueError(f"sidecar verification failed: {path}")
        if json.loads(path.read_text()) != document:
            raise FileExistsError(
                f"write-once artifact exists with different content: {path}")
        return digest
    write_once_json(path, document)
    return write_once_sidecar(path)


def _partial_path(output_root: Path, config: str) -> Path:
    return output_root / f"GCAL_PARTIAL_{config}.json"


def run_config(config: str, output_root: Path = OUTPUT_ROOT) -> dict:
    plan = _bound_json(output_root / f"{SCHEMA_PLAN}.json")
    if config not in plan["configurations"]:
        raise ValueError(f"config {config} not declared in frozen plan")
    spec = plan["configurations"][config]
    cfg = CONFIGS[config]
    x, regions = _load_cohort()
    stub = _plan_stub()
    results = []
    with threadpool_limits(limits=1):
        for replicate, seed in enumerate(spec["seeds"]):
            rng = np.random.default_rng(seed)
            x_syn = cfg["generator"](x, regions, rng)
            syn_regions = _regions_for(len(x_syn), regions, rng)
            doc = _synthetic_doc(x_syn, syn_regions)
            res = engine.evaluate(doc, stub)
            results.append({
                "replicate": replicate, "seed": seed,
                "status": res["status"],
                "passed": res["status"] == "ALGORITHMIC_STABILITY_PASS",
                "selected_k": res["full_sample"]["selected_k"],
                "silhouette": res["full_sample"]["silhouette"],
                "fraction_selecting_full_sample_k":
                    res["stability"]["fraction_selecting_full_sample_k"],
                "min_cluster_mean_jaccard": min(
                    c["mean_best_match_jaccard"]
                    for c in res["stability"]["clusters"]),
            })
    passed = sum(r["passed"] for r in results)
    partial = {
        "schema": SCHEMA_PARTIAL,
        "config": config,
        "kind": cfg["kind"],
        "frozen_plan_sha256": _sha256(output_root / f"{SCHEMA_PLAN}.json"),
        "replicates": results,
        "pass_count": passed,
        "pass_rate": passed / len(results),
        "authority": dict(engine.linkage.AUTHORITY_FLAGS),
    }
    sha = _publish_or_match(_partial_path(output_root, config), partial)
    return {"status": "GCAL_CONFIG_COMPLETE", "config": config,
            "pass_rate": partial["pass_rate"], "sha256": sha}


def _derive_verdict(max_null: float, min_power: float) -> str:
    if max_null > TYPE_I_TARGET:
        return VERDICTS[0]
    if min_power < POWER_TARGET:
        return VERDICTS[1]
    return VERDICTS[2]


def _check_partial(name: str, spec: dict[str, Any], output_root: Path,
                   plan_sha: str) -> dict[str, Any]:
    path = _partial_path(output_root, name)
    if not path.is_file():
        raise ValueError(f"missing partial for declared config: {name}")
    partial = _bound_json(path)
    if partial.get("schema") != SCHEMA_PARTIAL:
        raise ValueError(f"{name}: unexpected partial schema")
    if partial.get("frozen_plan_sha256") != plan_sha:
        raise ValueError(f"{name}: partial bound to a stale/superseded plan")
    if partial.get("config") != name:
        raise ValueError(f"{name}: partial config field mismatch")
    if partial.get("kind") != spec["kind"]:
        raise ValueError(f"{name}: partial kind differs from frozen plan")
    engine._false_authority(partial, f"gcal partial {name}")
    seeds = spec.get("seeds")
    if not isinstance(seeds, list) or len(seeds) != spec["replicates"]:
        raise ValueError(f"{name}: frozen plan seed list inconsistent")
    reps = partial.get("replicates")
    if not isinstance(reps, list) or len(reps) != spec["replicates"]:
        raise ValueError(f"{name}: replicate count != declared "
                         f"{spec['replicates']}")
    passed = 0
    for i, r in enumerate(reps):
        if r.get("replicate") != i or r.get("seed") != seeds[i]:
            raise ValueError(f"{name}: replicate/seed binding broken at {i}")
        if not isinstance(r.get("passed"), bool):
            raise ValueError(f"{name}: replicate {i} missing passed flag")
        if "status" in r and r["passed"] != (
                r["status"] == "ALGORITHMIC_STABILITY_PASS"):
            raise ValueError(f"{name}: passed/status disagree at {i}")
        passed += r["passed"]
    if partial.get("pass_count") != passed:
        raise ValueError(f"{name}: pass_count disagrees with replicates")
    if partial.get("pass_rate") != passed / len(reps):
        raise ValueError(f"{name}: pass_rate disagrees with replicates")
    return partial


def _bound_plan_and_partials(output_root: Path):
    plan_path = output_root / f"{SCHEMA_PLAN}.json"
    plan = _bound_json(plan_path)
    if plan.get("schema") != SCHEMA_PLAN:
        raise ValueError("unexpected plan schema")
    engine._false_authority(plan, "gcal plan")
    plan_sha = _sha256(plan_path)
    partials = {
        name: _check_partial(name, spec, output_root, plan_sha)
        for name, spec in plan["configurations"].items()
    }
    return plan, plan_sha, partials


def _rates(plan: dict[str, Any], partials: dict[str, Any]):
    null_rates = {n: p["pass_rate"] for n, p in partials.items()
                  if plan["configurations"][n]["kind"] == "null"}
    planted_rates = {n: p["pass_rate"] for n, p in partials.items()
                     if plan["configurations"][n]["kind"] == "planted"}
    if not null_rates or not planted_rates:
        raise ValueError("frozen plan lacks null or planted configs")
    return null_rates, planted_rates


def _verify_result(output_root: Path, plan: dict[str, Any], plan_sha: str,
                   partials: dict[str, Any]) -> dict[str, Any]:
    result = _bound_json(output_root / f"{SCHEMA_RESULT}.json")
    if result.get("schema") != SCHEMA_RESULT:
        raise ValueError("unexpected result schema")
    if result.get("frozen_plan_sha256") != plan_sha:
        raise ValueError("result bound to a stale/superseded plan")
    if result.get("event_association_branch") != "DORMANT":
        raise ValueError("result event-association branch not DORMANT")
    engine._false_authority(result, "gcal result")
    declared = plan["configurations"]
    if set(result.get("configs", {})) != set(declared):
        raise ValueError("result config set differs from frozen plan")
    null_rates, planted_rates = {}, {}
    for name, spec in declared.items():
        entry = result["configs"][name]
        if entry.get("sha256") != _sha256(_partial_path(output_root, name)):
            raise ValueError(f"{name}: sealed digest != current partial")
        if entry.get("kind") != spec["kind"]:
            raise ValueError(f"{name}: result kind differs from plan")
        if entry.get("pass_rate") != partials[name]["pass_rate"]:
            raise ValueError(f"{name}: result pass_rate mismatch")
        if entry.get("replicates") != partials[name]["replicates"]:
            raise ValueError(f"{name}: result replicates != partial")
        (null_rates if spec["kind"] == "null"
         else planted_rates)[name] = partials[name]["pass_rate"]
    max_null = max(null_rates.values())
    min_power = min(planted_rates.values())
    for field, recomputed in (
            ("null_pass_rates", null_rates),
            ("planted_pass_rates", planted_rates),
            ("max_null_pass_rate", max_null),
            ("min_planted_pass_rate", min_power),
            ("type_i_target", TYPE_I_TARGET),
            ("power_target", POWER_TARGET)):
        if result.get(field) != recomputed:
            raise ValueError(f"result {field} inconsistent with recomputed")
    verdict = result.get("verdict")
    if verdict not in VERDICTS:
        raise ValueError(f"undeclared verdict: {verdict}")
    if verdict != _derive_verdict(max_null, min_power):
        raise ValueError("result verdict inconsistent with recomputed rates")
    return result


def seal(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    plan, plan_sha, partials = _bound_plan_and_partials(output_root)
    null_rates, planted_rates = _rates(plan, partials)
    max_null = max(null_rates.values())
    min_power = min(planted_rates.values())
    verdict = _derive_verdict(max_null, min_power)
    result = {
        "schema": SCHEMA_RESULT,
        "version": 0,
        "frozen_plan_sha256": plan_sha,
        "verdict": verdict,
        "null_pass_rates": null_rates,
        "planted_pass_rates": planted_rates,
        "max_null_pass_rate": max_null,
        "min_planted_pass_rate": min_power,
        "type_i_target": TYPE_I_TARGET,
        "power_target": POWER_TARGET,
        "configs": {n: {"pass_rate": p["pass_rate"],
                        "kind": plan["configurations"][n]["kind"],
                        "sha256": _sha256(_partial_path(output_root, n)),
                        "replicates": p["replicates"]}
                    for n, p in partials.items()},
        "interpretation": plan["numeric_contract"]["decision_rules"],
        "event_association_branch": "DORMANT",
        "authority": dict(engine.linkage.AUTHORITY_FLAGS),
    }
    out = output_root / f"{SCHEMA_RESULT}.json"
    sha = _publish_or_match(out, result)
    _verify_result(output_root, plan, plan_sha, partials)
    return {"status": "GCAL_SEALED", "verdict": verdict, "sha256": sha}


def verify(output_root: Path = OUTPUT_ROOT) -> dict[str, Any]:
    plan, plan_sha, partials = _bound_plan_and_partials(output_root)
    result = _verify_result(output_root, plan, plan_sha, partials)
    return {"status": "GCAL_VERIFY_CLEAN",
            "verdict": result["verdict"],
            "configs_checked": len(partials),
            "result_sha256": _sha256(output_root / f"{SCHEMA_RESULT}.json")}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("plan")
    r = sub.add_parser("run")
    r.add_argument("--config", required=True)
    sub.add_parser("seal")
    sub.add_parser("verify")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "plan":
            result = freeze_plan()
        elif args.cmd == "run":
            result = run_config(args.config)
        elif args.cmd == "verify":
            result = verify()
        else:
            result = seal()
    except (OSError, ValueError, KeyError, TypeError, IndexError,
            json.JSONDecodeError, ArithmeticError) as exc:
        print(f"GCAL_{args.cmd.upper()}_BLOCKED: {exc}")
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
