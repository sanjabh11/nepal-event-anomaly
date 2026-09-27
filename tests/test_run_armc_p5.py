from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import scripts.run_armc_p5 as runner


def _frame(path: Path, n: int = 75) -> Path:
    rows = []
    rng = np.random.default_rng(4)
    for basin in ("gandaki", "karnali", "koshi"):
        for year in range(2001, 2026):
            rows.append({
                "unit_id": basin,
                "season_year": year,
                "date": (pd.Timestamp("2001-07-16") + pd.Timedelta(days=365 * (year - 2001))).strftime("%Y-%m-%d"),
                "basin_group": basin,
                "season": "JJA",
                "era": "pre_2013" if year < 2013 else "post_2013",
                "t2m_mean": float(rng.normal()),
                "z500_mean": float(rng.normal()),
            })
    frame = pd.DataFrame(rows).iloc[:n]
    path.write_text(frame.to_csv(index=False))
    digest = __import__("hashlib").sha256(path.read_bytes()).hexdigest()
    Path(str(path) + ".sha256").write_text(f"{digest}  {path.name}\n")
    prov = path.parent / "seasonal_frame_provenance_v0.json"
    prov.write_text(json.dumps({
        "schema": "P5_ARMC_EXTENDED_FRAME_PROVENANCE_V0",
        "collinearity_exclusions": [],
    }))
    pdigest = __import__("hashlib").sha256(prov.read_bytes()).hexdigest()
    Path(str(prov) + ".sha256").write_text(f"{pdigest}  {prov.name}\n")
    return path


def _contracts(tmp_path: Path) -> tuple[Path, Path, Path, Path]:
    frame = _frame(tmp_path / "seasonal_frame_jja_2001_2025.csv")
    units = tmp_path / "feature_units_v0.json"
    units.write_text(json.dumps({"schema": "P5_FEATURE_UNITS_V0", "units": {"t2m_mean": "degC", "z500_mean": "m"}}))
    digest = __import__("hashlib").sha256(units.read_bytes()).hexdigest()
    Path(str(units) + ".sha256").write_text(f"{digest}  {units.name}\n")
    feature = tmp_path / "feature_contract.json"
    feature.write_text(json.dumps({
        "schema": "P5_ARMC_FEATURE_CONTRACT_V0",
        "claim_scope": "research_only_no_operational_authorization",
        "base_feature_cols": ["t2m_mean"],
        "extended_features": [{"name": "z500_mean", "source_variable": "geopotential", "pressure_level_hpa": 500, "aggregation": "jja_mean"}],
        "feature_cols": ["t2m_mean", "z500_mean"],
        "feature_units": {"t2m_mean": "degC", "z500_mean": "m"},
        "collinearity_threshold": 0.95,
    }))
    run = tmp_path / "run_contract.json"
    run.write_text(json.dumps({
        "schema": "P5_ARMC_RUN_CONTRACT_V0",
        "claim_scope": "research_only_no_operational_authorization",
        "feature_cols": ["t2m_mean", "z500_mean"],
        "frame_grain": "seasonal",
        "covariance_type": "tied",
        "input_role": "scientific",
        "holdout_axis": "temporal",
        "loro_policy": "diagnostic",
        "seeds": [42, 7, 2024],
        "k_candidates": [1, 2, 3, 4],
        "n_bootstrap": 200,
        "n_null_replicates": 50,
        "null_alpha": 0.05,
        "era_boundaries": ["2013-01-01"],
        "era_drift_max": 0.5,
        "missingness_policy": "listwise",
        "effort_waiver_reason": "reanalysis assimilation-complete",
        "temporal_train_interval": ["2001-06-01", "2017-08-31"],
        "temporal_embargo_interval": ["2018-06-01", "2019-08-31"],
        "temporal_holdout_interval": ["2020-06-01", "2025-08-31"],
        "bootstrap_block_len": 3,
        "null_extra_strata_col": "basin_group",
    }))
    return frame, units, feature, run


def test_input_contract_requires_exact_75_rows(tmp_path: Path) -> None:
    frame, units, feature, _ = _contracts(tmp_path)
    frame = _frame(tmp_path / "bad.csv", n=74)
    with pytest.raises(ValueError, match="exactly 75"):
        runner._load_input(frame, units, feature)


def test_negative_control_refusal_is_a_hard_gate(monkeypatch, tmp_path: Path) -> None:
    frame, units, feature, run = _contracts(tmp_path)
    calls = []

    def fake_run(df, feature_cols, train_mask, config):
        calls.append(config.input_role)
        if config.input_role == "negative_control":
            return {"status": "RUN_ERROR", "reason": "declared negative-control input — nonphysical feature contracts cannot produce a regime fit"}
        return {"status": "UNSUPERVISED_STRUCTURE_NOT_STABLE", "stability": {"required_gates": {"x": False}}, "config_digest": "a" * 64, "regime_artifact_digest": "b" * 64}

    monkeypatch.setattr(runner, "run_regimes", fake_run)
    monkeypatch.setattr(runner, "freeze_regime_artifact", lambda value: value)
    result = runner.run_armc(
        frame_path=frame,
        units_path=units,
        feature_contract_path=feature,
        run_contract_path=run,
        output_root=tmp_path / "run-root",
    )
    assert calls == ["negative_control", "scientific"]
    assert result["negative_control"]["rejected_before_fit"] is True
    assert result["authority"] == runner.AUTHORITY_FALSE
    assert result["status"] == "UNSUPERVISED_STRUCTURE_NOT_STABLE"
    assert (tmp_path / "run-root/run/armc_lane_receipt_v0.json.sha256").exists()


def test_run_refuses_existing_output_root(tmp_path: Path) -> None:
    frame, units, feature, run = _contracts(tmp_path)
    output = tmp_path / "existing"
    output.mkdir()
    with pytest.raises(FileExistsError):
        runner.run_armc(
            frame_path=frame,
            units_path=units,
            feature_contract_path=feature,
            run_contract_path=run,
            output_root=output,
        )
