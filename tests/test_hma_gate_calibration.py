"""GCAL calibration lane contract tests."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hma_gate_calibration as gcal  # noqa: E402

E = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
PLAN = (E / "india-phase0-source-intake" / "hma-gate-calibration-v0"
        / "FROZEN_GCAL_PLAN_V0.json")
_PRESENT = (E / "india-phase0-source-intake"
            / "hma-lake-trajectory-poc-v2"
            / "HMA_LAKE_TRAJECTORIES_V2.json").is_file()


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
