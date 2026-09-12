"""Phase A catalog tests: date policy, source/impact separation, mechanism
policy, fixed holdouts, duplicates/cascades, RGI crosswalk, gate A, artifacts."""
import json

import pytest

from nepal.framework_v1 import contract as C
from nepal.framework_v1.catalog import (normalize_raw_date, classify_mechanism,
                                        assign_holdout_group,
                                        freeze_holdout_plan, normalize_record,
                                        link_duplicates_and_cascades,
                                        adjudicate_eligibility,
                                        evaluate_gate_a, build_catalog,
                                        write_phase_a_artifacts, CSV_FIELDS,
                                        A_ARTIFACTS)
from nepal.framework_v1.controls import ControlsConfig
from nepal.framework_v1.provenance import verify_manifest, sha256_canonical


def raw_row(**over):
    base = {"glacier_id": "G0001", "event_name": "Test Event",
            "lat": 33.5, "lon": 82.5, "glacier_name": "Test Glacier",
            "country": "China", "rgi_region_name": "Inner Tibet",
            "rgi_v7_id": "RGI2000-v7.0-G-13-00001",
            "day": 17, "month": "July", "year": 2016,
            "date_min": None, "date_max": None,
            "hazard_type": "Rock-Ice Avalanche",
            "total_volume": 68, "initial_volume": None, "large": 1,
            "slope_detachment_zone": 12.9,
            "triggers": "Precipitation trend; Weak bedrock",
            "impact": "Traveled 6 km; no fatalities",
            "references": "Kaeaeb et al. (2016)",
            "links": "https://doi.org/10.1038/s41561-017-0039-7",
            "comments": "Glacier instability; surge-like behavior",
            "uncertainties": ""}
    base.update(over)
    return base


CONFIG = ControlsConfig()


class TestDatePolicy:
    def test_exact_day(self):
        out = normalize_raw_date(raw_row(day=17, month="July", year=2016))
        assert out["date_precision"] == "EXACT_DAY"
        assert out["date_min"] == out["date_max"] == "2016-07-17"

    def test_exact_day_whitespace_month(self):
        out = normalize_raw_date(raw_row(day=1, month=" July ", year=2019))
        assert out["date_precision"] == "EXACT_DAY"

    def test_source_supported_interval_at_most_7_days(self):
        out = normalize_raw_date(raw_row(
            day=None, month=None, year=None,
            date_min="2019-09-03 00:00:00", date_max="2019-09-09"))
        assert out["date_precision"] == "INTERVAL"
        assert out["date_min"] == "2019-09-03" and out["date_max"] == "2019-09-09"

    def test_iso_date_rejects_unstructured_trailing_text(self):
        out = normalize_raw_date(raw_row(
            day=None, month=None, year=None,
            date_min="2019-09-03 not-a-time", date_max="2019-09-09"))
        assert out["date_precision"] == "UNRESOLVED"
        assert any("unparseable" in note for note in out["date_parse_notes"])

    def test_exact_date_must_agree_with_one_sided_bounds(self):
        out = normalize_raw_date(raw_row(
            day=17, month="July", year=2016,
            date_min="2016-07-18", date_max=None))
        assert out["date_precision"] == "UNRESOLVED"
        assert any("conflicts" in note for note in out["date_parse_notes"])

        out = normalize_raw_date(raw_row(
            day=17, month="July", year=2016,
            date_min="2016-07-01", date_max=None))
        assert out["date_precision"] == "EXACT_DAY"

    def test_interval_longer_than_7_days_ineligible(self):
        out = normalize_raw_date(raw_row(
            day=None, month=None, year=None,
            date_min="2019-04-01", date_max="2019-04-20"))
        assert out["date_precision"] == "INTERVAL"
        assert any("ineligible" in n for n in out["date_parse_notes"])

    def test_month_only_never_invents_day_15(self):
        out = normalize_raw_date(raw_row(day=None, month="November", year=2016))
        assert out["date_precision"] == "MONTH_ONLY"
        assert out["date_min"] == "2016-11-01"
        assert out["date_max"] == "2016-11-30"
        assert out["date_min"] != "2016-11-15"

    def test_season_only_not_converted_to_date(self):
        out = normalize_raw_date(raw_row(day=None, month="Fall", year=2007))
        assert out["date_precision"] == "SEASON_ONLY"
        assert out["date_min"] is None and out["date_max"] is None
        assert "season" in out["date_parse_notes"][-1]

    def test_malformed_day(self):
        out = normalize_raw_date(raw_row(day=45, month="July", year=2016))
        assert out["date_precision"] == "MALFORMED"
        assert "malformed" in out["date_parse_notes"][-1]

    def test_ambiguous_slash_date_left_unresolved(self):
        out = normalize_raw_date(raw_row(
            day=None, month=None, year=None,
            date_min="04/10/2016", date_max="07/04/2016"))
        assert out["date_precision"] == "UNRESOLVED"
        assert out["date_min"] is None and out["date_max"] is None

    def test_unambiguous_dmy_parsed(self):
        out = normalize_raw_date(raw_row(
            day=None, month=None, year=None,
            date_min="17/07/2016", date_max="23/07/2016"))
        assert out["date_precision"] == "INTERVAL"
        assert out["date_min"] == "2016-07-17"

    def test_no_date_info_resolves_unresolved(self):
        out = normalize_raw_date(raw_row(day=None, month=None, year=None))
        assert out["date_precision"] == "UNRESOLVED"

    def test_eligibility_requires_exact_day_or_short_interval(self):
        eligible = {"EXACT_DAY", "INTERVAL"}
        assert set(C.CATALOG_ELIGIBILITY_RULES["date"]
                   ["eligible_precisions"]) == eligible
        assert C.CATALOG_ELIGIBILITY_RULES["date"][
            "never_replace_missing_day_with_day_15"] is True
class TestSourceImpactAndMechanism:
    def test_source_vs_impact_separation(self):
        rec = normalize_record(raw_row(lat=33.5, lon=82.5,
                                       impact="Traveled 6 km; killed 3"),
                               controls=CONFIG)
        assert rec["source_lat"] == 33.5 and rec["source_lon"] == 82.5
        assert rec["impact_locations"] == []
        assert rec["impact_precision"] == "NONE_GEOREFERENCED"
        assert rec["mechanism_confirmation_scope"] == (
            "SOURCE_CITATION_ONLY_NOT_INDEPENDENT_FIELD_ADJUDICATION")
        assert "6 km" in rec["impact_raw"]

    def test_source_precision_class(self):
        rec = normalize_record(raw_row(lat=28.288708, lon=85.528159),
                               controls=CONFIG)
        assert rec["source_precision"] == "HIGH_<=11M"

    def test_rock_ice_in_cohort(self):
        assert classify_mechanism("Rock-Ice Avalanche") == \
            C.MechanismClass.ICE_ROCK_AVALANCHE

    def test_glof_excluded_from_cohort(self):
        assert classify_mechanism("GLOF") == C.MechanismClass.GLOF
        assert C.MechanismClass.GLOF.value not in \
            C.CATALOG_ELIGIBILITY_RULES["mechanism"]["ice_rock_cohort"]

    def test_ice_into_lake_separately_classified(self):
        assert classify_mechanism("Ice Avalanche (into lake)") == \
            C.MechanismClass.ICE_INTO_LAKE
        assert C.MechanismClass.ICE_INTO_LAKE.value in \
            C.CATALOG_ELIGIBILITY_RULES["mechanism"]["separately_classified"]

    def test_unknown_hazard_maps_unresolved(self):
        assert classify_mechanism("Something weird") == \
            C.MechanismClass.UNRESOLVED

    def test_mechanism_dissent_disqualifies(self):
        rec = normalize_record(raw_row(),
                               controls=CONFIG,
                               overrides={"Test Event": {
                                   "mechanism_dissent": True}})
        assert rec["mechanism_dissent"] is True
        assert rec["mechanism_confirmed"] is False

    def test_gate_counting_excludes_dissent(self):
        rows = [normalize_record(raw_row(event_name=f"E{i}"),
                                 controls=CONFIG) for i in range(5)]
        rows[0]["mechanism_dissent"] = True
        adjudicate_eligibility(rows)
        assert rows[0]["eligibility_status"] == "INELIGIBLE"
        assert "MECHANISM_DISSENT" in rows[0]["eligibility_reasons"]

    def test_transboundary_flag(self):
        rec = normalize_record(raw_row(country="Kyrgyzstan-Tajikistan"),
                               controls=CONFIG)
        assert rec["transboundary"] is True


class TestThameExclusion:
    def test_thame_excluded_as_precursor_analog(self):
        rec = normalize_record(raw_row(event_name="Thame 2025"),
                               controls=CONFIG)
        adjudicate_eligibility([rec])
        assert rec["eligibility_status"] == "INELIGIBLE"
        assert any("THAME" in r for r in rec["eligibility_reasons"])


class TestHoldoutPolicy:
    def test_group_assigned_before_eligibility_filtering(self):
        raw = [raw_row(event_name="Exact Date Event", day=5, month="Jan",
                       year=2020),
               raw_row(event_name="Month Only Event", day=None,
                       month="November", year=2019),
               raw_row(event_name="No Date Event", day=None, month=None,
                       year=None)]
        # Assign groups to EVERY raw row (ineligible ones included).
        kh = []
        for r in raw:
            gid, basis = assign_holdout_group(r)
            kh.append((r["event_name"], gid, basis))
        plan = freeze_holdout_plan(
            [{"holdout_group": g, "holdout_group_basis": b}
             for _, g, b in kh])
        assert plan["n_groups"] == 1  # all three rows -> same Test Glacier
        assert plan["frozen_before_eligibility_filtering"] is True
        assert plan["plan_sha256"]
        # Plan hash commits BEFORE date/mechanism filtering happened.
        assert len({pair[1] for pair in kh}) == 1

    def test_group_does_not_change_after_eligible_count_observed(self):
        raw = [raw_row(event_name=f"E{i}", day=1, month="Feb", year=2020,
                       glacier_name=f"Glacier {i}") for i in range(4)]
        plan_a = freeze_holdout_plan(
            [{"holdout_group": assign_holdout_group(r)[0], } for r in raw])
        plan_b = freeze_holdout_plan(
            [{"holdout_group": assign_holdout_group(r)[0], } for r in raw])
        assert plan_a["plan_sha256"] == plan_b["plan_sha256"]

    def test_holdout_basis_order_glacier_then_region_then_grid(self):
        g, b = assign_holdout_group(raw_row(glacier_name="Zemu Glacier",
                                            rgi_region_name="Kashmir",
                                            lat=33.5, lon=82.5))
        assert g == "GL-zemu_glacier" and b == "source_glacier_or_basin"
        g, b = assign_holdout_group(raw_row(glacier_name="",
                                            rgi_region_name="Kashmir",
                                            lat=33.5, lon=82.5))
        assert g == "MR-kashmir" and b == "fixed_hma_macroregion"
        g, b = assign_holdout_group(raw_row(glacier_name="",
                                            rgi_region_name="",
                                            lat=33.5, lon=82.5))
        assert g.startswith("GRID-") and b == "deterministic_spatial_fallback"
        g, b = assign_holdout_group(raw_row(glacier_name="",
                                            rgi_region_name="",
                                            lat=None, lon=None))
        assert g == "GRID-UNKNOWN"

    def test_placeholder_names_do_not_become_geographic_holdouts(self):
        g, basis = assign_holdout_group(raw_row(
            glacier_name="No information", rgi_region_name="Unknown",
            lat=28.2, lon=85.5))
        assert basis == "deterministic_spatial_fallback"
        assert g.startswith("GRID-")


class TestDuplicateCascade:
    def test_duplicate_marked_and_canonical_kept(self):
        a = normalize_record(raw_row(event_name="Dup Event", year=2018),
                             controls=CONFIG)
        b = normalize_record(raw_row(event_name="Dup Event", year=2018,
                                     lat=33.5001, lon=82.5001),
                             controls=CONFIG)
        rows = sorted([a, b], key=lambda r: r["event_id"])
        link_duplicates_and_cascades(rows)
        dupes = [r for r in rows if r["duplicate_of"] is not None]
        assert len(dupes) == 1
        assert dupes[0]["duplicate_of"] in {r["event_id"] for r in rows}

    def test_same_glacier_events_within_90_days_form_cascade(self):
        a = normalize_record(raw_row(event_name="Cascade A", lat=33.1,
                                     lon=80.1, day=None, month=None,
                                     year=None,
                                     date_min="2018-05-01",
                                     date_max="2018-05-01"),
                             controls=CONFIG, event_id="FE1-aaaaaaaaaaaa")
        b = normalize_record(raw_row(event_name="Cascade B", lat=33.2,
                                     lon=80.2, day=None, month=None,
                                     year=None,
                                     date_min="2018-05-20",
                                     date_max="2018-05-20"),
                             controls=CONFIG, event_id="FE1-bbbbbbbbbbbb")
        assert a["date_min"] == "2018-05-01" and b["date_min"] == "2018-05-20"
        link_duplicates_and_cascades([a, b])
        assert a["cascade_group_id"] and b["cascade_group_id"]
        assert a["cascade_group_id"] == b["cascade_group_id"]

    def test_same_glacier_events_far_apart_not_cascade(self):
        a = normalize_record(raw_row(event_name="Far A", lat=33.1, lon=80.1,
                                     day=None, month=None, year=None,
                                     date_min="2018-01-01",
                                     date_max="2018-01-01"),
                             controls=CONFIG, event_id="FE1-cccccccccccc")
        b = normalize_record(raw_row(event_name="Far B", lat=33.2, lon=80.2,
                                     day=None, month=None, year=None,
                                     date_min="2018-12-01",
                                     date_max="2018-12-01"),
                             controls=CONFIG, event_id="FE1-dddddddddddd")
        assert a["date_min"] == "2018-01-01" and b["date_min"] == "2018-12-01"
        link_duplicates_and_cascades([a, b])
        assert a["cascade_group_id"] is None
        assert b["cascade_group_id"] is None

    def test_same_location_different_named_events_are_not_merged(self):
        a = normalize_record(raw_row(event_name="Shuraki 2016a", year=2016),
                             controls=CONFIG)
        b = normalize_record(raw_row(event_name="Shuraki 2016b", year=2016),
                             controls=CONFIG)
        link_duplicates_and_cascades([a, b])
        assert a["duplicate_of"] is None
        assert b["duplicate_of"] is None


class TestRgiCrosswalk:
    def test_rgi60_crosswalk_absent_by_default(self):
        rec = normalize_record(raw_row(), controls=CONFIG)
        assert rec["rgi60_crosswalk_status"] == "ABSENT"
        assert rec["rgi60_id"] is None

    def test_documented_crosswalk_recorded(self):
        rec = normalize_record(
            raw_row(), controls=CONFIG,
            rgi60_crosswalk={"RGI2000-v7.0-G-13-00001": "RGI60-13.09876"})
        assert rec["rgi60_crosswalk_status"] == "DOCUMENTED"
        assert rec["rgi60_id"] == "RGI60-13.09876"


class TestEvidenceAndUnits:
    def test_declared_units_are_recorded_without_overwriting_raw_fields(self):
        rec = normalize_record(raw_row(total_volume=2, volume_units="m3"),
                               controls=CONFIG)
        assert rec["volume_min_m3"] == rec["volume_max_m3"] == 2.0
        assert rec["volume_units"] == "m3"
        assert rec["volume_unit_confidence"] == "DECLARED_M3"

    def test_missing_units_are_explicit_assumption(self):
        rec = normalize_record(raw_row(total_volume=2), controls=CONFIG)
        assert rec["volume_min_m3"] == rec["volume_max_m3"] == 2.0e6
        assert rec["volume_unit_confidence"] == "ASSUMED_COMPILED_MILLION_M3"

    def test_impact_prose_is_not_claimed_as_geocoded(self):
        rec = normalize_record(raw_row(impact="Gyirong Port, 6 km downstream"),
                               controls=CONFIG)
        assert rec["impact_locations"] == []
        assert rec["impact_coordinate_status"] == "TEXT_ONLY"

    def test_explicit_impact_coordinate_is_retained_separately(self):
        rec = normalize_record(raw_row(impact_lat=28.1, impact_lon=85.6),
                               controls=CONFIG)
        assert rec["impact_locations"] == [{"lat": 28.1, "lon": 85.6}]
        assert rec["impact_coordinate_status"] == "GEOCODED"
class TestGateA:
    def _eligible_catalog(self, n=5, n_groups=2):
        raw = []
        for i in range(n):
            raw.append(raw_row(
                event_name=f"Gate Event {i}", day=10 + i, month="June",
                year=2020 + i, glacier_name=f"Gate Glacier {i}",
                hazard_type="Rock-Ice Avalanche",
                total_volume=50 + i, lat=33.0 + i, lon=80.0 + i,
                impact=f"Traveled {i} km"))
        return build_catalog(raw, controls=CONFIG)

    def test_gate_passes_with_five_eligible_two_groups(self):
        gate = self._eligible_catalog(n=5)["gate"]
        assert gate["passed"] is True
        assert gate["n_eligible"] == 5
        assert len(gate["eligible_holdout_groups"]) >= 2

    def test_gate_fails_below_five_eligible(self):
        gate = self._eligible_catalog(n=4)["gate"]
        assert gate["passed"] is False
        assert gate["checks"]["min_eligible_events"]["passed"] is False
        assert any("NOT loosened" in c for c in gate["consequences_if_failed"])

    def test_gate_fails_with_one_group_only(self):
        raw = [raw_row(event_name=f"One Glacier Event {i}", day=10 + i,
                       month="June", year=2020 + i,
                       glacier_name="Single Glacier", lat=33.0 + i,
                       lon=80.0 + i) for i in range(5)]
        gate = build_catalog(raw, controls=CONFIG)["gate"]
        assert gate["passed"] is False
        assert gate["checks"]["min_eligible_holdout_groups"]["passed"] is False

    def test_month_only_row_never_counts_toward_gate(self):
        raw = [raw_row(event_name="MRO", day=None, month="June", year=2021,
                       glacier_name="MRO Glacier",
                       hazard_type="Rock-Ice Avalanche")]
        rec = build_catalog(raw, controls=CONFIG)["rows"][0]
        assert rec["eligibility_status"] == "INELIGIBLE"
        assert "DATE_MONTH_ONLY" in rec["eligibility_reasons"]


class TestArtifacts:
    def test_all_phase_a_artifacts_written_and_manifest_verifies(self, tmp_path):
        raw = [raw_row(event_name=f"Art Event {i}", day=10 + i, month="June",
                       year=2020 + i, glacier_name=f"Art Glacier {i}",
                       lat=33.0 + i, lon=80.0 + i) for i in range(5)]
        result = build_catalog(raw, controls=CONFIG, language="en",
                               access_date="2026-08-27")
        paths = write_phase_a_artifacts(result, tmp_path)
        assert sorted(p.name for p in tmp_path.iterdir()) == sorted(A_ARTIFACTS)
        manifest = json.loads((tmp_path / "catalog_manifest.json").read_text())
        ok, problems = verify_manifest(tmp_path, manifest)
        assert ok is True and problems == []

    def test_artifacts_deterministic_across_rebuilds(self, tmp_path):
        raw = [raw_row(event_name=f"Det Event {i}", day=10 + i, month="June",
                       year=2020 + i, glacier_name=f"Det Glacier {i}",
                       lat=33.0 + i, lon=80.0 + i) for i in range(5)]
        first, second = tmp_path / "a", tmp_path / "b"
        write_phase_a_artifacts(build_catalog(raw, controls=CONFIG), first)
        write_phase_a_artifacts(build_catalog(raw, controls=CONFIG), second)
        for name in A_ARTIFACTS:
            assert (first / name).read_bytes() == (second / name).read_bytes()

    def test_normalized_csv_columns_and_hash_tieback(self, tmp_path):
        import csv as _csv
        raw = [raw_row(event_name="Csv Event", day=1, month="Feb", year=2021,
                       glacier_name="Csv Glacier", lat=33.1, lon=80.1)]
        write_phase_a_artifacts(build_catalog(raw, controls=CONFIG), tmp_path)
        with (tmp_path / "catalog_normalized.csv").open(newline="") as fh:
            reader = _csv.DictReader(fh)
            assert set(reader.fieldnames) == set(CSV_FIELDS)
            row = next(reader)
        assert row["raw_row_sha256"] == sha256_canonical(dict(raw[0]))
        assert row["eligibility_status"] == "ELIGIBLE"

    def test_raw_records_preserved_unchanged(self):
        raw = [raw_row(event_name="Pres Event", day=3, month="Mar", year=2022,
                       glacier_name="Pres Glacier", lat=33.2, lon=80.2)]
        snapshot = json.dumps(raw, sort_keys=True)
        result = build_catalog(raw, controls=CONFIG)
        assert json.dumps(raw, sort_keys=True) == snapshot
        row = result["rows"][0]
        assert row["raw_date_min"] is None
        assert row["date_precision"] == "EXACT_DAY"

    def test_stable_event_id(self):
        from nepal.framework_v1.catalog import stable_event_id
        assert stable_event_id(raw_row()) == stable_event_id(raw_row())
        assert stable_event_id(raw_row()).startswith("FE1-")
        assert stable_event_id(raw_row(event_name="Other")) != \
            stable_event_id(raw_row())
        assert C.CATALOG_ELIGIBILITY_RULES["date"][
            "never_invent_date_from_month_or_season"] is True
