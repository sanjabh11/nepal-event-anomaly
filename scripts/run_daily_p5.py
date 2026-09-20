#!/usr/bin/env python3
"""P5-A2 daily lane artifact serialization (amendment v5).

R-03/R-04/R-05 repair: the original P5-A2 run computed the regime
artifact in-memory and bound only its digest into the v0 receipt —
the artifact bytes were never persisted.  This driver re-executes
the frozen daily protocol under the declared v1 config
(`retrieval/p5_amendment_v5_artifact_lineage.json`), serializes the
artifact to the canonical evidence location, and issues a v1
lineage-bound receipt.  The v0 receipt is HISTORICAL — preserved,
never rewritten.

Determinism check: ``run_glof_descriptive_poc`` recomputes the
artifact internally — the v1 receipt's ``regime_artifact_digest``
must equal the persisted artifact's digest.  Agreement proves the
receipt binds these exact bytes; disagreement fails closed.

Writes into DAILY_ROOT:
  retrieval/p5_glof_regime_artifact_v1.json (+sha256)
  retrieval/p5_glof_descriptive_receipt_v1.json (+sha256)
"""
from __future__ import annotations

import argparse
import datetime as _dt
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from nepal.science_v0.regimes import (RegimeRunConfig, run_regimes,
                                    freeze_regime_artifact)
from nepal.science_v0.glof_poc import run_glof_descriptive_poc

DEFAULT_DAILY_ROOT = Path(
    "/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19")

FEATURE_COLS = ["t2m_daily", "d2m_daily", "tp_daily", "sf_daily",
                "sd_daily", "wind_speed_daily", "wind_dir_sin",
                "wind_dir_cos", "rh_daily", "pdd_daily"]

# Declared v1 config — every field listed in amendment v5.  The
# original in-session config was never persisted; this is the
# declared reconstruction, not a claimed byte-identical replica.
V1_CONFIG = dict(
    seeds=(42, 7, 2024),
    k_candidates=(1, 2, 3, 4, 5),
    n_bootstrap=200, n_null_replicates=50, null_alpha=0.05,
    season_col="season", group_col="basin_group",
    era_col="era", era_boundaries=("2013-01-01",),
    era_drift_max=0.5, missingness_policy="listwise",
    effort_waiver_reason="ERA5-Land reanalysis is "
                         "assimilation-complete — no observing-"
                         "effort axis exists at daily grain",
    unit_col="unit_id", date_col="date", label_blinding=True,
    fitted_on="TRAIN_ONLY",
    train_groups=("gandaki", "karnali", "koshi"),
    heldout_groups=(), holdout_axis="temporal",
    temporal_train_interval=("2001-06-01", "2017-08-31"),
    temporal_embargo_interval=("2018-06-01", "2019-08-31"),
    temporal_holdout_interval=("2020-06-01", "2025-08-31"),
    cadence="1D", bootstrap_block_len=7, gap_policy="calendar",
    mode="RETROSPECTIVE_REGIME",
    retrospective_data_class="REANALYSIS",
    covariance_type="full",
    frame_grain="daily", input_role="scientific",
    loro_policy="required")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _sidecar(p: Path) -> None:
    Path(str(p) + ".sha256").write_text(f"{_sha(p)}  {p.name}\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily-root", default=str(DEFAULT_DAILY_ROOT))
    args = ap.parse_args()
    daily_root = Path(args.daily_root).resolve()
    frame_csv = (daily_root / "era5-multibasin/features/"
                 "regime_frame_hma_jja_2001_2025.csv")
    pkg_path = daily_root / "glof-events/p3_runner_package_v0.json"
    manifests_p = daily_root / "retrieval/role_manifests_v0.json"
    ret_dir = daily_root / "retrieval"

    for p in (frame_csv, pkg_path, manifests_p):
        sc = Path(str(p) + ".sha256")
        if not p.exists() or not sc.exists() or \
                sc.read_text().split()[0] != _sha(p):
            print(f"FAIL — byte verification failed for {p}")
            return 1

    df = pd.read_csv(frame_csv)
    package = json.loads(pkg_path.read_text())
    # the receipt requires config.source_manifest to be the manifest
    # the event package binds (digests.event / source_manifest_digest)
    event_manifest = json.loads(
        manifests_p.read_text())["event"]

    cfg = RegimeRunConfig(source_manifest=event_manifest,
                          **V1_CONFIG)
    d = pd.to_datetime(df["date"]).dt.date
    train_mask = ((d >= _dt.date(2001, 6, 1))
                  & (d <= _dt.date(2017, 8, 31))).to_numpy()

    # engine run -> freeze -> persist artifact
    artifact = run_regimes(df, FEATURE_COLS, train_mask, cfg)
    if artifact.get("status") == "RUN_ERROR":
        print("RUN_ERROR:", artifact.get("reason"))
        return 1
    frozen = freeze_regime_artifact(dict(artifact))
    art_path = ret_dir / "p5_glof_regime_artifact_v1.json"
    art_path.write_text(json.dumps(frozen, indent=1,
                                   sort_keys=True) + "\n")
    _sidecar(art_path)
    art_sha = _sha(art_path)

    # authoritative receipt path — recomputes the artifact
    # internally; digest agreement is the determinism binding
    receipt = run_glof_descriptive_poc(
        df, FEATURE_COLS, train_mask, cfg, package)
    rcpt = dict(receipt)
    rcpt["record_type"] = "GLOF_POC_RECEIPT_V1"
    rcpt["artifact_file"] = str(art_path)
    rcpt["artifact_file_sha256"] = art_sha
    rcpt["artifact_digest_matches_file"] = (
        rcpt.get("regime_artifact_digest")
        == frozen.get("regime_artifact_digest"))
    rcpt["supersedes"] = (
        "retrieval/p5_glof_descriptive_receipt_v0.json — historical; "
        "its artifact digest (982e7b6e…8327) was receipt-bound only "
        "(bytes never persisted). This v1 receipt binds persisted "
        "artifact bytes under the amendment-v5 declared config.")
    rcpt["amendment"] = "p5_amendment_v5_artifact_lineage.json"
    # rebind the report digest over the extended v1 surface
    from nepal.research_v0._hashing import sha256_canonical
    rcpt["report_digest"] = sha256_canonical(
        {k: v for k, v in rcpt.items()
         if k not in ("report_digest", "problems")})
    rcpt_path = ret_dir / "p5_glof_descriptive_receipt_v1.json"
    rcpt_path.write_text(json.dumps(rcpt, indent=1,
                                    sort_keys=True) + "\n")
    _sidecar(rcpt_path)

    print(json.dumps({
        "artifact_status": frozen.get("status"),
        "artifact_digest": frozen.get("regime_artifact_digest"),
        "artifact_file_sha": art_sha,
        "receipt_status": rcpt["status"],
        "digest_matches_file":
            rcpt["artifact_digest_matches_file"],
        "receipt_problems": rcpt["problems"]}, indent=1))
    return 0 if rcpt["artifact_digest_matches_file"] else 1


if __name__ == "__main__":
    sys.exit(main())
