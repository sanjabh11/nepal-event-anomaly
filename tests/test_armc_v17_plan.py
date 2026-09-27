"""Tests for the v17 request-plan generator — network-free validation."""
import json
from pathlib import Path
import pytest
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import armc_v17_request_plan as rp

EVID = Path("/Users/sanjayb/nepal-event-anomaly-evidence/p5-armc-pressure-levels-2026-09-22/retrieval")
if (EVID / "armc_event_episode_mapping_v0.json").is_file():
    MAP = json.loads((EVID / "armc_event_episode_mapping_v0.json").read_text())
    PLAN = json.loads((EVID / "armc_v17_request_plan_v0.json").read_text())
else:
    MAP = PLAN = None
pytestmark = pytest.mark.skipif(
    MAP is None,
    reason="operator-local ARMC evidence lane absent (CI checkout)")

def test_request_count():
    assert len(PLAN["requests"]) == 14 * 14  # 14 units x (10 single + 4 pressure)
    assert PLAN["n_requests"] == 196

def test_months_match_episode_map():
    umonth = {u["unit_id"]: u["month"] for u in MAP["units"]}
    for r in PLAN["requests"]:
        assert r["month"] == umonth[r["unit_id"]]
        assert r["month"] in (4,5,6,7,8,9)

def test_all_years_present():
    for r in PLAN["requests"]:
        assert r["years"] == list(range(2001, 2026))

def test_snapshot_pinned():
    assert PLAN["snapshot"] == "ZFKDHBCTBVHVXM3BQFV0"

def test_only_declared_variables():
    allowed = set(rp.SINGLE_VARS) | {p["var"] for p in rp.PRESSURE_VARS}
    for r in PLAN["requests"]:
        assert r["var"] in allowed
        if r["group"] == "pressure/temporal":
            assert r["level_hpa"] == 500

def test_box_centered_on_event():
    umap = {u["unit_id"]: u for u in MAP["units"]}
    for r in PLAN["requests"]:
        u = umap[r["unit_id"]]
        assert abs((r["box"]["lat_min"] + r["box"]["lat_max"]) / 2 - u["lat"]) < 1e-3
        assert abs((r["box"]["lon_min"] + r["box"]["lon_max"]) / 2 - u["lon"]) < 1e-3
        assert abs(r["box"]["lat_max"] - r["box"]["lat_min"] - 0.5) < 1e-3

def test_byte_cap_declared():
    assert PLAN["est_mb"] < 500  # hard cap for the lane

def test_plan_deterministic():
    p2 = rp.build_plan(MAP)
    assert p2["n_requests"] == PLAN["n_requests"]
    assert [r["unit_id"] for r in p2["requests"]] == [r["unit_id"] for r in PLAN["requests"]]
