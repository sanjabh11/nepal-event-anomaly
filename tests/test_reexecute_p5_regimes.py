"""Model re-execution proof tests — synthetic lane fixtures only.

Builds a real run_regimes artifact on a small temporal-holdout frame,
persists artifact + frame + role manifests with sidecars under
tmp_path, then exercises scripts/reexecute_p5_regimes.reexecute_lane:
byte-verified re-execution REPRODUCES the frozen artifact, a mutated
frame is caught by the input_values binding, a tampered recorded
config diffs semantically, a foreign config key refuses closed, and
the report write is exclusive-create.
"""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "scripts"))

from p5_safe_io import (write_once_bytes, write_once_json,
                        write_once_sidecar)
from reexecute_p5_regimes import (DAILY_ARTIFACT_REL, DAILY_FRAME_REL,
                                  DAILY_MANIFESTS_REL, main,
                                  overall_status, reexecute_lane)

FEATURES = ["f1", "f2", "f3"]

# declared temporal lock: train < embargo < holdout, every row inside
TRAIN_IV = ("2020-06-01", "2020-07-10")
EMBARGO_IV = ("2020-07-11", "2020-07-20")
HOLDOUT_IV = ("2020-07-21", "2020-08-19")


def _frame(seed=0) -> pd.DataFrame:
    """80 daily rows x 3 basin groups; declared intervals partition
    the grid (train 40d, embargo 10d, holdout 30d)."""
    rng = np.random.default_rng(seed)
    rows = []
    for gi, g in enumerate(("grp_a", "grp_b", "grp_c")):
        for i in range(80):
            rows.append({
                "unit_id": g,
                "date": str(date(2020, 6, 1) + timedelta(days=i)),
                "basin_group": g,
                "season": "JJA",
                "era": "e1" if i < 40 else "e2",
                "f1": gi + rng.normal(0, 0.5),
                "f2": rng.normal(0, 1.0),
                "f3": i * 0.1 + rng.normal(0, 0.2)})
    return pd.DataFrame(rows)


def _config():
    """Smallest legal declared config — engine floors (>=3 seeds,
    n_bootstrap>=200, n_null_replicates>=50, >=3 fit groups, >=50
    train rows) are hard preflight, so the fixture meets the floor
    rather than subverting it.  Fixture manifest skips host byte
    verification by declaration."""
    from nepal.science_v0.regimes import RegimeRunConfig
    return RegimeRunConfig(
        seeds=(1, 2, 3),
        k_candidates=(1, 2),
        n_bootstrap=200,
        n_null_replicates=50,
        season_col="season", group_col="basin_group",
        era_col="era",
        era_waiver_reason="two-era synthetic fixture",
        effort_waiver_reason="synthetic frame carries no "
                             "observation-effort column",
        unit_col="unit_id", date_col="date",
        label_blinding=True, fitted_on="TRAIN_ONLY",
        train_groups=("grp_a", "grp_b", "grp_c"),
        heldout_groups=(), holdout_axis="temporal",
        temporal_train_interval=TRAIN_IV,
        temporal_embargo_interval=EMBARGO_IV,
        temporal_holdout_interval=HOLDOUT_IV,
        cadence="1D", bootstrap_block_len=3,
        gap_policy="calendar",
        source_manifest={"fixture": True},
        mode="RETROSPECTIVE_REGIME",
        frame_grain="daily", input_role="scientific",
        loro_policy="required")


def _mask(df: pd.DataFrame) -> np.ndarray:
    import datetime as _dt
    d = pd.to_datetime(df["date"]).dt.date
    return ((d >= _dt.date.fromisoformat(TRAIN_IV[0]))
            & (d <= _dt.date.fromisoformat(TRAIN_IV[1]))).to_numpy()


def _publish_lane(root: Path) -> dict:
    """Run the engine once, freeze, and publish a lane-shaped root
    (artifact + frame + role manifests, all sidecar-bound)."""
    from nepal.science_v0.regimes import (freeze_regime_artifact,
                                        run_regimes)
    # publish at the canonical daily relpaths so the fixture root is
    # a drop-in --daily-root for build_report/main
    art_p = root / DAILY_ARTIFACT_REL
    frame_p = root / DAILY_FRAME_REL
    man_p = root / DAILY_MANIFESTS_REL
    art_p.parent.mkdir(parents=True, exist_ok=True)
    frame_p.parent.mkdir(parents=True, exist_ok=True)
    # persist first, then run on the RE-READ bytes — to_csv is not a
    # float64-exact round-trip, and the real producers bind the
    # persisted frame values, never the pre-serialization frame
    write_once_bytes(frame_p,
                     _frame().to_csv(index=False).encode("utf-8"))
    write_once_sidecar(frame_p)
    df = pd.read_csv(frame_p)
    cfg = _config()
    artifact = run_regimes(df, FEATURES, _mask(df), cfg)
    assert artifact.get("status") != "RUN_ERROR", \
        artifact.get("reason")
    frozen = freeze_regime_artifact(dict(artifact))
    write_once_json(art_p, frozen, indent=1)
    write_once_sidecar(art_p)
    write_once_json(
        man_p, {"event": dict(cfg.source_manifest),
                "context": {"fixture": True}}, indent=1)
    write_once_sidecar(man_p)
    return {"artifact": art_p, "frame": frame_p, "manifests": man_p,
            "frozen": frozen}


def _lane(root: Path) -> dict:
    return reexecute_lane(
        "daily", root / DAILY_ARTIFACT_REL, root / DAILY_FRAME_REL,
        role_manifests_path=root / DAILY_MANIFESTS_REL,
        manifest_key="event", root_id="fixture_daily",
        artifact_relpath=DAILY_ARTIFACT_REL,
        frame_relpath=DAILY_FRAME_REL)


@pytest.fixture(scope="module")
def published_lane(tmp_path_factory):
    """One engine run + publication per test session — run_regimes
    executes the real bootstrap/null floors (~seconds); mutation
    tests copy the published tree rather than re-running."""
    root = tmp_path_factory.mktemp("lane")
    return {"root": root, **_publish_lane(root)}


def _copy_lane(src_root: Path, tmp_path: Path) -> Path:
    dst = tmp_path / "lane"
    shutil.copytree(src_root, dst)
    # write-once helpers refuse existing paths — copies are mutated
    # via plain writes in the tests below
    return dst


class TestReexecuteLane:
    def test_reproduced(self, published_lane):
        lane = _lane(published_lane["root"])
        assert lane["verdict"] == "REPRODUCED", lane["problems"]
        assert lane["differences"]["semantic"] == []
        assert lane["checks"]["input_fidelity"] == "exact"
        assert lane["checks"]["train_mask_digest_match"] is True
        assert lane["checks"]["freeze_ok"] is True
        assert lane["checks"]["status_match"] is True
        assert lane["checks"]["config_digest_match"] is True
        assert lane["checks"]["environment_digest_match"] is True
        gates = lane["checks"]["required_gates_equality"]
        assert gates and all(gates.values())
        assert overall_status({"daily": lane}) == "MODEL_REEXECUTED"

    def test_frame_mutation_fails_input_binding(self, published_lane,
                                              tmp_path):
        root = _copy_lane(published_lane["root"], tmp_path)
        frame_p = root / DAILY_FRAME_REL
        df = pd.read_csv(frame_p)
        df.loc[0, "f1"] = float(df.loc[0, "f1"]) + 7.5
        # consistent sidecar, wrong payload — the input_values
        # binding, not the sidecar, must catch the drift
        frame_p.write_bytes(df.to_csv(index=False).encode("utf-8"))
        (Path(str(root / DAILY_FRAME_REL) + ".sha256")).write_text(
            hashlib.sha256(frame_p.read_bytes())
            .hexdigest() + "  " + frame_p.name + "\n")
        lane = _lane(root)
        assert lane["verdict"] == "UNAVAILABLE"
        assert lane["checks"]["input_fidelity"] == "mismatch"
        assert any("input_values" in p for p in lane["problems"])

    def test_roundtrip_drift_splices_bound_input(self, published_lane,
                                                 tmp_path):
        """A frame cell drifted within float64 CSV-ulp tolerance is
        disclosed as roundtrip_drift, the bound input_values matrix is
        spliced in, and the refit still reproduces digest-exactly."""
        root = _copy_lane(published_lane["root"], tmp_path)
        frame_p = root / DAILY_FRAME_REL
        df = pd.read_csv(frame_p)
        v = float(df.loc[0, "f1"])
        # 1e-13 relative drift dominates the CSV parser's own ulp
        # noise (~1e-16) while staying inside the fidelity tolerance
        df.loc[0, "f1"] = v * (1.0 + 1e-13)
        frame_p.write_bytes(df.to_csv(index=False).encode("utf-8"))
        (Path(str(root / DAILY_FRAME_REL) + ".sha256")).write_text(
            hashlib.sha256(frame_p.read_bytes())
            .hexdigest() + "  " + frame_p.name + "\n")
        lane = _lane(root)
        assert lane["checks"]["input_fidelity"] == "roundtrip_drift"
        assert "ulp" in lane["checks"]["input_fidelity_note"]
        assert lane["verdict"] == "REPRODUCED", lane["problems"]

    def test_tampered_recorded_config_is_semantic_mismatch(
            self, published_lane, tmp_path):
        root = _copy_lane(published_lane["root"], tmp_path)
        art_p = root / DAILY_ARTIFACT_REL
        art = json.loads(art_p.read_text())
        art["config"]["seeds"] = [9, 8, 7]
        art_p.write_text(json.dumps(art, indent=1, sort_keys=True)
                         + "\n")
        (Path(str(art_p) + ".sha256")).write_text(
            hashlib.sha256(art_p.read_bytes())
            .hexdigest() + "  " + art_p.name + "\n")
        lane = _lane(root)
        assert lane["verdict"] == "MISMATCH"
        # the tampered seeds are what the proof executes — the
        # recorded config payload itself round-trips, but its bound
        # digest was taken over the original seeds
        assert "config_digest" in lane["differences"]["semantic"]
        assert lane["checks"]["config_digest_match"] is False
        assert lane["checks"][
            "reconstructed_config_digest_match"] is False
        assert lane["checks"][
            "recorded_envelope_digest_match"] is False

    def test_foreign_config_key_refuses_closed(self, published_lane,
                                               tmp_path):
        root = _copy_lane(published_lane["root"], tmp_path)
        art_p = root / DAILY_ARTIFACT_REL
        art = json.loads(art_p.read_text())
        art["config"]["undeclared_knob"] = 1
        art_p.write_text(json.dumps(art, indent=1, sort_keys=True)
                         + "\n")
        (Path(str(art_p) + ".sha256")).write_text(
            hashlib.sha256(art_p.read_bytes())
            .hexdigest() + "  " + art_p.name + "\n")
        lane = _lane(root)
        assert lane["verdict"] == "UNAVAILABLE"
        assert any("undeclared" in p for p in lane["problems"])

    def test_missing_sidecar_unavailable(self, published_lane,
                                         tmp_path):
        root = _copy_lane(published_lane["root"], tmp_path)
        (Path(str(root / DAILY_FRAME_REL) + ".sha256")).unlink()
        lane = _lane(root)
        assert lane["verdict"] == "UNAVAILABLE"
        assert lane["sidecars"]["frame"] is False

    def test_authority_flags_all_false_assertion(self,
                                                 published_lane):
        lane = _lane(published_lane["root"])
        assert lane["checks"][
            "authority_flags_all_false_recomputed"] is True
        assert lane["recomputed"]["associable"] in (True, False)


class TestReportWrite:
    def test_exclusive_create_refuses_second_write(
            self, published_lane, tmp_path, capsys):
        report_out = tmp_path / "report.json"
        # the fixture root is daily-shaped, so the seasonal lane
        # reports UNAVAILABLE — irrelevant to the write-semantics test
        argv = ["--daily-root", str(published_lane["root"]),
                "--seasonal-root", str(published_lane["root"]),
                "--write", "--report-out", str(report_out)]
        rc1 = main(argv)
        assert rc1 in (0, 1)
        assert report_out.exists()
        assert Path(str(report_out) + ".sha256").exists()
        first_bytes = report_out.read_bytes()
        rc2 = main(argv)
        assert rc2 == 2
        assert report_out.read_bytes() == first_bytes

    def test_dry_run_never_writes(self, published_lane, tmp_path,
                                  capsys):
        report_out = tmp_path / "report.json"
        rc = main(["--daily-root", str(published_lane["root"]),
                   "--seasonal-root", str(published_lane["root"]),
                   "--dry-run", "--report-out", str(report_out)])
        assert rc == 2
        assert not report_out.exists()

    def test_report_envelope(self, published_lane, capsys):
        rc = main(["--daily-root", str(published_lane["root"]),
                   "--seasonal-root", str(published_lane["root"]),
                   "--dry-run"])
        out = capsys.readouterr().out
        report = json.loads(out[:out.rindex("\nstatus:")])
        assert report["proof_scope"] == "model_reexecution"
        assert report["schema"] == "P5_MODEL_REEXECUTION_PROOF_V0"
        assert all(v is False for v in report["authority"].values())
        assert report["lanes"]["daily"]["verdict"] == "REPRODUCED"
        # the fixture root carries no seasonal relpaths -> fail closed
        assert report["lanes"]["seasonal"]["verdict"] == "UNAVAILABLE"
        assert report["status"] == "MODEL_REEXECUTION_UNAVAILABLE"
        assert rc == 1
