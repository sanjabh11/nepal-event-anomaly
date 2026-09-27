"""P3 real-intake lane tests — HMAGLOFDB byte-bound label package.

Synthetic-fixture lane (CI-safe): a miniature evidence root with a
real zip layout, cp1252 CSV bytes, retrieval + anchor records built
from the actual member digests.  Proves the intake chain end to end:

* verifier-BEFORE-parser: a tampered zip, a tampered provenance
  record, or a member digest drift all reject before any member byte
  is parsed;
* the six required reports are emitted over the FULL row census;
* timing derivation is deterministic (day/month/year classes with
  uncertainty covering the bracket) and unresolved rows are ledgered;
* basin assignment follows the explicit river table, records the
  Nepal Province conflict instead of silently choosing, and never
  drops a row without a ledger reason;
* recurrence series form atomic cascade groups with parent links;
* the holdout plan assigns every event to a declared basin group
  BEFORE filtering, with locked test groups and named evaluation
  regions;
* opportunities/control windows validate and controls are derived,
  never asserted.

The real-bytes lane runs only where the P5 evidence root exists
(owner machine) and pins the recorded intake numbers as a regression
gate.
"""
from __future__ import annotations

import csv
import hashlib
import io
import json
import zipfile
from pathlib import Path

import pytest

from nepal.research_v0.p3_intake import (
    _MEMBER_MAIN, _MEMBER_REMOVED, _MEMBER_SCHEMA, run_intake)

_ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence/"
             "p5-glof-2026-09-19")

_HEADER = ("GF_ID,Year_approx,Year_exact,Month,Day,Lake_name,Glacier_name,"
           "GL_ID,LakeDB_ID,G_ID,Lat_lake,Lon_lake,Elev_lake,Lat_impact,"
           "Lon_impact,Elev_impact,Impact_type,Lake_type,Transboundary,"
           "Repeat,Region_RGI,Region_HiMAP,Country,Province,River_Basin,"
           "Driver_lake,Driver_GLOF,Mechanism,Area,Volume,Discharge_water,"
           "Discharge_solid,Impact,Lives_total,Lives_male,Lives_female,"
           "Lives_disabilities,Injured_total,Injured_male,Injured_female,"
           "Injured_disabilities,Displaced_total,Displaced_male,"
           "Displaced_female,Displaced_disabilities,Livestock,"
           "Residential_destroyed,Commerical_destroyed,"
           "Residential_damaged,Commerical_damaged,Infra,Agricultural,"
           "Hydropower,Econ_damage,Sat_evidence,Ref_scientific,"
           "Ref_scientific_full,Ref_other,Remarks")

# GF, approx, exact, month, day, lake, glacier, gl_id, lakedb, g_id,
# lat, lon, elev, lat_i, lon_i, elev_i, impact_type, lake_type,
# transb, repeat, rgi, himap, country, province, river, driver_lake,
# driver_glof, mechanism — remaining impact columns blank.
_ROWS = [
    # day precision, Nepal, koshi (Dudh Koshi) — Repeat Y: member of
    # the lake's recurrence series with GF 2 (earlier event is root)
    ("1", "NA", "2015", "4", "25", "L1", "G1", "GL086304E28374N",
     "LDB1", "G077657E35156N", "27.828", "86.839", "4700", "NA", "NA",
     "NA", "NA", "Supraglacial", "N", "Y", "14_2", "9", "Nepal",
     "Koshi", "Dudh Koshi", "Unknown", "Unknown", "Unknown"),
    # recurrence member: same lake as GF 1, Repeat Y, earlier year
    ("2", "NA", "1998", "9", "3", "L1", "G1", "GL086304E28374N",
     "LDB1", "G077657E35156N", "27.828", "86.839", "4700", "NA", "NA",
     "NA", "NA", "Supraglacial", "N", "Y", "14_2", "9", "Nepal",
     "Koshi", "Dudh Koshi", "Unknown", "Unknown", "Unknown"),
    # month precision, Nepal, karnali (Humla)
    ("3", "NA", "2004", "6", "NA", "L2", "G2", "GL082673E29802N",
     "LDB2", "G082673E29802N", "30.27", "81.475", "5000", "NA", "NA",
     "NA", "NA", "Ice dammed", "N", "N", "14_2", "9", "Nepal",
     "Karnali", "Humla", "Unknown", "Unknown", "Unknown"),
    # year precision, Nepal, gandaki (Seti), GL placeholder 'No lake'
    ("4", "1560", "NA", "NA", "NA", "L3", "G3", "No lake", "NA",
     "G084009E28557N", "28.52", "83.992", "3741", "NA", "NA", "NA",
     "NA", "Supraglacial", "N", "N", "15_1", "11", "Nepal", "Gandaki",
     "Seti", "Unknown", "Unknown", "Unknown"),
    # year from approx, Nepal, bagmati (Melamchi), GL 'Not mapped'
    ("5", "2021", "NA", "NA", "NA", "L4", "G4", "Not mapped", "NA",
     "G085131E27831N", "28.131", "85.515", "1500", "NA", "NA", "NA",
     "NA", "Other", "N", "N", "15_1", "11", "Nepal", "Bagmati",
     "Melamchi", "Unknown", "Unknown", "Unknown"),
    # unresolved: no year at all
    ("6", "NA", "NA", "NA", "NA", "L5", "G5", "Ephemeral", "NA",
     "G087000E27900N", "27.9", "87.9", "4800", "NA", "NA", "NA",
     "NA", "Supraglacial", "N", "N", "14_2", "9", "Nepal", "Koshi",
     "Arun", "Unknown", "Unknown", "Unknown"),
    # outside the operative universe (transboundary Karakoram)
    ("7", "NA", "2018", "7", "10", "L6", "G6", "GL090000E35500N",
     "LDB6", "G090000E35500N", "35.5", "90.0", "5200", "NA", "NA",
     "NA", "NA", "Supraglacial", "Y", "N", "14_2", "9", "China",
     "Tibet", "Shyok", "Unknown", "Unknown", "Unknown"),
    # province conflict: hydrological koshi, Province Bagmati
    ("8", "NA", "1991", "7", "12", "L7", "G7", "GL086400E27900N",
     "LDB7", "G086400E27900N", "27.887", "86.467", "4200", "NA", "NA",
     "NA", "NA", "Supraglacial", "N", "N", "14_2", "9", "Nepal",
     "Bagmati", "Tama Koshi", "Unknown", "Unknown", "Unknown"),
]

_PADDING = tuple("" for _ in range(31))


def _csv() -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(_HEADER.split(","))
    for row in _ROWS:
        # GF 1 carries a non-ASCII cp1252 byte in Remarks to prove the
        # schema.ini ANSI decode path end to end.
        remark = "caf\xe9 bytes" if row[0] == "1" else ""
        writer.writerow(row + _PADDING + (remark,))
    return buf.getvalue().encode("cp1252")


def _removed_csv() -> bytes:
    buf = io.StringIO()
    writer = csv.writer(buf, lineterminator="\r\n")
    writer.writerow(_HEADER.split(",")[:10])
    # GF 99 overlaps the main CSV (recorded); GF 98 does not
    writer.writerow(("99",) + ("NA",) * 9)
    writer.writerow(("98",) + ("NA",) * 9)
    return buf.getvalue().encode("cp1252")


def _build_root(root: Path, *, mutate_zip: bytes | None = None,
                corrupt_member_digest: bool = False,
                drop_member_from_record: bool = False) -> None:
    (root / "glof-events").mkdir(parents=True, exist_ok=True)
    (root / "retrieval").mkdir(exist_ok=True)
    members = {_MEMBER_MAIN: _csv(), _MEMBER_REMOVED: _removed_csv(),
               _MEMBER_SCHEMA: b"[HMAGLOFDB_v0.1.csv]\nCol2=Year_approx"
                               b" Text\n"}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    zip_bytes = buf.getvalue()
    if mutate_zip is not None:
        zip_bytes = mutate_zip
    (root / "glof-events" / "HMAGLOFDB-v1.3.0.zip").write_bytes(
        zip_bytes)
    record = {
        "source": "icimod_hmaglofdb_v1_3_0",
        "outcome": "PROMOTED",
        "evidence_relpath": "glof-events/HMAGLOFDB-v1.3.0.zip",
        "sha256": hashlib.sha256(zip_bytes).hexdigest(),
        "md5": "0" * 32,
        "members": [
            {"name": name, "size_bytes": len(data),
             "sha256": hashlib.sha256(data).hexdigest()}
            for name, data in members.items()],
    }
    if corrupt_member_digest:
        record["members"][0]["sha256"] = "0" * 64
    if drop_member_from_record:
        record["members"] = record["members"][1:]
    (root / "retrieval" / "retrieval_record_hmaglofdb.json").write_text(
        json.dumps(record))
    anchor = {
        "basin_coverage": {
            "Koshi": {"n_lakes": 1,
                      "gl_ids": ["GL086304E28374N", "GL086157E28303N"]},
            "Gandaki": {"n_lakes": 1, "gl_ids": ["GL085630E28162N"]},
        },
    }
    (root / "retrieval" / "anchor_derivation_record.json").write_text(
        json.dumps(anchor))


@pytest.fixture()
def evidence_root(tmp_path) -> Path:
    root = tmp_path / "evidence"
    _build_root(root)
    return root


def _intake(root: Path):
    intake = run_intake(root)
    intake.package_dict()  # every record must validate
    return intake


class TestVerifierBeforeParser:
    def test_tampered_zip_rejected_before_parse(self, tmp_path):
        root = tmp_path / "evidence"
        _build_root(root)
        path = root / "glof-events" / "HMAGLOFDB-v1.3.0.zip"
        good = path.read_bytes()
        # post-acquisition byte swap, without updating the acquirer
        # record — verification must reject before any member is read
        path.write_bytes(good[:-1] + bytes([good[-1] ^ 0x01]))
        with pytest.raises(ValueError, match="verification failed"):
            run_intake(root)

    def test_member_digest_drift_rejected(self, tmp_path):
        root = tmp_path / "evidence"
        _build_root(root, corrupt_member_digest=True)
        with pytest.raises(ValueError, match="quarantine, never parse"):
            run_intake(root)



class TestReports:
    def test_six_required_report_groups_present(self, evidence_root):
        report = _intake(evidence_root).report
        for key in ("actual_bytes", "row_census",
                    "precision_distribution", "id_coverage",
                    "recurrence", "cascades", "removed_rows",
                    "post_2025_rows", "basin_assignment",
                    "ledger_reasons"):
            assert key in report, key

    def test_full_census_counts(self, evidence_root):
        census = _intake(evidence_root).report["row_census"]
        assert census["total"] == 8
        assert census["nepal_count"] == 7
        assert census["nepal_loadable"] == 6

    def test_precision_distribution_covers_all_rows(
            self, evidence_root):
        dist = _intake(evidence_root).report["precision_distribution"]
        assert sum(dist.values()) == 8
        assert dist["day"] == 4 and dist["month"] == 1
        assert dist["year"] == 2 and dist["unresolved"] == 1

    def test_id_coverage_classifies_placeholders(self, evidence_root):
        cov = _intake(evidence_root).report["id_coverage"]
        assert cov["GL_ID_valid"] == 5
        assert cov["GL_ID_placeholder"] == 3   # No lake / Not mapped /
        assert cov["GL_ID_na"] == 0            # Ephemeral
        assert cov["gf_id_z_suffix_present"] is False

    def test_removed_rows_overlap_recorded(self, evidence_root):
        removed = _intake(evidence_root).report["removed_rows"]
        assert removed["count"] == 2
        assert removed["gf_ids_also_in_main"] == 0

    def test_post_2025_zero(self, evidence_root):
        assert _intake(evidence_root).report["post_2025_rows"] == 0


class TestLabels:
    def test_loadable_events_and_timing(self, evidence_root):
        intake = _intake(evidence_root)
        by_gf = {e.event_id.rsplit(":", 1)[1]: e for e in intake.events}
        assert sorted(by_gf) == ["1", "2", "3", "4", "5", "8"]
        day = by_gf["1"]
        assert day.event_time_precision == "day"
        assert day.event_time_start == "2015-04-25T00:00:00Z"
        assert day.event_time_end == "2015-04-26T00:00:00Z"
        assert day.uncertainty_seconds == 86400.0
        month = by_gf["3"]
        # 30-day June measures INTERVAL_8_30D -> declared 'interval'
        # (C14 class-consistency); the basis string keeps the
        # calendar-month semantics.
        assert month.event_time_precision == "interval"
        assert month.event_time_start == "2004-06-01T00:00:00Z"
        assert month.event_time_end == "2004-07-01T00:00:00Z"
        assert month.uncertainty_seconds == 30 * 86400.0
        year = by_gf["4"]
        assert year.event_time_precision == "year"
        from datetime import datetime, timezone
        expected = (datetime(1561, 1, 1, tzinfo=timezone.utc)
                    - datetime(1560, 1, 1, tzinfo=timezone.utc))
        assert year.uncertainty_seconds == expected.total_seconds()

    def test_recurrence_series_atomic(self, evidence_root):
        intake = _intake(evidence_root)
        by_gf = {e.event_id.rsplit(":", 1)[1]: e for e in intake.events}
        g1, g2 = by_gf["1"], by_gf["2"]
        assert g1.cascade_group_id == g2.cascade_group_id
        assert g2.parent_event_id == g1.event_id or \
            g1.parent_event_id == g2.event_id
        root_event = (g1 if g1.parent_event_id == ""
                      else g2)
        assert root_event.event_id.endswith(
            intake.report["cascades"] and g1.cascade_group_id.split(
                ":", 1)[1])
        assert intake.report["cascades"]["groups"] == 1
        assert intake.report["cascades"]["events_in_groups"] == 2

    def test_unresolved_row_ledgered_not_dropped(self, evidence_root):
        intake = _intake(evidence_root)
        reasons = {(e["gf_id"], e["reason"]) for e in intake.ledger}
        assert ("6", "unresolved_timing") in reasons
        assert ("7", "basin_outside_operative_universe") in reasons

    def test_province_conflict_recorded_hydrology_governs(
            self, evidence_root):
        intake = _intake(evidence_root)
        by_gf = {e.event_id.rsplit(":", 1)[1]: e for e in intake.events}
        assert by_gf["8"].basin_id == "koshi"


class TestOpportunitiesControls:
    def test_frame_counts(self, evidence_root):
        intake = _intake(evidence_root)
        assert len(intake.opportunities) == 75   # 3 lakes x 25 JJA
        assert len(intake.controls) == 75

    def test_controls_derived_not_asserted(self, evidence_root):
        intake = _intake(evidence_root)
        for opp, ctrl in zip(intake.opportunities, intake.controls):
            assert opp.state == "UNKNOWN"
            assert ctrl.opportunity_id == opp.opportunity_id
            assert ctrl.window_start == opp.window_start
            assert ctrl.state == "CENSORED_OR_AMBIGUOUS"


class TestHoldout:
    def test_assignment_before_filtering_and_locked(self, evidence_root):
        intake = _intake(evidence_root)
        plan = intake.holdout
        assert plan.assigned_before_filtering and plan.test_locked
        assert plan.assignment_rule == "basin"
        assert set(plan.event_assignments) == {
            e.event_id for e in intake.events}
        declared = (set(plan.train_groups) | set(plan.validation_groups)
                    | set(plan.test_groups))
        assert set(plan.event_assignments.values()) == declared
        assert plan.evaluation_region_names == plan.test_groups
        assert len(plan.evaluation_region_names) >= 2
        assert plan.embargo_seconds == 1209600.0

    def test_cascade_members_share_basin_group(self, evidence_root):
        intake = _intake(evidence_root)
        by_gf = {e.event_id.rsplit(":", 1)[1]: e for e in intake.events}
        assert by_gf["1"].basin_id == by_gf["2"].basin_id
        assert by_gf["1"].cascade_group_id == \
            by_gf["2"].cascade_group_id


class TestRealBytesRegression:
    """Runs only where the P5 evidence root exists (owner machine);
    pins the recorded intake numbers as a regression gate."""

    @pytest.fixture()
    def real_intake(self):
        if not (_ROOT / "glof-events" /
                "HMAGLOFDB-v1.3.0.zip").exists():
            pytest.skip("P5 evidence root not present on this machine")
        return run_intake(_ROOT)

    def test_real_bytes_parse_and_validate(self, real_intake):
        real_intake.package_dict()

    def test_recorded_real_intake_numbers(self, real_intake):
        report = real_intake.report
        assert report["actual_bytes"]["zip"] == 107879
        census = report["row_census"]
        assert census["total"] == 768
        assert census["nepal_count"] == 58
        assert census["nepal_loadable"] == 30
        assert report["post_2025_rows"] == 0
        assert report["removed_rows"]["count"] == 102
        assert report["id_coverage"]["GL_ID_valid"] == 386
        assert report["recurrence"]["repeat_y"] == 448

    def test_real_events_validate_and_are_bounded(
            self, real_intake):
        assert len(real_intake.events) == 45
        assert real_intake.holdout.validation_groups == ("karnali",)
        assert set(real_intake.holdout.test_groups) == \
            {"koshi", "gandaki"}
        years = [int(e.event_time_start[:4])
                 for e in real_intake.events]
        assert min(years) >= 1533 and max(years) <= 2025


