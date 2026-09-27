#!/usr/bin/env python3
"""Step 1.9 — seasonal regime redesign (Nepal diagnostic re-analysis).

Plan: p5-nepal-reanalysis-2026-09-26/plan/nepal_diagnostic_reanalysis_plan_v1.json
Engine and config are the sealed p5-seasonal-v1 ones (run_seasonal_p5._config),
unchanged.  Arms:
  R0_replay       raw 6-feature frame -> must reproduce assignment digest f23fb0cf...
  R1_primary      6-feature frame, per-basin TRAIN-only anomalised
  R2_alternative  12-feature armc frame-v2, per-basin TRAIN-only anomalised
  RNC             R1 features jointly shuffled, input_role=negative_control -> must be refused
Descriptive only; statuses are reported verbatim.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import run_seasonal_p5 as S  # noqa: E402
from nepal.science_v0.regimes import freeze_regime_artifact, run_regimes  # noqa: E402
from p5_safe_io import write_once_bytes, write_once_json, write_once_sidecar  # noqa: E402

F6 = ["t2m_mean", "d2m_mean", "pdd_sum", "tp_q95", "wet_spell_max_days", "sd_delta"]
F12 = F6 + ["z500_mean", "t500_mean", "t700_mean", "q700_mean", "w700_mean", "w700_q95"]
V0_ASSIGNMENT_DIGEST = "f23fb0cf193f597ab768550b99aec18eb30f16acfbe9ece9d1208f693b9733ad"


def _sha(p) -> str:
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def anomalise_per_basin(df: pd.DataFrame, feats, train_mask: np.ndarray) -> tuple[pd.DataFrame, dict]:
    """Per-basin standardisation with TRAIN-period statistics only."""
    out = df.copy()
    out[list(feats)] = out[list(feats)].astype(float)
    stats = {}
    for b, idx in df.groupby("basin_group").groups.items():
        rows = df.index.isin(idx)
        tr = rows & train_mask
        mu = df.loc[tr, feats].mean()
        sd = df.loc[tr, feats].std(ddof=1)
        if (sd == 0).any() or sd.isna().any():
            raise ValueError(f"basin {b}: zero/undefined train sd for {list(sd[(sd == 0) | sd.isna()].index)}")
        out.loc[rows, feats] = ((df.loc[rows, feats] - mu) / sd).astype(float)
        stats[b] = {"n_train": int(tr.sum()), "mean": mu.round(12).to_dict(), "sd": sd.round(12).to_dict()}
    return out, stats


def manifest_for(root: Path, relpath: str, sha: str, feats) -> dict:
    return {"source_id": f"nepal-diagnostic-reanalysis-{Path(relpath).stem}",
            "source_digests": [sha],
            "units": ["gandaki", "karnali", "koshi"],
            "feature_allowlist": list(feats),
            "lineage": "derived: per-basin TRAIN-only anomalisation of sealed seasonal frame under "
                       "nepal_diagnostic_reanalysis_plan_v1 step 1.9",
            "evidence_root": str(root),
            "source_files": [{"relpath": relpath, "sha256": sha}]}


def summarise(art: dict, frame: pd.DataFrame) -> dict:
    s = {"status": art.get("status"), "reason": art.get("reason"),
         "terminal_reason": S._terminal_reason(art), "failed_gates": S._failed_gates(art)}
    if art.get("status") == "RUN_ERROR":
        return s
    asg = art.get("assignments") or []
    ct = {}
    for b, _, c in asg:
        ct.setdefault(b, {}).setdefault(str(c), 0)
        ct[b][str(c)] += 1
    stab = art.get("stability") or {}
    s.update({"k": art.get("k"), "per_seed_best_k": art.get("per_seed_best_k"),
              "modal_k_frequency": art.get("modal_k_frequency"),
              "ambiguous_fraction": art.get("ambiguous_fraction"),
              "assignment_digest": art.get("assignment_digest"),
              "basin_by_cluster": ct,
              "clusters_equal_basins": all(len(v) == 1 for v in ct.values()) and
              len({next(iter(v)) for v in ct.values()}) == len(ct),
              "nulls": art.get("nulls"),
              "gate_observations": {k: v.get("observed_status") for k, v in
                                    (stab.get("gate_observations") or {}).items()},
              "required_gates": stab.get("required_gates")})
    return s


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame6", required=True)
    ap.add_argument("--frame6-provenance", required=True)
    ap.add_argument("--frame12", required=True)
    ap.add_argument("--plan", required=True)
    ap.add_argument("--out-root", required=True)
    a = ap.parse_args()
    root = Path(a.out_root).resolve()
    (root / "features").mkdir(parents=True, exist_ok=True)
    (root / "run").mkdir(parents=True, exist_ok=True)
    receipt = {"schema": "P5_NEPAL_REGIME_REDESIGN_RECEIPT_V1",
               "plan_sha256": _sha(a.plan),
               "claim_scope": "descriptive_post_hoc_diagnostic_only",
               "authority": {"forecast_authorized": False, "warning_authorized": False,
                             "operational_use_authorized": False, "causal_claim_authorized": False,
                             "confirmatory_claim_authorized": False},
               "inputs_sha256": {"frame6": _sha(a.frame6), "frame6_provenance": _sha(a.frame6_provenance),
                                 "frame12": _sha(a.frame12)},
               "code_sha256": {"runner": _sha(__file__), "run_seasonal_p5": _sha(S.__file__)},
               "arms": {}}

    f6 = pd.read_csv(a.frame6)
    f12 = pd.read_csv(a.frame12)
    mask6, mask12 = S._train_mask(f6), S._train_mask(f12)

    # R0 replay — sealed bytes, original evidence root and manifest shape
    src_root = Path(a.frame6).resolve().parents[1]
    m0 = S._manifest(_sha(a.frame6), _sha(a.frame6_provenance), src_root)
    art0 = run_regimes(f6, F6, mask6, S._config(m0))
    r0 = summarise(art0, f6)
    r0["replay_matches_v0"] = art0.get("assignment_digest") == V0_ASSIGNMENT_DIGEST
    receipt["arms"]["R0_replay"] = r0

    arms = {}
    for name, df, feats, mask in (("R1_primary", f6, F6, mask6), ("R2_alternative", f12, F12, mask12)):
        missing = [c for c in feats if c not in df.columns]
        if missing:
            receipt["arms"][name] = {"status": "RUN_ERROR", "reason": f"missing features {missing}"}
            continue
        an, stats = anomalise_per_basin(df, feats, mask)
        rel = f"features/{name}_anomalised_frame.csv"
        write_once_bytes(root / rel, an.to_csv(index=False).encode("utf-8"))
        sha = write_once_sidecar(root / rel)
        art = run_regimes(an, feats, mask, S._config(manifest_for(root, rel, sha, feats)))
        entry = summarise(art, an)
        entry.update({"frame": rel, "frame_sha256": sha, "n_features": len(feats),
                      "anomalisation_train_stats": stats})
        if art.get("status") != "RUN_ERROR":
            frozen = freeze_regime_artifact(dict(art))
            ap_ = root / "run" / f"{name}_regime_artifact_v1.json"
            write_once_json(ap_, frozen, indent=1)
            entry["artifact"] = str(ap_.relative_to(root))
            entry["artifact_sha256"] = write_once_sidecar(ap_)
        receipt["arms"][name] = entry
        arms[name] = (an, feats, mask)

    # RNC — negative control on the R1 input
    if "R1_primary" in arms:
        an, feats, mask = arms["R1_primary"]
        nc = an.copy()
        rng = np.random.default_rng(20260920)
        nc.loc[:, feats] = an[feats].to_numpy()[rng.permutation(len(an))]
        rel = "features/RNC_negative_control_frame.csv"
        write_once_bytes(root / rel, nc.to_csv(index=False).encode("utf-8"))
        sha = write_once_sidecar(root / rel)
        res = run_regimes(nc, feats, mask,
                          S._config(manifest_for(root, rel, sha, feats), input_role="negative_control"))
        receipt["arms"]["RNC_negative_control"] = {
            "frame": rel, "frame_sha256": sha, "engine_status": res.get("status"),
            "engine_reason": res.get("reason"),
            "rejected_before_fit": res.get("status") == "RUN_ERROR",
            "harness_integrity": "PASS" if res.get("status") == "RUN_ERROR" else "HARNESS FAILURE"}

    out = root / "run" / "regime_redesign_receipt_v1.json"
    write_once_json(out, receipt, indent=1)
    write_once_sidecar(out)
    print(json.dumps({k: {kk: v.get(kk) for kk in ("status", "k", "per_seed_best_k", "clusters_equal_basins",
                                                  "failed_gates", "replay_matches_v0", "reason",
                                                  "rejected_before_fit", "engine_status")}
                      for k, v in receipt["arms"].items()}, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
