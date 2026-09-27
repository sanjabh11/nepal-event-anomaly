"""Network-free tests for the non-authorizing Route-B selector inventory."""

import copy
import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "scripts"))
import armc_v17_request_plan_v1 as planner

EVIDENCE = Path("/Users/sanjayb/nepal-event-anomaly-evidence/p5-armc-pressure-levels-2026-09-22/retrieval")
AMENDMENT = Path("/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19/retrieval/p5_amendment_v17r_armc_perlake_scope.json")
MAPPING_PATH = EVIDENCE / "armc_event_episode_mapping_v0.json"
PROTOCOL_PATH = EVIDENCE / "armc_v17_protocol_v0.json"
DECISION_PATH = EVIDENCE / "armc_event_adjudication_decision_v0.json"
if not MAPPING_PATH.is_file():
    pytest.skip("operator-local ARMC evidence lane absent (CI checkout)",
                allow_module_level=True)
MAPPING = json.loads(MAPPING_PATH.read_text())
PROTOCOL = json.loads(PROTOCOL_PATH.read_text())
DECISION = json.loads(DECISION_PATH.read_text())


def build(mapping=MAPPING, protocol=PROTOCOL, decision=DECISION):
    return planner.build_plan(
        mapping,
        protocol,
        decision,
        episode_mapping_sha256=planner.sha256_file(MAPPING_PATH),
        protocol_sha256=planner.sha256_file(PROTOCOL_PATH),
        decision_sha256=planner.sha256_file(DECISION_PATH),
        basis_amendment_sha256=planner.sha256_file(AMENDMENT),
    )


def _event(plan, member_id):
    return next(row for row in plan["event_windows"] if row["member_id"] == member_id)


def test_scope_is_seven_raw_inputs_derived_from_route_b_not_old_regime_contract():
    plan = build()
    assert plan["raw_variable_count"] == 7
    assert plan["raw_variables"] == {
        "single_level": ["tp", "tcwv", "cape", "t2m", "sp", "sf"],
        "pressure_level_hpa_500": ["t"],
    }
    assert plan["protocol_exposure_raw_variables"] == {
        "tp_antecedent_7d": ["tp"],
        "tcwv_mean": ["tcwv"],
        "cape_mean": ["cape"],
        "theta500_minus_thetasfc": ["t@500hPa", "t2m", "sp"],
        "sf_daily": ["sf"],
    }
    assert plan["feature_contract_status"].startswith("ROUTE_B_SPECIFIC_TRANSFORM_CONTRACT_MISSING")
    assert "17-column daily regime contract" in plan["feature_contract_status"]
    assert not {"d2m", "sd", "u10", "v10", "z", "q", "w"} & {
        variable for values in plan["raw_variables"].values() for variable in values
    }


def test_all_eligible_event_members_are_retained_not_just_analysis_unit_dates():
    plan = build()
    assert plan["n_analysis_units"] == 14
    assert plan["n_eligible_event_members"] == 15
    assert plan["n_logical_event_variable_observations"] == 15 * 7
    recurrence = [row for row in plan["event_windows"] if row["analysis_unit_id"] == "ep_01"]
    assert {(row["member_id"], row["event_date_utc"]) for row in recurrence} == {
        ("400", "2002-05-23"),
        ("401", "2002-06-29"),
    }
    assert _event(plan, "401")["climatology_month"] == 6
    assert _event(plan, "401")["exposure_window"]["start_date"] == "2002-06-19"
    assert plan["recurrence_policy"].startswith("UNRESOLVED")


def test_antecedent_spillover_days_are_exact_and_not_whole_extra_months():
    plan = build()
    assert len(plan["antecedent_spillover_selections"]) == 2
    spills = {
        (row["year"], row["month"]): row["dates"]
        for row in plan["antecedent_spillover_selections"]
    }
    assert spills[(2016, 4)] == [
        "2016-04-27", "2016-04-28", "2016-04-29", "2016-04-30",
    ]
    assert spills[(2025, 6)] == ["2025-06-28", "2025-06-29", "2025-06-30"]
    assert _event(plan, "484")["exposure_window"]["end_date"] == "2016-05-03"
    assert _event(plan, "762")["exposure_window"]["end_date"] == "2025-07-04"


def test_climatology_is_deduplicated_by_site_and_month_and_keeps_all_washout_members():
    plan = build()
    assert plan["n_unique_climatology_groups"] == 14
    assert plan["n_unique_climatology_variable_selections"] == 14 * 7
    groups = [row for row in plan["climatology_selections"] if "438" in row["member_ids"]]
    assert len(groups) == 1
    assert groups[0]["member_ids"] == ["438", "454"]
    assert groups[0]["calendar_month"] == 6
    assert groups[0]["years"] == list(range(2001, 2026))
    assert {row["member_id"] for row in groups[0]["washout_event_intervals"]} == {"438", "454"}


def test_grid_centers_are_explicit_but_source_coordinates_remain_a_gate():
    plan = build()
    assert all(row["grid_selection"]["cell_count"] == 4 for row in plan["analysis_units"])
    for row in plan["analysis_units"]:
        selection = row["grid_selection"]
        box = row["event_box"]
        assert selection["source_coordinate_verification"] == "REQUIRED_BEFORE_EXTRACTION_NOT_YET_VERIFIED"
        assert all(box["lat_min"] <= lat <= box["lat_max"] for lat in selection["latitudes_descending"])
        assert all(box["lon_min"] <= lon <= box["lon_max"] for lon in selection["longitudes_ascending"])
        assert all(
            abs(value / planner.GRID_DEG - round(value / planner.GRID_DEG)) < 1e-8
            for value in selection["latitudes_descending"] + selection["longitudes_ascending"]
        )


def test_provider_call_count_is_not_invented_and_candidate_is_not_authorization():
    plan = build()
    assert plan["provider_request_count"] is None
    assert plan["status"] == "CANDIDATE_ONLY_NOT_AUTHORIZED_FOR_ACQUISITION"
    assert plan["canary_status"].startswith("NOT_DESIGNED")
    assert plan["data_semantics_gate"].startswith("FREEZE_AND_VERIFY")
    assert plan["estimate"]["below_basis_cap_on_model_estimate"] is True
    assert plan["estimate"]["estimated_bytes_with_overhead"] < plan["estimate"]["cap_bytes_from_unsigned_basis_amendment"]


def test_byte_estimate_uses_deduplicated_month_and_spillover_selectors():
    plan = build()
    cells_hours = 0
    for group in plan["climatology_selections"]:
        month = group["calendar_month"]
        cells_hours += sum(
            planner.calendar.monthrange(year, month)[1] for year in planner.YEARS
        ) * 24 * group["grid_selection"]["cell_count"]
    for group in plan["antecedent_spillover_selections"]:
        cells_hours += len(group["dates"]) * 24 * group["grid_selection"]["cell_count"]
    raw = cells_hours * 7 * planner.BYTES_PER_VALUE
    assert plan["estimate"]["raw_decoded_array_bytes"] == raw
    assert plan["estimate"]["estimated_bytes_with_overhead"] == planner.math.ceil(
        raw * planner.PLANNING_OVERHEAD_FACTOR
    )


def test_mapping_member_set_must_equal_adjudication_eligible_set():
    mapping = copy.deepcopy(MAPPING)
    mapping["units"][0]["member_ids"].remove("401")
    with pytest.raises(ValueError, match="eligible-member mismatch"):
        build(mapping)


def test_member_coordinates_must_match_episode_box():
    decision = copy.deepcopy(DECISION)
    row = next(event for event in decision["events"] if event["event_id"].endswith(":484"))
    row["local"]["lat"] += 0.01
    with pytest.raises(ValueError, match="coordinates disagree"):
        build(decision=decision)


def test_non_day_precision_event_fails_closed():
    decision = copy.deepcopy(DECISION)
    row = next(event for event in decision["events"] if event["event_id"].endswith(":484"))
    row["adjudication"]["event_time_interval"]["precision"] = "month"
    with pytest.raises(ValueError, match="not day-precision"):
        build(decision=decision)


def test_route_b_protocol_drift_fails_closed():
    protocol = copy.deepcopy(PROTOCOL)
    protocol["estimand"]["secondary_exposures"][0] = "tcwv or precip"
    with pytest.raises(ValueError, match="secondary exposures differ"):
        build(protocol=protocol)


def test_invalid_digest_fails_closed():
    with pytest.raises(ValueError, match="protocol_sha256"):
        planner.build_plan(
            MAPPING,
            PROTOCOL,
            DECISION,
            episode_mapping_sha256="a" * 64,
            protocol_sha256="not-a-digest",
            decision_sha256="b" * 64,
            basis_amendment_sha256="c" * 64,
        )


def test_sealed_v17r_plan_remains_v0_and_is_not_relabelled():
    old_plan = json.loads((EVIDENCE / "armc_v17_request_plan_v0.json").read_text())
    assert old_plan["schema"] == "P5_V17_REQUEST_PLAN_V0"
    assert old_plan["raw_variable_count"] == 14
    assert build()["schema"] == "P5_V17_SELECTOR_INVENTORY_V1"
