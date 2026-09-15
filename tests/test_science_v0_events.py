"""Behavioral tests for nepal.science_v0.events (synthetic inputs only)."""

import pytest

from nepal.science_v0.events import (
    ControlWindow,
    EventIdentity,
    ObservationOpportunity,
    SourceRow,
    assign_holdouts,
    build_controls,
    classify_timing,
    deduplicate,
    normalize_event,
    validate_cascade_graph,
)


def _row(key="r1", basin="koshi", mech="snow_avalanche",
         start="2020-06-01T00:00:00Z", end="2020-06-02T00:00:00Z",
         prec="day", **kw):
    return SourceRow(source_id="synthetic_inventory_v0",
                     source_version="0.0.0-synthetic",
                     source_row_key=key, mechanism=mech,
                     interval_start=start, interval_end=end,
                     declared_precision=prec, basin=basin, **kw)


class TestTimingClass:
    def test_day_precision(self):
        assert classify_timing("2020-06-01T00:00:00Z",
                               "2020-06-02T00:00:00Z", "day") == "EXACT_DAY"

    def test_three_day_bracket(self):
        assert classify_timing("2020-06-01T00:00:00Z",
                               "2020-06-04T00:00:00Z",
                               "interval_3d") == "INTERVAL_LE_7D"

    def test_month_declared_maps_coarse(self):
        assert classify_timing("2020-06-01T00:00:00Z",
                               "2020-06-02T00:00:00Z",
                               "month") == "COARSE_OR_UNRESOLVED"

    def test_wide_interval_coarse(self):
        assert classify_timing("2020-06-01T00:00:00Z",
                               "2020-08-01T00:00:00Z",
                               "interval") == "COARSE_OR_UNRESOLVED"


class TestNormalize:
    def test_canonical_id_and_variants(self):
        ev = normalize_event(_row())
        assert ev.event_id == \
            "synthetic_inventory_v0:0.0.0-synthetic:r1"
        assert ev.timing_class == "EXACT_DAY"
        assert ev.uncertainty_seconds == 86400
        assert len(ev.timing_variants) == 3

    def test_unknown_basin_rejected(self):
        with pytest.raises(ValueError, match="basin"):
            normalize_event(_row(basin="nowhere"))

    def test_inverted_interval_rejected(self):
        with pytest.raises(ValueError):
            normalize_event(_row(start="2020-06-02T00:00:00Z",
                                 end="2020-06-01T00:00:00Z"))


class TestDedupCascade:
    def test_duplicate_marked_not_merged(self):
        coarse = normalize_event(SourceRow(
            source_id="s2", source_version="v1", source_row_key="a",
            mechanism="m", interval_start="2020-06-01T00:00:00Z",
            interval_end="2020-06-15T00:00:00Z",
            declared_precision="interval_14d", basin="koshi"))
        fine = normalize_event(SourceRow(
            source_id="s1", source_version="v1", source_row_key="b",
            mechanism="m", interval_start="2020-06-03T00:00:00Z",
            interval_end="2020-06-04T00:00:00Z",
            declared_precision="day", basin="koshi"))
        out = deduplicate([coarse, fine])
        assert len(out) == 2
        canon = {e.event_id: e for e in out}
        finer = canon["s1:v1:b"]
        coarser = canon["s2:v1:a"]
        assert finer.duplicate_of is None
        assert coarser.duplicate_of == finer.event_id

    def test_cascade_dangling_parent_rejected(self):
        child = normalize_event(_row(key="c", parent_source_row_key="ghost"))
        with pytest.raises(ValueError, match="dangling"):
            validate_cascade_graph([child])

    def test_cascade_atomicity_across_basins(self):
        p = normalize_event(_row(key="p", basin="koshi",
                                 cascade_group_id="g1"))
        c = normalize_event(_row(key="c", basin="gandaki",
                                 cascade_group_id="g1",
                                 parent_source_row_key="p"))
        with pytest.raises(ValueError, match="atomic"):
            validate_cascade_graph([p, c])


class TestControls:
    def _opp(self, basin="koshi"):
        return ObservationOpportunity(
            source_id="synthetic_inventory_v0", basin=basin,
            window_start="2020-01-01T00:00:00Z",
            window_end="2020-12-31T00:00:00Z",
            coverage_class="OBSERVED_FULL")

    def test_control_requires_opportunity(self):
        windows = [("2020-03-01T00:00:00Z", "2020-03-08T00:00:00Z")]
        # no opportunity -> no control (missing coverage != negative)
        assert build_controls("synthetic_inventory_v0", "gandaki",
                              windows, [self._opp()], []) == []

    def test_control_rejected_when_event_overlaps(self):
        ev = normalize_event(_row())
        windows = [("2020-06-01T00:00:00Z", "2020-06-10T00:00:00Z")]
        assert build_controls("synthetic_inventory_v0", "koshi",
                              windows, [self._opp()], [ev]) == []

    def test_clean_control_emitted(self):
        ev = normalize_event(_row())
        windows = [("2020-03-01T00:00:00Z", "2020-03-08T00:00:00Z")]
        out = build_controls("synthetic_inventory_v0", "koshi",
                             windows, [self._opp()], [ev])
        assert len(out) == 1 and out[0].control_digest


class TestHoldouts:
    def _events(self):
        return [normalize_event(_row(key=f"e{i}", basin=b))
                for i, b in enumerate(
                    ["koshi", "gandaki", "karnali", "mahakali"])]

    def _basin_group(self):
        # 5 basins -> 3 disjoint geographic groups
        return {"koshi": "grp_east", "gandaki": "grp_central",
                "karnali": "grp_west", "mahakali": "grp_west",
                "bagmati": "grp_central"}

    def _split(self):
        return {"grp_east": "train", "grp_central": "test",
                "grp_west": "test"}

    def test_assignment_before_filtering(self):
        out = assign_holdouts(self._events(), self._basin_group(),
                              self._split(),
                              ("gandaki", "mahakali"), 86400 * 7)
        assert set(out.assignments.values()) == {"train", "test"}
        assert len(out.assignments) == 4
        assert out.basin_groups["grp_west"] == {"karnali", "mahakali"}

    def test_unknown_embargo_blocks(self):
        with pytest.raises(ValueError, match="embargo"):
            assign_holdouts(self._events(), self._basin_group(),
                            self._split(),
                            ("gandaki", "mahakali"), None)

    def test_two_groups_rejected(self):
        g = {"koshi": "g1", "gandaki": "g2"}
        s = {"g1": "train", "g2": "test"}
        evs = [normalize_event(_row(key="a", basin="koshi")),
               normalize_event(_row(key="b", basin="gandaki"))]
        with pytest.raises(ValueError, match=">= 3"):
            assign_holdouts(evs, g, s, ("gandaki", "koshi"), 1)

    def test_eval_region_must_be_test(self):
        with pytest.raises(ValueError, match="test"):
            assign_holdouts(self._events(), self._basin_group(),
                            self._split(),
                            ("koshi", "gandaki"), 1)

    def test_single_box_rejected(self):
        evs = [normalize_event(_row(key="a",
                                    basin="langtang_trishuli"))]
        g = {"langtang_trishuli": "g1", "koshi": "g2",
             "gandaki": "g3"}
        s = {"g1": "train", "g2": "test", "g3": "test"}
        with pytest.raises(ValueError, match="single-box"):
            assign_holdouts(evs, g, s, ("koshi", "gandaki"), 1,
                            eligible_universe={"langtang_trishuli"})

    def test_eventless_test_basin_is_valid_eval_region(self):
        # bagmati is in a test group but carries no event — still a
        # legal evaluation region (region, not event, bound).
        out = assign_holdouts(self._events(), self._basin_group(),
                              self._split(),
                              ("gandaki", "bagmati"), 86400 * 7)
        assert set(out.evaluation_regions) == {"bagmati", "gandaki"}

    def test_group_without_split_rejected(self):
        s = {"grp_east": "train", "grp_west": "test"}  # grp_central lost
        with pytest.raises(ValueError, match="split"):
            assign_holdouts(self._events(), self._basin_group(), s,
                            ("gandaki", "mahakali"), 1)
