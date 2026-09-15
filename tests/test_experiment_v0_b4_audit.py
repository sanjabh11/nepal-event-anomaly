"""Leakage, timing, and reproducibility audit tests (SWE-B4).

Every audit rule in ``nepal.experiment_v0.audit`` gets one test that
triggers it and one that passes clean.  All inputs are synthetic,
deterministic, and serialized — no real data, no network, no wall
clock.
"""
from __future__ import annotations

import copy
import dataclasses
import json
from pathlib import Path

import pytest

from nepal.experiment_v0.audit import (Finding, audit_module_source,
                                     audit_output_class, audit_pipeline,
                                     replay_bundle, replay_problems)
from nepal.experiment_v0.association import (
    RegimeAssignmentArtifact, association_report_text, run_association)
from nepal.experiment_v0.evaluation import EvaluationReport, evaluate
from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.gates import scan_claims_text

from tests.fixtures import synthetic_exp_b4 as fx

MODULE_DIR = Path(__file__).resolve().parents[1] / "nepal" / \
    "experiment_v0"
AUDITED_MODULES = ("association.py", "vintages.py", "baselines.py",
                   "metrics.py", "evaluation.py", "adapters.py")


def _write(tmp_path: Path, source: str,
           name: str = "tmp_audited_module.py") -> Path:
    target = tmp_path / name
    target.write_text(source, encoding="utf-8")
    return target


# ---------------------------------------------------------------------
# 1. Static module audit — clean vs. triggering sources
# ---------------------------------------------------------------------

class TestModuleSourceAudit:
    @pytest.mark.parametrize("name", AUDITED_MODULES)
    def test_real_modules_produce_no_findings(self, name):
        findings = audit_module_source(MODULE_DIR / name)
        assert findings == [], [f"{f.code}: {f.detail}"
                               for f in findings]

    def test_clean_temp_module_produces_no_findings(self, tmp_path):
        path = _write(tmp_path, (
            "from dataclasses import dataclass\n"
            "import random\n\n"
            "@dataclass(frozen=True)\n"
            "class Rec:\n"
            "    value: str\n\n"
            "def pick(seed: int) -> int:\n"
            "    return random.Random(seed).randrange(10)\n"))
        assert audit_module_source(path) == []

    def test_fit_call_flagged(self, tmp_path):
        path = _write(tmp_path, (
            "class Skimmer:\n"
            "    def fit(self, x):\n"
            "        return self\n\n"
            "def go(m, x):\n"
            "    return m.fit(x)\n"))
        codes = {f.code for f in audit_module_source(path)}
        assert "FIT_CALL" in codes

    def test_fit_call_flagged_even_when_named_association(
            self, tmp_path):
        path = _write(tmp_path, "def go(m, x):\n"
                                "    return m.fit_transform(x)\n",
                      name="association.py")
        codes = {f.code for f in audit_module_source(path)}
        assert "FIT_CALL" in codes

    def test_baselines_fit_site_exempt(self):
        # baselines.py is the single declared supervised-fit site.
        assert audit_module_source(MODULE_DIR / "baselines.py") == []

    def test_wall_clock_flagged(self, tmp_path):
        path = _write(tmp_path, (
            "from datetime import datetime\n\n"
            "def stamp():\n"
            "    return datetime.now()\n"))
        codes = {f.code for f in audit_module_source(path)}
        assert "WALL_CLOCK" in codes
        path2 = _write(tmp_path, (
            "import time\n\n"
            "def stamp():\n"
            "    return time.time()\n"), name="tmp_clock2.py")
        assert "WALL_CLOCK" in {f.code for f in
                                audit_module_source(path2)}

    def test_unseeded_random_flagged(self, tmp_path):
        path = _write(tmp_path, (
            "import random\n\n"
            "def pick():\n"
            "    return random.random()\n"))
        codes = {f.code for f in audit_module_source(path)}
        assert "UNSEEDED_RANDOM" in codes

    def test_network_import_flagged(self, tmp_path):
        path = _write(tmp_path, "import requests\n"
                                "from urllib.request import urlopen\n")
        codes = {f.code for f in audit_module_source(path)}
        assert "NETWORK_IMPORT" in codes

    def test_open_call_flagged_for_review(self, tmp_path):
        path = _write(tmp_path, (
            "def read(p):\n"
            "    with open(p) as fh:\n"
            "        return fh.read()\n"))
        codes = {f.code for f in audit_module_source(path)}
        assert "FILE_ACCESS_REVIEW" in codes

    def test_frozen_mutation_flagged(self, tmp_path):
        path = _write(tmp_path, (
            "from dataclasses import dataclass\n\n"
            "@dataclass(frozen=True)\n"
            "class Rec:\n"
            "    items: tuple = ()\n"
            "    def poke(self):\n"
            "        object.__setattr__(self, 'items', ())\n"
            "        self.items.append('x')\n"))
        findings = audit_module_source(path)
        assert sum(1 for f in findings
                   if f.code == "MUTATION_ATTEMPT") >= 2

    def test_post_init_setattr_idiom_allowed(self, tmp_path):
        path = _write(tmp_path, (
            "from dataclasses import dataclass\n\n"
            "@dataclass(frozen=True)\n"
            "class Rec:\n"
            "    items: tuple = ()\n"
            "    def __post_init__(self):\n"
            "        object.__setattr__(self, '_idx', {})\n"))
        assert audit_module_source(path) == []

    def test_forbidden_claim_text_flagged(self, tmp_path):
        path = _write(tmp_path, (
            'NOTE = "this module issues an operational warning"\n'))
        codes = {f.code for f in audit_module_source(path)}
        assert "FORBIDDEN_CLAIM_TEXT" in codes

    def test_unfrozen_dataclass_flagged(self, tmp_path):
        path = _write(tmp_path, (
            "from dataclasses import dataclass\n\n"
            "@dataclass\n"
            "class Rec:\n"
            "    value: str\n"))
        codes = {f.code for f in audit_module_source(path)}
        assert "UNFROZEN_DATACLASS" in codes

    def test_dynamic_exec_flagged(self, tmp_path):
        path = _write(tmp_path, "def go(s):\n    return eval(s)\n")
        codes = {f.code for f in audit_module_source(path)}
        assert "DYNAMIC_EXEC" in codes


# ---------------------------------------------------------------------
# 2. Label-ordering leakage probe
# ---------------------------------------------------------------------

class TestLabelOrderingLeakage:
    def test_permuted_events_identical_digests(self):
        bundle_a = fx.synthetic_bundle()
        bundle_b = copy.deepcopy(bundle_a)
        bundle_b["association"]["events"] = list(
            reversed(bundle_b["association"]["events"]))
        ra = replay_bundle(bundle_a)
        rb = replay_bundle(bundle_b)
        assert ra["problems"] == []
        assert rb["problems"] == []
        assert ra["association_digest"] == rb["association_digest"]
        # The frozen artifact digest is identical under permutation of
        # both its own rows and the label sequence.
        assert bundle_a["association"]["artifact"] == \
            bundle_b["association"]["artifact"]

    def test_permuted_assignment_rows_identical_artifact_digest(self):
        bundle_a = fx.synthetic_bundle()
        bundle_b = copy.deepcopy(bundle_a)
        bundle_b["association"]["artifact"]["assignments"] = list(
            reversed(
                bundle_b["association"]["artifact"]["assignments"]))
        art_a = RegimeAssignmentArtifact.from_dict(
            bundle_a["association"]["artifact"])
        art_b = RegimeAssignmentArtifact.from_dict(
            bundle_b["association"]["artifact"])
        assert sha256_canonical(art_a.to_dict()) == \
            sha256_canonical(art_b.to_dict())
        assert replay_bundle(bundle_a)["association_digest"] == \
            replay_bundle(bundle_b)["association_digest"]

    def test_audit_pipeline_order_probe_clean(self):
        findings = audit_pipeline(fx.synthetic_bundle())
        assert "LABEL_ORDER_SENSITIVE" not in {f.code for f in findings}
        assert "ARTIFACT_ORDER_SENSITIVE" not in \
            {f.code for f in findings}


# ---------------------------------------------------------------------
# 3. Timing violations
# ---------------------------------------------------------------------

class TestTimingViolations:
    def test_issue_time_mismatch_yields_replay_problems(self):
        bundle = fx.synthetic_bundle()
        bundle["forecast"]["cases"][0]["issue_time"] = \
            "2020-06-01T07:00:00Z"   # bound vintage issues at 06:00Z
        result = replay_bundle(bundle)
        assert result["problems"]
        assert any("issue_time" in p for p in result["problems"])
        assert result["evaluation_status"] == "REPLAY_FAILED"

    def test_consistent_issue_times_replay_clean(self):
        result = replay_bundle(fx.synthetic_bundle())
        assert result["problems"] == []
        assert result["evaluation_status"] in (
            "FORECAST_EXPERIMENT_ONLY", "UNDERPOWERED_DESCRIPTIVE_ONLY")


# ---------------------------------------------------------------------
# 4. Test-region reuse
# ---------------------------------------------------------------------

class TestRegionReuse:
    def test_case_region_outside_test_groups_flagged(self):
        bundle = fx.synthetic_bundle()
        bundle["forecast"]["cases"][0]["region"] = "train_basin_a"
        findings = audit_pipeline(bundle)
        reuse = [f for f in findings if f.code == "TEST_REGION_REUSE"]
        assert reuse
        assert any("train_basin_a" in f.detail for f in reuse)

    def test_case_regions_inside_test_groups_clean(self):
        bundle = fx.synthetic_bundle()
        findings = [f for f in audit_pipeline(bundle)
                    if f.code == "TEST_REGION_REUSE"]
        assert findings == []

    def test_event_basin_region_outside_test_groups_flagged(self):
        bundle = fx.synthetic_bundle()
        holdout = bundle["association"]["holdout"]
        # Demote koshi_eval to the train split: koshi events then sit
        # in a region that is not a locked test group.
        holdout["train_groups"] = ["north_train", "koshi_eval"]
        holdout["test_groups"] = ["karnali_eval", "gandaki_eval"]
        findings = audit_pipeline(bundle)
        assert any(f.code == "TEST_REGION_REUSE" for f in findings)


# ---------------------------------------------------------------------
# 5. Cascade split atomicity
# ---------------------------------------------------------------------

class TestCascadeAtomicity:
    def test_cascade_spanning_splits_flagged(self):
        bundle = fx.synthetic_bundle()
        events = bundle["association"]["events"]
        events[0]["cascade_group_id"] = "casc-x"   # koshi -> test
        events[1]["cascade_group_id"] = "casc-x"   # gandaki -> train
        bundle["association"]["holdout"]["event_assignments"][
            events[1]["event_id"]] = "north_train"
        findings = audit_pipeline(bundle)
        assert any(f.code == "CASCADE_SPLIT_COLLISION"
                   for f in findings)
        # The same defective bundle fails the pipeline on replay —
        # problems, not a crash.
        assert replay_bundle(bundle)["problems"]

    def test_shared_cascade_in_one_split_clean(self):
        bundle = fx.synthetic_bundle()
        events = bundle["association"]["events"]
        # ev-000 (koshi) and ev-003 (koshi) both map to koshi_eval/test.
        events[0]["cascade_group_id"] = "casc-ok"
        events[3]["cascade_group_id"] = "casc-ok"
        findings = audit_pipeline(bundle)
        assert "CASCADE_SPLIT_COLLISION" not in \
            {f.code for f in findings}


# ---------------------------------------------------------------------
# 6. Mutable outputs
# ---------------------------------------------------------------------

class TestMutableOutputs:
    def test_records_and_reports_are_frozen(self):
        events = fx.make_events(21)
        artifact = fx.planted_artifact(events)
        holdout = fx.make_holdout(events)
        report = run_association(
            artifact, events, fx.make_controls(), fx.UNIT_BASINS,
            holdout=holdout, region_basins=fx.REGION_BASINS,
            opportunities=fx.make_opportunities(),
            n_boot=16, seed=3)
        with pytest.raises(dataclasses.FrozenInstanceError):
            artifact.regime_digest = "0" * 64  # type: ignore[misc]
        with pytest.raises(dataclasses.FrozenInstanceError):
            report.status = "X"  # type: ignore[misc]
        cases = fx.synthetic_forecast_cases()
        admitted = {sha256_canonical(v.to_dict()): v for v in (
            fx.admitted_vintage_for(r)
            for r in fx.synthetic_vintage_requests())}
        evaluation = evaluate(
            cases, holdout=fx.synthetic_forecast_holdout(),
            baseline_probs=fx.synthetic_baseline_probs(cases),
            admitted_vintages=admitted,
            n_opportunities=len(cases), n_boot=16, seed=3)
        with pytest.raises(dataclasses.FrozenInstanceError):
            evaluation.status = "X"  # type: ignore[misc]

    def test_nonfrozen_output_class_flagged(self):
        @dataclasses.dataclass
        class NotFrozen:
            value: str

        findings = audit_output_class(NotFrozen)
        assert [f.code for f in findings] == ["MUTABLE_OUTPUT"]

    def test_dict_field_on_frozen_class_flagged_for_review(self):
        @dataclasses.dataclass(frozen=True)
        class DictField:
            table: dict

        findings = audit_output_class(DictField)
        assert [f.code for f in findings] == ["MUTABLE_FIELD"]

    def test_frozen_immutable_fields_clean(self):
        @dataclasses.dataclass(frozen=True)
        class Clean:
            name: str
            rows: tuple = ()

        assert audit_output_class(Clean) == []

    def test_real_pipeline_classes_frozen(self):
        # Documented observation: both report dataclasses are frozen
        # but carry dict-typed fields — shallow immutability only.
        findings = audit_pipeline(fx.synthetic_bundle())
        review = {f.code for f in findings}
        assert review <= {"MUTABLE_FIELD"}
        paths = {f.path for f in findings}
        assert any("AssociationReport" in p for p in paths)
        assert any("EvaluationReport" in p for p in paths)


# ---------------------------------------------------------------------
# 7. Missing provenance
# ---------------------------------------------------------------------

class TestMissingProvenance:
    def test_empty_artifact_digest_flagged(self):
        bundle = fx.synthetic_bundle()
        bundle["association"]["artifact"]["regime_digest"] = ""
        findings = audit_pipeline(bundle)
        assert any(f.code == "MISSING_PROVENANCE"
                   and "regime_digest" in f.detail for f in findings)

    def test_empty_vintage_digests_flagged(self):
        bundle = fx.synthetic_bundle()
        bundle["forecast"]["vintage_requests"][0][
            "archive_payload_sha256"] = ""
        findings = audit_pipeline(bundle)
        assert any(f.code == "MISSING_PROVENANCE"
                   and "archive_payload_sha256" in f.detail
                   for f in findings)

    def test_empty_case_vintage_digest_flagged(self):
        bundle = fx.synthetic_bundle()
        bundle["forecast"]["cases"][0]["vintage_digest"] = ""
        findings = audit_pipeline(bundle)
        assert any(f.code == "MISSING_PROVENANCE"
                   and "vintage_digest" in f.detail for f in findings)

    def test_unadmitted_case_vintage_flagged(self):
        bundle = fx.synthetic_bundle()
        bundle["forecast"]["cases"][0]["vintage_digest"] = "f" * 64
        findings = audit_pipeline(bundle)
        assert any(f.code == "UNADMITTED_VINTAGE" for f in findings)

    def test_full_provenance_clean(self):
        bundle = fx.synthetic_bundle()
        findings = audit_pipeline(bundle)
        assert "MISSING_PROVENANCE" not in {f.code for f in findings}
        assert "UNADMITTED_VINTAGE" not in {f.code for f in findings}
        assert "VINTAGE_NOT_ADMISSIBLE" not in \
            {f.code for f in findings}


# ---------------------------------------------------------------------
# 8. Claim-text discipline
# ---------------------------------------------------------------------

class TestClaimText:
    def test_forbidden_phrase_detected_in_report_text(self):
        text = ("This synthetic report issues an operational warning "
                "for the basin.")
        assert scan_claims_text(text)

    def test_real_report_text_clean(self):
        events = fx.make_events(21)
        report = run_association(
            fx.planted_artifact(events), events, fx.make_controls(),
            fx.UNIT_BASINS, holdout=fx.make_holdout(events),
            region_basins=fx.REGION_BASINS,
            opportunities=fx.make_opportunities(),
            n_boot=16, seed=3)
        assert scan_claims_text(association_report_text(report)) == []

    def test_real_module_sources_carry_no_claim_hits(self):
        for name in AUDITED_MODULES:
            findings = audit_module_source(MODULE_DIR / name)
            assert "FORBIDDEN_CLAIM_TEXT" not in \
                {f.code for f in findings}, name

    def test_status_tokens_in_module_text_flagged(self, tmp_path):
        path = _write(tmp_path,
                      'LINE = "status: FORECAST_READY"\n')
        codes = {f.code for f in audit_module_source(path)}
        assert "FORBIDDEN_CLAIM_TEXT" in codes
