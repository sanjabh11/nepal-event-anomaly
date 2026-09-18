"""SEISMIC-EVT — typed event/opportunity/control package (S10).

Detection claims need labelled truth without leakage: events after
the cutoff reject, out-of-coverage events censor, cascade events
label, partial-coverage opportunities can never be negatives, and
labels live on a separate surface — never in the predictor set.
"""
from __future__ import annotations

import pytest

import nepal.seismic_sidecar.events as ev


def _rows(station="STA1", n=10):
    rows = []
    for i in range(n):
        h = i // 60
        m = i % 60
        rows.append({
            "station_id": station,
            "window_start": f"2020-06-01T{h:02d}:{m:02d}:00Z",
            "window_end": f"2020-06-01T{h:02d}:{m + 1:02d}:00Z",
            "unit_id": f"unit-{station}", "basin_group": "g",
            "date": "2020-06-01"})
    return rows


def _event(eid="e1", origin="2020-06-01T00:05:30Z", **kw):
    base = dict(event_id=eid, origin_iso=origin,
                source_catalog="TESTCAT")
    base.update(kw)
    return ev.SeismicEventV0(**base)


def _opp(station="STA1", start="2020-06-01T00:20:00Z",
         end="2020-06-01T00:30:00Z", coverage=1.0, **kw):
    base = dict(station_id=station, window_start=start,
                window_end=end, coverage_basis="full",
                coverage_fraction=coverage)
    base.update(kw)
    return ev.OpportunityWindowV0(**base)


CUTOFF = "2020-06-02T00:00:00Z"


class TestRecordValidation:
    def test_event_requires_utc_origin(self):
        e = _event(origin="2020-06-01 00:05:30")
        assert any("explicit-UTC" in p for p in e.validate())

    def test_event_requires_catalog(self):
        e = _event(source_catalog="")
        assert any("source_catalog" in p for p in e.validate())

    def test_event_bad_coordinates(self):
        e = _event(latitude=95.0)
        assert any("latitude" in p for p in e.validate())

    def test_inverted_opportunity_rejects(self):
        o = _opp(start="2020-06-01T00:30:00Z",
                 end="2020-06-01T00:20:00Z")
        assert any("inverted" in p for p in o.validate())


class TestPackageAssembly:
    def test_happy_path_labels(self):
        pkg = ev.build_event_package(
            events=(_event(),), opportunities=(_opp(),),
            frame_rows=_rows(), cutoff_iso=CUTOFF)
        assert pkg.problems == ()
        labels = dict(pkg.label_map)
        assert any(v == "EVENT" for v in labels.values())
        assert any(v == "CONTROL" for v in labels.values())
        assert len(pkg.package_digest) == 64
        assert len(pkg.labels_digest) == 64
        assert len(pkg.events_digest) == 64
        assert pkg.controls

    def test_post_cutoff_event_rejects(self):
        pkg = ev.build_event_package(
            events=(_event(origin="2020-06-03T00:00:00Z"),),
            opportunities=(_opp(),),
            frame_rows=_rows(), cutoff_iso=CUTOFF)
        assert any("cutoff" in p and "leakage" in p
                   for p in pkg.problems)

    def test_duplicate_event_id_rejects(self):
        pkg = ev.build_event_package(
            events=(_event("e1"), _event("e1")), opportunities=(),
            frame_rows=_rows(), cutoff_iso=CUTOFF)
        assert any("duplicate" in p for p in pkg.problems)

    def test_cascade_flagged(self):
        pkg = ev.build_event_package(
            events=(
                _event("e1", origin="2020-06-01T00:05:00Z"),
                _event("e2", origin="2020-06-01T00:05:30Z")),
            opportunities=(), frame_rows=_rows(),
            cutoff_iso=CUTOFF, dedup_seconds=120.0)
        assert pkg.problems == ()
        assert pkg.cascade and "e2" in pkg.cascade[0]

    def test_out_of_coverage_event_censored(self):
        pkg = ev.build_event_package(
            events=(_event(origin="2020-06-05T00:00:00Z"),),
            opportunities=(), frame_rows=_rows(),
            cutoff_iso="2020-06-10T00:00:00Z")
        assert pkg.problems == ()
        assert pkg.censored == ("e1",)
        assert not any(v == "EVENT" for v in pkg.label_map.values())

    def test_partial_coverage_opportunity_not_a_negative(self):
        pkg = ev.build_event_package(
            events=(), opportunities=(_opp(coverage=0.6),),
            frame_rows=_rows(), cutoff_iso=CUTOFF)
        assert pkg.problems == ()
        assert not pkg.controls
        assert any("partial coverage" in c for c in pkg.censored)
        assert not any(v == "CONTROL" for v in
                       pkg.label_map.values())

    def test_event_inside_opportunity_not_a_control(self):
        pkg = ev.build_event_package(
            events=(_event(origin="2020-06-01T00:25:00Z"),),
            opportunities=(_opp(),),
            frame_rows=_rows(), cutoff_iso=CUTOFF)
        assert pkg.problems == ()
        assert not pkg.controls

    def test_labels_are_separate_surface(self):
        """Labels never enter the feature digest or predictor set —
        they live in label_map keyed by (station|window_start)."""
        pkg = ev.build_event_package(
            events=(_event(),), opportunities=(_opp(),),
            frame_rows=_rows(), cutoff_iso=CUTOFF)
        assert pkg.problems == ()
        for key in pkg.label_map:
            sta, _ws = key.split("|", 1)
            assert sta == "STA1"
        # The label surface carries only the declared vocabulary.
        assert set(pkg.label_map.values()) <= \
            {"EVENT", "CONTROL"}

    def test_station_scoped_events(self):
        rows = _rows("STA1") + _rows("STA2")
        pkg = ev.build_event_package(
            events=(_event(station_ids=("STA2",)),),
            opportunities=(), frame_rows=rows, cutoff_iso=CUTOFF)
        assert pkg.problems == ()
        event_keys = [k for k, v in pkg.label_map.items()
                      if v == "EVENT"]
        assert event_keys and all(
            k.startswith("STA2|") for k in event_keys)
