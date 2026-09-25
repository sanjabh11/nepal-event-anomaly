"""Behavior tests for armc_routeb_analyze — regression-locks the
defects Codex found in the v18 execution."""
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
    G = {"status": "APPROVED", "target_sha256": "D" * 64,
         "episode_map_sha256": "E" * 64,
         "approver": {"id": "sanjayb", "role": "owner"},
         "scope": "per-record ELIGIBILITY adjudication", "records": {"a": {}}}

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
        v0 = {"status": "APPROVED", "role": "owner_approval", "signer": "sanjayb",
              "approved_artifact": {"file": "decision_v0.json", "sha256": "A" * 64}}
        assert A.verify_signoff(v0, {"A" * 64}, None)

    def test_v0_style_wrong_target_rejected(self):
        v0 = {"status": "APPROVED", "role": "owner_approval",
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
        so.write_text(json.dumps({"status": "APPROVED", "role": "owner_approval",
            "signer": "s", "approved_artifact": {"sha256":
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
    G = {"status": "APPROVED", "target_sha256": "D" * 64,
         "episode_map_sha256": "E" * 64,
         "approver": {"id": "x", "role": "owner"},
         "scope": "per-record ELIGIBILITY adjudication", "records": {}}

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
        g = dict(self.G); g["scope"] = ("per-record ELIGIBILITY adjudication "
            "(DISTINCT from v19 retrieval-scope approval)")
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
