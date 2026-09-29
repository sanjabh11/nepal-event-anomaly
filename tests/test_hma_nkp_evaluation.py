"""NKP evaluation lane contract tests."""
import json
import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hma_nkp_evaluation as nkp  # noqa: E402

V3 = Path("/Users/sanjayb/nepal-event-anomaly-evidence"
          "/india-phase0-source-intake/hma-lake-trajectory-poc-v3")
_V3_PRESENT = (V3 / "HMA_LAKE_TRAJECTORIES_V3.json").is_file()


def _row(i, rate, typ="Unconnected glacial lakes",
         region="Central Himalaya", elev=5000.0, area=1e6):
    return {"id": f"P{i}", "rates": [rate] * 4, "mean_rate": rate,
            "type": typ, "region": region, "elevation": elev,
            "area_m2_1990": area, "root_feature_id": f"GH:1990:{i:05d}"}


def test_auc_perfect_and_zero():
    pos = np.array([1.0, 2.0, 3.0])
    neg = np.array([-1.0, 0.0])
    assert nkp._auc(pos, neg) == 1.0
    assert nkp._auc(neg, pos) == 0.0
    assert nkp._auc(np.array([0.5]), np.array([0.5])) == 0.5


def test_stratified_auc_recovers_known_contrast():
    rng = np.random.default_rng(0)
    rows = ([_row(i, 0.05 + rng.normal(0, 0.01), "Pro-glacial lakes")
             for i in range(30)]
            + [_row(100 + i, -0.02 + rng.normal(0, 0.01))
               for i in range(120)])
    strata = ["s"] * len(rows)
    auc = nkp._stratified_auc(rows, strata)
    assert auc > 0.9


def test_placebo_auc_centers_on_half():
    rng = np.random.default_rng(1)
    rows = ([_row(i, rng.normal(0, 0.02), "Pro-glacial lakes")
             for i in range(30)]
            + [_row(100 + i, rng.normal(0, 0.02))
               for i in range(120)])
    strata = ["s"] * len(rows)
    auc = nkp._stratified_auc(rows, strata)
    assert abs(auc - 0.5) < 0.15


def test_spearman_intervals():
    rng = np.random.default_rng(2)
    rates = rng.normal(0, 1, size=(200, 4))
    rates[:, 2] = rates[:, 0] + rng.normal(0, 0.1, 200)
    assert nkp._spearman(rates[:, 0], rates[:, 2]) > 0.9
    assert abs(nkp._spearman(rates[:, 0], rates[:, 1])) < 0.2


def test_strata_split_and_cells():
    rows = [_row(i, 0.01, elev=4000 + i, area=10.0 ** (4 + i % 4))
            for i in range(40)]
    strata = nkp._strata(rows)
    assert len(strata) == 40 and "|" in strata[0]
    cents = {r["root_feature_id"]: (i * 50_000.0, 0.0)
             for i, r in enumerate(rows)}
    cells = nkp._cells(rows, cents)
    assert cells[0] == cells[1]  # 0 and 50 km share a 100 km cell
    assert cells[0] != cells[2]  # 100 km boundary crossed


@pytest.mark.skipif(not _V3_PRESENT,
                    reason="V3 trajectories not sealed yet")
def test_frozen_plan_binds_v3_inputs():
    plan_path = V3 / "FROZEN_NKP_PLAN_V0.json"
    if not plan_path.is_file():
        pytest.skip("plan not frozen yet")
    plan = json.loads(plan_path.read_text())
    assert plan["schema"] == nkp.SCHEMA_PLAN
    assert all(v is False for v in plan["authority"].values())
    assert plan["event_association_branch"] == "DORMANT"
    assert plan["no_post_hoc_changes"] is True
