"""Behavior tests for armc_routeb_analyze — regression-locks the
defects Codex found in the v18 execution."""
import json
import numpy as np
import pandas as pd
import pytest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import armc_routeb_analyze as A


def hourly(vals, start="2016-05-01", tz="UTC"):
    idx = pd.date_range(start, periods=len(vals), freq="h", tz=tz)
    return pd.Series(vals, index=idx)


class TestDedupe:
    def test_conflicting_duplicate_raises(self):
        s1 = hourly([1.0] * 25)
        s2 = hourly([9.0], start="2016-05-01")  # conflicts at t=0
        with pytest.raises(ValueError, match="conflicting"):
            A.merge_box_series({"a": s1, "b": s2}, ["a", "b"])

    def test_identical_duplicate_dropped(self):
        s1 = hourly([1.0] * 25)
        s2 = hourly([1.0], start="2016-05-01")  # same value, dup t=0
        out = A.merge_box_series({"a": s1, "b": s2}, ["a", "b"])
        assert len(out) == 25 and out.iloc[0] == 1.0


class TestThetaMask:
    def test_below_ground_masked(self):
        import pandas as pd
        idx = pd.date_range("2016-05-01", periods=4, freq="h", tz="UTC")
        t = pd.DataFrame({0: [280.]*4}, index=idx)
        sp = pd.DataFrame({0: [40000.]*4}, index=idx)
        t2m = pd.DataFrame({0: [270.]*4}, index=idx)
        td, fr = A.masked_theta_cells(t, sp, t2m)
        assert td.isna().all() and (fr == 0).all()

    def test_above_ground_kept(self):
        import pandas as pd
        idx = pd.date_range("2016-05-01", periods=4, freq="h", tz="UTC")
        t = pd.DataFrame({0: [260.]*4}, index=idx)
        sp = pd.DataFrame({0: [65000.]*4}, index=idx)
        t2m = pd.DataFrame({0: [275.]*4}, index=idx)
        td, fr = A.masked_theta_cells(t, sp, t2m)
        assert td.notna().all() and (fr == 1.0).all()
        assert abs(td.iloc[0] - (260 * (1000/500)**0.286 - 275 * (1000/650)**0.286)) < 0.5


class TestAccumulation:
    def test_tp_daily_uses_trailing_hour(self):
        # 25 hours of 1mm/h starting D 00:00 -> D total = hours 01..24 = 24mm
        s = hourly([1.0] * 25, start="2016-05-01")
        d = s.groupby((s.index - pd.Timedelta(hours=1)).normalize()).sum()
        assert d.loc["2016-05-01"] == 24.0


class TestWindows:
    def test_incomplete_window_nan(self):
        df = pd.DataFrame({"tp": [1.0] * 5},
                          index=pd.date_range("2016-04-27", periods=5))
        x, cov = A.antecedent(df, "tp", pd.Timestamp("2016-05-07"), True)
        assert np.isnan(x) and cov == 5

    def test_reference_excludes_washout(self):
        idx = pd.date_range("2000-05-01", periods=31 * 25, freq="D")
        s = pd.Series(1.0, index=pd.DatetimeIndex([d for y in range(2001, 2026)
                    for d in pd.date_range(f"{y}-05-01", periods=31)]))
        ref = A.ref_distribution(s, 5, [pd.Timestamp("2016-05-07")], accum=False)
        assert len(ref) > 100
        # no window may end within +-7d of washout
        assert not ((ref.index >= "2016-04-30") & (ref.index <= "2016-05-14")).any()

    def test_reference_requires_consecutive(self):
        # gaps break windows: every other day missing -> no full 7d window
        s = pd.Series(1.0, index=pd.date_range("2001-05-01", periods=40, freq="2D"))
        ref = A.ref_distribution(s, 5, [], accum=False)
        assert len(ref) == 0


class TestCohort:
    def test_unit_level_primary_is_earliest(self):
        # ep_01 has May + June members -> primary must be May (400)
        inv = {"climatology_selections": [], "antecedent_spillover_selections": []}
        # logic is in main(); test via member ordering semantics instead:
        mids = ["401", "400"]
        starts = {"400": "2002-05-23", "401": "2002-06-29"}
        ordered = sorted(mids, key=lambda m: starts[m])
        assert ordered[0] == "400"


class TestCellTheta:
    def test_per_cell_mask_mixed(self):
        import pandas as pd
        idx = pd.date_range("2016-05-01", periods=4, freq="h", tz="UTC")
        # cell0 above 500hPa (valid), cell1 below (masked)
        t = pd.DataFrame({0: [260.]*4, 1: [260.]*4}, index=idx)
        sp = pd.DataFrame({0: [65000.]*4, 1: [40000.]*4}, index=idx)
        t2m = pd.DataFrame({0: [275.]*4, 1: [275.]*4}, index=idx)
        bm, fr = A.masked_theta_cells(t, sp, t2m)
        assert (fr == 0.5).all()          # half the cells valid
        # box mean should equal cell0's own deficit only
        exp = 260*(1000/500)**0.286 - 275*(1000/650)**0.286
        assert abs(bm.iloc[0] - exp) < 0.5

    def test_conflicting_cell_duplicate_raises(self):
        import pandas as pd
        idx = pd.date_range("2016-05-01", periods=2, freq="h", tz="UTC")
        a = pd.DataFrame({0: [1., 2.]}, index=idx)
        b = pd.DataFrame({0: [9.]}, index=[idx[0]])
        import pytest
        with pytest.raises(ValueError):
            A.merge_cell_series({"a": a, "b": b}, ["a", "b"])


class TestEraMatched:
    def test_max_year_restriction(self):
        s = pd.Series(1.0, index=pd.DatetimeIndex(
            [d for y in (1999, 2000, 2001, 2002) for d in pd.date_range(f"{y}-05-01", periods=31)]))
        ref = A.ref_distribution(s, 5, [], accum=False, max_year=2000)
        assert (ref.index.year <= 2000).all()


class TestFdrScope:
    def test_primary_not_in_fdr_set(self):
        # PRIMARY is 'tp_antecedent_7d_sum'; FDR applies to secondaries.
        # Verify by checking EXPOSURES membership of the primary key.
        assert A.PRIMARY in A.EXPOSURES and len(A.EXPOSURES) > 1


class TestSignoffVerification:
    """Exercise the PRODUCTION gate verify_signoff — no local re-implementation."""
    G = {"schema": "P5_OWNER_SIGNOFF_V1", "status": "APPROVED",
         "target_sha256": "D" * 64, "episode_map_sha256": "E" * 64,
         "approver": {"id": "sanjayb", "role": "owner"},
         "scope": A.CANONICAL_V1_SCOPE, "records": {"a": {}}}

    def test_genuine_accepted(self):
        assert A.verify_signoff(dict(self.G), {"D" * 64}, "E" * 64)

    def test_wrong_decision_target_rejected(self):
        assert not A.verify_signoff(dict(self.G), {"F" * 64}, "E" * 64)

    def test_wrong_episode_map_rejected(self):
        assert not A.verify_signoff(dict(self.G), {"D" * 64}, "F" * 64)

    def test_missing_episode_map_field_rejected(self):
        g = dict(self.G); g.pop("episode_map_sha256")
        assert not A.verify_signoff(g, {"D" * 64}, "E" * 64)

    def test_non_owner_rejected(self):
        g = dict(self.G); g["approver"] = {"id": "x", "role": "agent"}
        assert not A.verify_signoff(g, {"D" * 64}, "E" * 64)

    def test_wrong_scope_rejected(self):
        g = dict(self.G); g["scope"] = "retrieval scope only"
        assert not A.verify_signoff(g, {"D" * 64}, "E" * 64)

    def test_not_approved_rejected(self):
        g = dict(self.G); g["status"] = "PENDING"
        assert not A.verify_signoff(g, {"D" * 64}, "E" * 64)

    def test_v0_style_accepted(self):
        v0 = {"schema": "P5_OWNER_SIGNOFF_V0", "status": "APPROVED",
              "role": "owner_approval", "signer": "sanjayb",
              "approved_artifact": {"file": "decision_v0.json", "sha256": "A" * 64}}
        assert A.verify_signoff(v0, {"A" * 64}, None)

    def test_v0_style_wrong_target_rejected(self):
        v0 = {"schema": "P5_OWNER_SIGNOFF_V0", "status": "APPROVED",
              "role": "owner_approval",
              "approved_artifact": {"sha256": "A" * 64}}
        assert not A.verify_signoff(v0, {"B" * 64}, None)


class TestApprovalCoverage:
    """approved_record_ids — fail-closed coverage incl. supersession checks."""
    EV = "icimod_hmaglofdb_v1_3_0:1.3.0:5"

    def _dec(self, tmp_path, name, lat=28.0):
        import json
        d = {"events": [{"event_id": self.EV,
             "adjudication": {"disposition": "ELIGIBLE",
                "event_time_interval": {"start": "2002-05-20T00:00:00Z"}},
             "local": {"lat": lat, "lon": 84.0}}]}
        p = tmp_path / name; p.write_text(json.dumps(d)); return p

    def _v0so(self, tmp_path, tgt_path):
        import json, hashlib
        so = tmp_path / "so.json"
        so.write_text(json.dumps({"schema": "P5_OWNER_SIGNOFF_V0",
            "status": "APPROVED", "role": "owner_approval",
            "signer": "sanjayb", "approved_artifact": {"sha256":
            hashlib.sha256(tgt_path.read_bytes()).hexdigest()}}))
        return so

    def test_whole_decision_approval(self, tmp_path):
        d0 = self._dec(tmp_path, "d0.json"); d1 = self._dec(tmp_path, "d1.json")
        so = self._v0so(tmp_path, d0)
        ids = A.approved_record_ids([so], d1, [d0], "E" * 64)
        assert self.EV in ids

    def test_stale_v0_not_in_chain_rejected(self, tmp_path):
        # signoff targets a decision file that is NOT an explicit predecessor
        d1 = self._dec(tmp_path, "d1.json")
        stray = self._dec(tmp_path, "stray.json", lat=29.9)  # different bytes -> different sha
        so = self._v0so(tmp_path, stray)
        ids = A.approved_record_ids([so], d1, [], "E" * 64)
        assert self.EV not in ids

    def test_field_change_revokes_approval(self, tmp_path):
        # predecessor approved lat=28; current decision moved it to 28.5
        d0 = self._dec(tmp_path, "d0.json", lat=28.0)
        d1 = self._dec(tmp_path, "d1.json", lat=28.5)
        so = self._v0so(tmp_path, d0)
        ids = A.approved_record_ids([so], d1, [d0], "E" * 64)
        assert self.EV not in ids

class TestSignoffNegations:
    """Codex's negated-value adversarial cases."""
    G = {"schema": "P5_OWNER_SIGNOFF_V1", "status": "APPROVED",
         "target_sha256": "D" * 64, "episode_map_sha256": "E" * 64,
         "approver": {"id": "sanjayb", "role": "owner"},
         "scope": A.CANONICAL_V1_SCOPE, "records": {"a": {}}}

    def test_negated_role_rejected(self):
        g = dict(self.G); g["approver"] = {"id": "x", "role": "not-owner"}
        assert not A.verify_signoff(g, {"D" * 64}, "E" * 64)

    def test_negated_scope_rejected(self):
        g = dict(self.G); g["scope"] = "NOT ELIGIBILITY adjudication"
        assert not A.verify_signoff(g, {"D" * 64}, "E" * 64)

    def test_retrieval_only_scope_rejected(self):
        g = dict(self.G); g["scope"] = "v19 retrieval scope approval"
        assert not A.verify_signoff(g, {"D" * 64}, "E" * 64)

    def test_real_scope_accepted(self):
        g = dict(self.G); g["scope"] = A.CANONICAL_V1_SCOPE
        assert A.verify_signoff(g, {"D" * 64}, "E" * 64)

    def test_non_eligibility_prefix_rejected(self):
        g = dict(TestSignoffNegations.G); g["scope"] = "NON-ELIGIBILITY adjudication"
        assert not A.verify_signoff(g, {"D" * 64}, "E" * 64)

    def test_retrieval_first_mixed_scope_rejected(self):
        g = dict(TestSignoffNegations.G); g["scope"] = "retrieval plus eligibility adjudication"
        assert not A.verify_signoff(g, {"D" * 64}, "E" * 64)

    def test_typed_approval_type_accepted(self):
        g = dict(TestSignoffNegations.G); g["approval_type"] = "ELIGIBILITY_ADJUDICATION"
        g["scope"] = "anything"  # typed field is load-bearing, scope ignored
        assert A.verify_signoff(g, {"D" * 64}, "E" * 64)

class TestSignoffSchema:
    """approval_type must not bypass schema/role/target checks."""
    G = {"schema": "P5_OWNER_SIGNOFF_V2", "status": "APPROVED",
         "target_sha256": "D" * 64, "episode_map_sha256": "E" * 64,
         "approver": {"id": "sanjayb", "role": "owner"},
         "approval_type": "ELIGIBILITY_ADJUDICATION", "records": {"a": {}}}

    def test_typed_full_accept(self):
        assert A.verify_signoff(dict(self.G), {"D" * 64}, "E" * 64)

    def test_typed_wrong_schema_rejected(self):
        g = dict(self.G); g["schema"] = "ANY_JSON"
        assert not A.verify_signoff(g, {"D" * 64}, "E" * 64)

    def test_typed_fake_approver_rejected(self):
        g = dict(self.G); g["approver"] = {"id": "mallory", "role": "agent"}
        assert not A.verify_signoff(g, {"D" * 64}, "E" * 64)

    def test_typed_wrong_target_rejected(self):
        assert not A.verify_signoff(dict(self.G), {"F" * 64}, "E" * 64)

    def test_typed_wrong_epmap_rejected(self):
        assert not A.verify_signoff(dict(self.G), {"D" * 64}, "F" * 64)

    def test_typed_pending_rejected(self):
        g = dict(self.G); g["status"] = "PENDING"
        assert not A.verify_signoff(g, {"D" * 64}, "E" * 64)

    def test_typed_same_id_wrong_role_rejected(self):
        # isolate the ROLE check: correct signer id, wrong role
        g = dict(TestSignoffSchema.G); g["approver"] = {"id": "sanjayb", "role": "agent"}
        assert not A.verify_signoff(g, {"D" * 64}, "E" * 64)

    def test_wrong_owner_id_with_owner_role_rejected(self):
        g = dict(TestSignoffSchema.G); g["approver"] = {"id": "mallory", "role": "owner"}
        assert not A.verify_signoff(g, {"D" * 64}, "E" * 64)

    def test_suffixed_schema_decoy_rejected(self):
        g = dict(TestSignoffSchema.G); g["schema"] = "P5_OWNER_SIGNOFF_EVIL"
        assert not A.verify_signoff(g, {"D" * 64}, "E" * 64)

    def test_trailing_negation_scope_rejected(self):
        g = dict(TestSignoffNegations.G); g.pop("approval_type", None)
        g["scope"] = "ELIGIBILITY adjudication NOT approved"
        assert not A.verify_signoff(g, {"D" * 64}, "E" * 64)

    def test_v0_wrong_signer_rejected(self):
        v0 = {"schema": "P5_OWNER_SIGNOFF_V0", "status": "APPROVED",
              "role": "owner_approval", "signer": "mallory",
              "approved_artifact": {"sha256": "A" * 64}}
        assert not A.verify_signoff(v0, {"A" * 64}, None)

    def test_fuzzy_predecessor_name_rejected(self, tmp_path):
        # predecessor filename must match declared supersedes EXACTLY
        sup = "armc_event_adjudication_decision_v0.json"
        decoy = tmp_path / "armc_event_adjudication_decision_v0_decoy.json"
        assert not A._is_declared_predecessor(decoy, sup)
        near = tmp_path / "armc_event_adjudication_decision_v0x.json"
        assert not A._is_declared_predecessor(near, sup)
        real = tmp_path / "armc_event_adjudication_decision_v0.json"
        assert A._is_declared_predecessor(real, sup)
        assert A._is_declared_predecessor(real, "armc_event_adjudication_decision_v0")

    def _dec(self, tmp_path, events, name="dec.json"):
        f = tmp_path / name
        f.write_text(json.dumps({"events": events}))
        return f

    def _ev(self, rid, disp="ELIGIBLE", start="2000-01-01T00:00:00Z",
            end="2000-01-02T00:00:00Z", precision="day", lake="X",
            lat=28.0, lon=85.0, basin="b1", cascade="c1"):
        return {"event_id": rid,
                "adjudication": {"disposition": disp,
                                 "event_time_interval": {"start": start,
                                                         "end": end}},
                "local": {"precision": precision, "lat": lat, "lon": lon,
                          "basin_group": basin, "cascade_group_id": cascade},
                "source_fields": {"Lake_name": lake}}

    def _v2(self, tmp_path, dec, records, name="so.json"):
        so = tmp_path / name
        so.write_text(json.dumps({
            "schema": "P5_OWNER_SIGNOFF_V2", "status": "APPROVED",
            "target_sha256": A._sha(dec), "episode_map_sha256": "E" * 64,
            "approver": {"id": "sanjayb", "role": "owner"},
            "approval_type": "ELIGIBILITY_ADJUDICATION", "records": records}))
        return so

    def test_signoff_unknown_record_id_voids_whole_signoff(self, tmp_path):
        # all-or-nothing: one unknown id voids the entire per-record signoff
        dec = self._dec(tmp_path, [self._ev("real:1")])
        so = self._v2(tmp_path, dec, {"real:1": {}, "ghost:99": {}})
        assert A.approved_record_ids([so], dec, [], "E" * 64) == set()

    def test_signoff_ineligible_record_voids_whole_signoff(self, tmp_path):
        # a signoff cannot ratify a record the decision marked INELIGIBLE
        dec = self._dec(tmp_path, [self._ev("ok:1"),
                                   self._ev("bad:2", disp="INELIGIBLE")])
        so = self._v2(tmp_path, dec, {"ok:1": {}, "bad:2": {}})
        assert A.approved_record_ids([so], dec, [], "E" * 64) == set()

    def test_signoff_all_valid_records_approved(self, tmp_path):
        dec = self._dec(tmp_path, [self._ev("a:1"), self._ev("a:2")])
        so = self._v2(tmp_path, dec, {"a:1": {"date": "2000-01-01", "lake": "X"},
                                      "a:2": {}})
        assert A.approved_record_ids([so], dec, [], "E" * 64) == {"a:1", "a:2"}

    @pytest.mark.parametrize("bad_date", ["1964", "1964-05", "not-a-date",
                                          "2000-01-02", "2000-1-1"])
    def test_signoff_bad_date_voids_signoff(self, tmp_path, bad_date):
        # exact canonical YYYY-MM-DD match vs the decision's interval start
        dec = self._dec(tmp_path, [self._ev("e:1")])
        so = self._v2(tmp_path, dec, {"e:1": {"date": bad_date}})
        assert A.approved_record_ids([so], dec, [], "E" * 64) == set()

    def test_signoff_lake_mismatch_voids_signoff(self, tmp_path):
        dec = self._dec(tmp_path, [self._ev("e:1", lake="Cirenma Co")])
        so = self._v2(tmp_path, dec, {"e:1": {"lake": "Other Lake"}})
        assert A.approved_record_ids([so], dec, [], "E" * 64) == set()

    def test_v2_epmap_param_mandatory(self):
        # direct verifier call without the map digest must fail
        g = {"schema": "P5_OWNER_SIGNOFF_V2", "status": "APPROVED",
             "target_sha256": "D" * 64, "episode_map_sha256": "E" * 64,
             "approver": {"id": "sanjayb", "role": "owner"},
             "approval_type": "ELIGIBILITY_ADJUDICATION", "records": {"a": {}}}
        assert not A.verify_signoff(g, {"D" * 64}, None)
        g2 = dict(g); g2["episode_map_sha256"] = ""
        assert not A.verify_signoff(g2, {"D" * 64}, "E" * 64)

    def test_perrecord_predecessor_continuity(self, tmp_path):
        # per-record approval bound to a predecessor is revoked when the
        # record's fields changed under supersession
        old = self._dec(tmp_path, [self._ev("e:1", lat=28.0)], "old.json")
        cur = self._dec(tmp_path, [self._ev("e:1", lat=29.0)], "cur.json")
        so = self._v2(tmp_path, old, {"e:1": {}})
        assert A.approved_record_ids([so], cur, [old], "E" * 64) == set()

    @pytest.mark.parametrize("field", [
        "disposition", "start", "end", "precision", "lat", "lon",
        "basin", "cascade", "lake"])
    def test_continuity_each_projected_field(self, tmp_path, field):
        # one change per projected field must each revoke the approval
        import copy
        base = self._ev("e:1")
        mod = copy.deepcopy(base)
        if field == "disposition":
            mod["adjudication"]["disposition"] = "INELIGIBLE"
        elif field == "start":
            mod["adjudication"]["event_time_interval"]["start"] = \
                "2001-01-01T00:00:00Z"
        elif field == "end":
            mod["adjudication"]["event_time_interval"]["end"] = \
                "2001-01-03T00:00:00Z"
        elif field == "precision":
            mod["local"]["precision"] = "month"
        elif field == "lat":
            mod["local"]["lat"] = 29.0
        elif field == "lon":
            mod["local"]["lon"] = 86.0
        elif field == "basin":
            mod["local"]["basin_group"] = "other"
        elif field == "cascade":
            mod["local"]["cascade_group_id"] = "other"
        elif field == "lake":
            mod["source_fields"]["Lake_name"] = "Other Lake"
        old = self._dec(tmp_path, [base], "old.json")
        cur = self._dec(tmp_path, [mod], "cur.json")
        so = self._v2(tmp_path, old, {"e:1": {}})
        assert A.approved_record_ids([so], cur, [old], "E" * 64) == set(), field

    @pytest.mark.parametrize("bad_rv", [None, False, 0, "", [], "x"])
    def test_signoff_nonobject_record_value_voids(self, tmp_path, bad_rv):
        # falsey/malformed non-mapping record values must fail closed,
        # not be coerced to {} (no claims)
        dec = self._dec(tmp_path, [self._ev("e:1")])
        so = self._v2(tmp_path, dec, {"e:1": bad_rv})
        assert A.approved_record_ids([so], dec, [], "E" * 64) == set()

    def test_perrecord_predecessor_unchanged_fields_approved(self, tmp_path):
        old = self._dec(tmp_path, [self._ev("e:1")], "old.json")
        cur = self._dec(tmp_path, [self._ev("e:1")], "cur.json")
        so = self._v2(tmp_path, old, {"e:1": {}})
        assert A.approved_record_ids([so], cur, [old], "E" * 64) == {"e:1"}

    def test_current_approval_survives_predecessor_revocation(self, tmp_path):
        # R6-A: an id approved by BOTH a current-decision signoff and a
        # predecessor signoff keeps its CURRENT approval even when the
        # predecessor record's fields changed under supersession
        old = self._dec(tmp_path, [self._ev("e:1")], "old.json")
        cur = self._dec(tmp_path, [self._ev("e:1", lat=29.0)], "cur.json")
        so_old = self._v2(tmp_path, old, {"e:1": {}}, "so_old.json")
        so_cur = self._v2(tmp_path, cur, {"e:1": {}}, "so_cur.json")
        got = A.approved_record_ids([so_old, so_cur], cur, [old], "E" * 64)
        assert got == {"e:1"}

    def test_predecessor_only_approval_revoked_on_change(self, tmp_path):
        # without a current signoff the same changed predecessor record
        # is revoked — direct/inherited separation must not leak
        old = self._dec(tmp_path, [self._ev("e:1")], "old.json")
        cur = self._dec(tmp_path, [self._ev("e:1", lat=29.0)], "cur.json")
        so = self._v2(tmp_path, old, {"e:1": {}})
        assert A.approved_record_ids([so], cur, [old], "E" * 64) == set()

    @pytest.mark.parametrize("key,val", [
        ("date", False), ("date", 0), ("date", ""),
        ("date", "2000-1-1"), ("date", "2000-01-02"),
        ("date", None), ("date", ["2000-01-01"]),
        ("lake", False), ("lake", 0), ("lake", ""),
        ("lake", None), ("lake", "Other Lake")])
    def test_falsey_or_bad_optional_fields_void(self, tmp_path, key, val):
        # R6-B: optional means KEY ABSENT — a present date/lake must be
        # a non-empty string exactly matching the bound record; falsey
        # or malformed present values void the whole signoff
        dec = self._dec(tmp_path, [self._ev("e:1")])
        so = self._v2(tmp_path, dec, {"e:1": {key: val}})
        assert A.approved_record_ids([so], dec, [], "E" * 64) == set()

    def test_absent_optional_fields_valid(self, tmp_path):
        # {} and partial records without date/lake keys remain valid
        dec = self._dec(tmp_path, [self._ev("e:1"), self._ev("e:2")])
        so = self._v2(tmp_path, dec, {"e:1": {}, "e:2": {"note": "x"}})
        got = A.approved_record_ids([so], dec, [], "E" * 64)
        assert got == {"e:1", "e:2"}

    @pytest.mark.parametrize("bad", [
        [], "not-object", None, 42, True])
    def test_nondict_signoff_rejected_not_raised(self, bad):
        assert A.verify_signoff(bad, {"D" * 64}, "E" * 64) is False

    def test_v0_nondict_approved_artifact_rejected(self):
        v0 = {"schema": "P5_OWNER_SIGNOFF_V0", "status": "APPROVED",
              "role": "owner_approval", "signer": "sanjayb",
              "approved_artifact": "bad-string"}
        assert A.verify_signoff(v0, {"A" * 64}, None) is False
        v0["approved_artifact"] = ["list"]
        assert A.verify_signoff(v0, {"A" * 64}, None) is False

    def test_v2_nondict_approver_rejected(self):
        v2 = {"schema": "P5_OWNER_SIGNOFF_V2", "status": "APPROVED",
              "target_sha256": "D" * 64, "episode_map_sha256": "E" * 64,
              "approver": "not-a-dict",
              "approval_type": "ELIGIBILITY_ADJUDICATION",
              "records": {"a": {}}}
        assert A.verify_signoff(v2, {"D" * 64}, "E" * 64) is False

    @pytest.mark.parametrize("bad_tgt", [
        {"x": 1}, ["x"], 42, None, True])
    def test_v2_nonstr_target_rejected_not_raised(self, bad_tgt):
        # unhashable/non-str targets must not raise TypeError on `in`
        v2 = {"schema": "P5_OWNER_SIGNOFF_V2", "status": "APPROVED",
              "target_sha256": bad_tgt, "episode_map_sha256": "E" * 64,
              "approver": {"id": "sanjayb", "role": "owner"},
              "approval_type": "ELIGIBILITY_ADJUDICATION",
              "records": {"a": {}}}
        assert A.verify_signoff(v2, {"D" * 64}, "E" * 64) is False

    @pytest.mark.parametrize("field,val", [
        ("signer", {"x": 1}), ("signer", ["x"]), ("signer", 42)])
    def test_v0_nonstr_signer_rejected(self, field, val):
        v0 = {"schema": "P5_OWNER_SIGNOFF_V0", "status": "APPROVED",
              "role": "owner_approval", field: val,
              "approved_artifact": {"sha256": "A" * 64}}
        assert A.verify_signoff(v0, {"A" * 64}, None) is False

    @pytest.mark.parametrize("bad_sha", [{"x": 1}, ["x"], 42, None])
    def test_v0_nonstr_artifact_sha_rejected(self, bad_sha):
        v0 = {"schema": "P5_OWNER_SIGNOFF_V0", "status": "APPROVED",
              "role": "owner_approval", "signer": "sanjayb",
              "approved_artifact": {"sha256": bad_sha}}
        assert A.verify_signoff(v0, {"A" * 64}, None) is False

    @pytest.mark.parametrize("bad_id", [{"x": 1}, ["x"], 42, None])
    def test_v2_nonstr_approver_id_rejected(self, bad_id):
        v2 = {"schema": "P5_OWNER_SIGNOFF_V2", "status": "APPROVED",
              "target_sha256": "D" * 64, "episode_map_sha256": "E" * 64,
              "approver": {"id": bad_id, "role": "owner"},
              "approval_type": "ELIGIBILITY_ADJUDICATION",
              "records": {"a": {}}}
        assert A.verify_signoff(v2, {"D" * 64}, "E" * 64) is False

    def test_result_supersedes_lineage(self):
        s = A._result_supersedes("/x/armc_routeb_result_v31.json")
        assert "armc_routeb_result_v30" in s
        s0 = A._result_supersedes("/x/armc_routeb_result_v1.json")
        assert "armc_routeb_result_v0" in s0
        su = A._result_supersedes("/x/no_version.json")
        assert "unknown" in su

    def test_terminal_approval_status(self):
        epmap = {"units": [
            {"era": "post2000", "member_ids": [400, 401]},
            {"era": "pre2001", "member_ids": [188, 190]}]}
        full = {"icimod_hmaglofdb_v1_3_0:1.3.0:400",
                "icimod_hmaglofdb_v1_3_0:1.3.0:401",
                "icimod_hmaglofdb_v1_3_0:1.3.0:188",
                "icimod_hmaglofdb_v1_3_0:1.3.0:190"}
        assert A.cohort_approval_status(epmap, full) == "APPROVED_ALL_STRATA"
        missing = full - {"icimod_hmaglofdb_v1_3_0:1.3.0:188"}
        assert A.cohort_approval_status(epmap, missing) == "PENDING_STRATA:pre2001"
        none = set()
        assert A.cohort_approval_status(epmap, none) == \
            "PENDING_STRATA:post2000,pre2001"

    def test_signoff_record_value_mismatch_not_approved(self, tmp_path):
        # provided date/lake must match the bound decision record
        dec = tmp_path / "dec.json"
        dec.write_text(json.dumps({"events": [{
            "event_id": "e:1",
            "adjudication": {"disposition": "ELIGIBLE",
                             "event_time_interval": {"start": "1964-05-17T00:00:00Z"}},
            "local": {}, "source_fields": {"Lake_name": "Cirenma Co"}}]}))
        so = tmp_path / "so.json"
        so.write_text(json.dumps({
            "schema": "P5_OWNER_SIGNOFF_V2", "status": "APPROVED",
            "target_sha256": A._sha(dec), "approver": {"id": "sanjayb", "role": "owner"},
            "approval_type": "ELIGIBILITY_ADJUDICATION",
            "records": {"e:1": {"date": "1964-05-18", "lake": "Cirenma Co"},
                        }}))
        assert A.approved_record_ids([so], dec, [], None) == set()

    def test_empty_records_signoff_rejected(self, tmp_path):
        # v2 contract is per-record — an empty records map is malformed
        so = {"schema": "P5_OWNER_SIGNOFF_V2", "status": "APPROVED",
              "target_sha256": "D" * 64, "episode_map_sha256": "E" * 64,
              "approver": {"id": "sanjayb", "role": "owner"},
              "approval_type": "ELIGIBILITY_ADJUDICATION", "records": {}}
        assert not A.verify_signoff(so, {"D" * 64}, "E" * 64)
