"""GCAL calibration lane contract tests."""
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hma_gate_calibration as gcal  # noqa: E402

E = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
GCAL_ROOT = E / "india-phase0-source-intake" / "hma-gate-calibration-v0"
PLAN = GCAL_ROOT / "FROZEN_GCAL_PLAN_V0.json"
RESULT = GCAL_ROOT / "HMA_GATE_CALIBRATION_V0.json"
_PRESENT = (E / "india-phase0-source-intake"
            / "hma-lake-trajectory-poc-v2"
            / "HMA_LAKE_TRAJECTORIES_V2.json").is_file()
_AUTH = dict(gcal.engine.linkage.AUTHORITY_FLAGS)


def test_configs_declared_and_disjoint_seeds():
    seeds = []
    for i, name in enumerate(gcal.CONFIGS):
        for r in range(gcal.REPLICATES):
            seeds.append(gcal._seed_for(i, r))
    assert len(seeds) == len(set(seeds))
    kinds = {c["kind"] for c in gcal.CONFIGS.values()}
    assert kinds == {"null", "planted"}


@pytest.mark.skipif(not _PRESENT, reason="evidence root unavailable")
def test_frozen_plan_sealed_and_binds_v2(tmp_path):
    assert PLAN.is_file()
    plan = json.loads(PLAN.read_text())
    assert plan["status"] == "FROZEN_BEFORE_CALIBRATION_RUNS"
    assert all(v is False for v in plan["authority"].values())
    assert plan["event_association_branch"] == "DORMANT"
    # every declared config exists in code
    assert set(plan["configurations"]) == set(gcal.CONFIGS)


@pytest.mark.skipif(not _PRESENT, reason="evidence root unavailable")
def test_null_generators_preserve_shape():
    x, regions = gcal._load_cohort()
    rng = np.random.default_rng(0)
    for name, cfg in gcal.CONFIGS.items():
        if cfg["kind"] != "null":
            continue
        out = cfg["generator"](x[:200], regions[:200], rng)
        assert out.shape == (200, x.shape[1]), name
        assert np.isfinite(out).all(), name


def test_synthetic_doc_gate_contract():
    x = np.zeros((10, 4))
    doc = gcal._synthetic_doc(x, ["R"] * 10)
    row = doc["trajectories"][0]
    assert row["cluster_evaluation_eligible"] is True
    assert row["observations"][0]["epoch"] == 1990
    assert row["observations"][0]["region_source"] == "R"


# --- adversarial seal/verify tests on a synthetic lane --------------------

def _write_doc(root: Path, name: str, doc: dict) -> Path:
    p = root / name
    p.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    Path(f"{p}.sha256").write_text(f"{digest}  {p.name}\n")
    return p


def _mutate_doc(root: Path, name: str, mutate) -> None:
    p = root / name
    doc = json.loads(p.read_text())
    mutate(doc)
    _write_doc(root, name, doc)


def _tiny_plan() -> dict:
    return {
        "schema": gcal.SCHEMA_PLAN,
        "authority": dict(_AUTH),
        "event_association_branch": "DORMANT",
        "numeric_contract": {"decision_rules": ["rule"]},
        "configurations": {
            "N_FAKE_NULL": {"kind": "null", "replicates": 3,
                            "seeds": [gcal._seed_for(0, r)
                                      for r in range(3)]},
            "P_FAKE_PLANT": {"kind": "planted", "replicates": 3,
                             "seeds": [gcal._seed_for(1, r)
                                       for r in range(3)]},
        },
    }


def _tiny_partial(name: str, spec: dict, plan_sha: str,
                  passes: list[bool]) -> dict:
    reps = [{"replicate": i, "seed": s,
             "status": ("ALGORITHMIC_STABILITY_PASS" if passes[i]
                        else "NO_ROBUST_STRUCTURE_UNDER_THIS_DESIGN"),
             "passed": passes[i], "attacker_free_field": "x"}
            for i, s in enumerate(spec["seeds"])]
    n = sum(passes)
    return {"schema": gcal.SCHEMA_PARTIAL, "config": name,
            "kind": spec["kind"], "frozen_plan_sha256": plan_sha,
            "replicates": reps, "pass_count": n, "pass_rate": n / len(reps),
            "authority": dict(_AUTH), "extra_top_level": 1}


def _build_lane(root: Path, mutate_null=None) -> Path:
    plan = _tiny_plan()
    plan_sha = hashlib.sha256(
        _write_doc(root, f"{gcal.SCHEMA_PLAN}.json", plan)
        .read_bytes()).hexdigest()
    passes = {"N_FAKE_NULL": [False, False, False],
              "P_FAKE_PLANT": [True, True, True]}
    for name, spec in plan["configurations"].items():
        doc = _tiny_partial(name, spec, plan_sha, passes[name])
        if name == "N_FAKE_NULL" and mutate_null:
            mutate_null(doc)
        _write_doc(root, f"GCAL_PARTIAL_{name}.json", doc)
    return root


@pytest.fixture
def lane(tmp_path):
    _build_lane(tmp_path)
    gcal.seal(tmp_path)
    return tmp_path


def test_verify_clean_synthetic_lane_and_never_writes(lane):
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(lane.iterdir())}
    out = gcal.verify(lane)
    assert out["status"] == "GCAL_VERIFY_CLEAN"
    assert out["verdict"] == "GATE_CALIBRATED_V2_BOUNDED_NEGATIVE"
    assert out["configs_checked"] == 2
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
             for p in sorted(lane.iterdir())}
    assert before == after


def test_tampered_replicate_seed_rejected(lane):
    _mutate_doc(lane, "GCAL_PARTIAL_N_FAKE_NULL.json",
                lambda d: d["replicates"][1].update(seed=d["replicates"]
                                                    [1]["seed"] + 1))
    with pytest.raises(ValueError):
        gcal.verify(lane)


def test_swapped_config_field_rejected(lane):
    _mutate_doc(lane, "GCAL_PARTIAL_N_FAKE_NULL.json",
                lambda d: d.update(config="P_FAKE_PLANT"))
    with pytest.raises(ValueError):
        gcal.verify(lane)


def test_swapped_kind_rejected(lane):
    _mutate_doc(lane, "GCAL_PARTIAL_N_FAKE_NULL.json",
                lambda d: d.update(kind="planted"))
    with pytest.raises(ValueError):
        gcal.verify(lane)


def test_stale_plan_binding_rejected(lane):
    _mutate_doc(lane, "GCAL_PARTIAL_N_FAKE_NULL.json",
                lambda d: d.update(frozen_plan_sha256="0" * 64))
    with pytest.raises(ValueError):
        gcal.verify(lane)


def test_inflated_pass_count_rejected(lane):
    _mutate_doc(lane, "GCAL_PARTIAL_N_FAKE_NULL.json",
                lambda d: d.update(pass_count=3, pass_rate=1.0))
    with pytest.raises(ValueError):
        gcal.verify(lane)


def test_passed_flag_flip_rejected(lane):
    _mutate_doc(lane, "GCAL_PARTIAL_N_FAKE_NULL.json",
                lambda d: d["replicates"][0].update(passed=True))
    with pytest.raises(ValueError):
        gcal.verify(lane)


def test_missing_replicate_rejected(lane):
    _mutate_doc(lane, "GCAL_PARTIAL_N_FAKE_NULL.json",
                lambda d: d["replicates"].pop())
    with pytest.raises(ValueError):
        gcal.verify(lane)


def test_reordered_or_duplicated_replicates_rejected(lane):
    _mutate_doc(lane, "GCAL_PARTIAL_N_FAKE_NULL.json",
                lambda d: d["replicates"].__setitem__(
                    1, dict(d["replicates"][0])))
    with pytest.raises(ValueError):
        gcal.verify(lane)


def test_authority_flag_flip_rejected(lane):
    _mutate_doc(lane, "GCAL_PARTIAL_N_FAKE_NULL.json",
                lambda d: d["authority"].update(forecast_authorized=True))
    with pytest.raises(ValueError):
        gcal.verify(lane)


def test_tampered_sidecar_rejected(lane):
    sidecar = lane / "GCAL_PARTIAL_N_FAKE_NULL.json.sha256"
    sidecar.write_text("0" * 64 + "  GCAL_PARTIAL_N_FAKE_NULL.json\n")
    with pytest.raises(ValueError):
        gcal.verify(lane)


def test_result_verdict_inconsistent_rejected(lane):
    _mutate_doc(lane, f"{gcal.SCHEMA_RESULT}.json",
                lambda d: d.update(verdict=gcal.VERDICTS[0]))
    with pytest.raises(ValueError):
        gcal.verify(lane)


def test_result_undeclared_verdict_rejected(lane):
    _mutate_doc(lane, f"{gcal.SCHEMA_RESULT}.json",
                lambda d: d.update(verdict="GATE_FINE_TRUST_ME"))
    with pytest.raises(ValueError):
        gcal.verify(lane)


def test_result_partial_digest_swap_rejected(lane):
    _mutate_doc(lane, "GCAL_PARTIAL_N_FAKE_NULL.json",
                lambda d: d.update(post_seal_swap=True))
    with pytest.raises(ValueError):
        gcal.verify(lane)


def test_seal_rejects_tampered_partial(tmp_path):
    _build_lane(tmp_path,
                lambda d: d["replicates"][0].update(seed=0))
    with pytest.raises(ValueError):
        gcal.seal(tmp_path)
    assert not (tmp_path / f"{gcal.SCHEMA_RESULT}.json").exists()


@pytest.mark.skipif(not RESULT.is_file(), reason="evidence root unavailable")
def test_real_sealed_lane_verifies_clean():
    out = gcal.verify(gcal.OUTPUT_ROOT)
    assert out["status"] == "GCAL_VERIFY_CLEAN"
    assert out["configs_checked"] == len(gcal.CONFIGS)
    assert out["verdict"] in gcal.VERDICTS


@pytest.mark.skipif(not RESULT.is_file(), reason="evidence root unavailable")
def test_real_seal_matches_existing_bytes():
    out = gcal.seal(gcal.OUTPUT_ROOT)
    assert out["status"] == "GCAL_SEALED"
    assert out["sha256"] == hashlib.sha256(RESULT.read_bytes()).hexdigest()
