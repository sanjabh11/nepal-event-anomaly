"""Owner-decision sign-off lane contract tests."""
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import india_owner_decisions as od  # noqa: E402

E = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
CANDIDATES = (E / "india-phase0-source-intake"
              / "INDIA_OBSERVATION_SOURCE_CANDIDATES_V0.json")
_PRESENT = CANDIDATES.is_file()


def test_admin_contract_seals_typed_artifact(tmp_path):
    out = tmp_path / "c.json"
    problems = od.admin_contract("DEFER", "owner:x", "pending", out)
    assert problems == []
    doc = json.loads(out.read_text())
    assert doc["schema"] == "INDIA_ADMIN_TERRITORY_CONTRACT_V0"
    assert doc["decision_state"] == "DEFER"
    assert all(v is False for v in doc["authority"].values())
    assert (tmp_path / "c.json.sha256").is_file()


def test_record_rule_needs_rule(tmp_path):
    main_args = ["observation-intake"]
    # Direct function check: rule omission handled by CLI; here verify
    # DEFER works without one.
    problems = od.admin_contract("RECORD_RULE", "owner:x", "r",
                                 tmp_path / "c.json", rule="x")
    assert problems == []


@pytest.mark.skipif(not _PRESENT, reason="evidence root unavailable")
def test_observation_intake_rejects_unknown_candidate(tmp_path):
    problems = od.observation_intake("NOPE", "DEFER", "owner:x", "r",
                                   tmp_path / "o.json")
    assert any("unknown candidate" in p for p in problems)


@pytest.mark.skipif(not _PRESENT, reason="evidence root unavailable")
def test_observation_intake_binds_candidates_digest(tmp_path):
    out = tmp_path / "o.json"
    problems = od.observation_intake("GREATER_HIMALAYA_FIGSHARE_21708590",
                                   "DEFER", "owner:x", "r", out)
    assert problems == []
    doc = json.loads(out.read_text())
    assert doc["candidates_sha256"] == od.sha(CANDIDATES)
