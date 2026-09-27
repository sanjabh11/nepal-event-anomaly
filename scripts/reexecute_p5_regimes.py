#!/usr/bin/env python3
"""Model re-execution proof for the P5 regime lanes (daily + seasonal).

The artifact-integrity replay scripts verify that persisted bytes are
self-consistent; they never refit the model.  This proof goes further:
it re-runs ``run_regimes`` on the byte-verified persisted input frames
under the persisted declared configs — the config is reconstructed
STRICTLY from the artifact's recorded ``config`` payload (foreign keys
reject, list->tuple restoration follows the dataclass defaults) — and
compares the recomputed artifact field-by-field against the persisted
frozen artifact.

Lane verdicts:
  REPRODUCED               regime_artifact_digest AND freeze_digest equal
  SEMANTIC_MATCH_ENV_DIFF  only volatile fields differ (environment_digest
                           and the digests that cover it, plus a
                           run_manifest that differs solely in its
                           embedded environment_digest)
  MISMATCH                 any semantic field differs (mask drift,
                           gate drift, parameter drift — never a byte
                           problem alone)
  UNAVAILABLE              inputs/config unverifiable (sidecars, strict
                           config reconstruction, source-manifest byte
                           verification, input_values decode)

Report status rolls up: MODEL_REEXECUTED (both lanes REPRODUCED),
MODEL_REEXECUTION_MISMATCH (any MISMATCH), MODEL_REEXECUTION_PARTIAL
(any SEMANTIC_MATCH_ENV_DIFF, none MISMATCH),
MODEL_REEXECUTION_UNAVAILABLE (any UNAVAILABLE, none MISMATCH).

This script writes nothing except the single --report-out path
(exclusive-create + sidecar).  It is NOT an artifact_integrity_replay:
proof_scope is ``model_reexecution``.

Usage:
    .venv/bin/python -B scripts/reexecute_p5_regimes.py --dry-run
    .venv/bin/python -B scripts/reexecute_p5_regimes.py --write \
        --report-out NEW_PATH
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import sys
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
_SCRIPTS = Path(__file__).resolve().parent
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))

DEFAULT_DAILY_ROOT = Path(
    "/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19")
DEFAULT_SEASONAL_ROOT = Path(
    "/Users/sanjayb/nepal-event-anomaly-evidence/"
    "p5-seasonal-v1-2026-09-20")

DAILY_ARTIFACT_REL = "retrieval/p5_glof_regime_artifact_v1.json"
DAILY_FRAME_REL = ("era5-multibasin/features/"
                   "regime_frame_hma_jja_2001_2025.csv")
DAILY_MANIFESTS_REL = "retrieval/role_manifests_v0.json"
SEASONAL_ARTIFACT_REL = "run/seasonal_regime_artifact_v0.json"
SEASONAL_FRAME_REL = "features/seasonal_frame_jja_2001_2025.csv"

AUTHORITY_FIELDS = ("promotion_eligible", "production_authorized",
                    "warning_path_authorized", "operational_claim")
# Fields whose difference is environment/volatility, not semantics.
# regime_artifact_digest and freeze_digest cover the whole payload,
# so an environment-only drift moves them too; run_manifest embeds
# environment_digest (env-derived only when that is its sole diff).
VOLATILE_KEYS = frozenset(
    {"environment_digest", "regime_artifact_digest", "freeze_digest"})
# The serialized producer config never carries forecast_vintages —
# it is artifact-level evidence, not bound configuration.
_NON_CONFIG_FIELDS = frozenset({"forecast_vintages"})
_ABSENT = object()


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def _sidecar_ok(p: Path) -> bool:
    s = Path(str(p) + ".sha256")
    try:
        return s.is_file() and \
            s.read_text().split()[0] == _sha(p)
    except (OSError, IndexError):
        return False


def _engine():
    """Deferred engine imports — the script must stay importable in
    environments where only the report layer is exercised."""
    from nepal.research_v0._hashing import (
        sha256_canonical, verify_source_evidence)
    from nepal.research_v0.producer_validation import fixture_flag
    from nepal.science_v0 import regimes
    return regimes, sha256_canonical, verify_source_evidence, fixture_flag


def _reconstruct_config(recorded: dict):
    """Strictly rebuild a RegimeRunConfig from the artifact's recorded
    ``config`` dict.  Foreign keys reject (the serialized contract is
    exactly the dataclass field set minus artifact-level fields);
    list-valued fields restore to tuples where the dataclass default
    is a tuple; a missing field with no default is a problem."""
    regimes, _, _, _ = _engine()
    if not isinstance(recorded, dict) or not recorded:
        return None, ["artifact config payload missing or not a mapping"]
    fields = {f.name: f for f in dataclasses.fields(
        regimes.RegimeRunConfig)}
    allowed = set(fields) - _NON_CONFIG_FIELDS
    unknown = sorted(set(recorded) - allowed)
    if unknown:
        return None, [
            f"recorded config carries undeclared field(s) {unknown} — "
            "strict reconstruction refuses foreign keys"]
    kwargs = {}
    problems = []
    for name, f in fields.items():
        if name in _NON_CONFIG_FIELDS:
            continue
        if name not in recorded:
            if f.default is dataclasses.MISSING and \
                    f.default_factory is dataclasses.MISSING:
                problems.append(
                    f"recorded config omits required field {name!r} "
                    "(no dataclass default)")
            continue
        v = recorded[name]
        if isinstance(f.default, tuple) and isinstance(v, list):
            v = tuple(tuple(x) if isinstance(x, list) else x
                      for x in v)
        kwargs[name] = v
    if problems:
        return None, problems
    try:
        cfg = regimes.RegimeRunConfig(**kwargs)
    except TypeError as exc:
        return None, [f"config reconstruction failed: {exc}"]
    return cfg, []


def _serialized_config(cfg) -> dict:
    """The exact producer serialization of a RegimeRunConfig (the
    dict config_digest binds): asdict minus artifact-level evidence,
    forecast fields normalized to sorted string lists."""
    d = dataclasses.asdict(cfg)
    d.pop("forecast_vintages", None)
    d["forecast_vintage_digests"] = sorted(
        str(x) for x in cfg.forecast_vintage_digests)
    d["forecast_feature_set"] = sorted(
        str(x) for x in cfg.forecast_feature_set)
    return d


def _train_mask(df, cfg):
    """Rebuild the declared train mask from the recorded config —
    the temporal axis is the declared train interval (inclusive ISO
    dates); the geographic axis is train_groups membership.  The mask
    is derived, never read back from the artifact."""
    import datetime as _dt
    import pandas as pd
    if cfg.holdout_axis == "temporal":
        iv = cfg.temporal_train_interval
        if not isinstance(iv, (list, tuple)) or len(iv) != 2:
            raise ValueError("temporal_train_interval is not a "
                             "(start, end) pair")
        lo = _dt.date.fromisoformat(str(iv[0]))
        hi = _dt.date.fromisoformat(str(iv[1]))
        dser = pd.Series(
            pd.to_datetime(df[cfg.date_col],
                           errors="coerce").dt.date,
            index=df.index)
        return dser.map(
            lambda d: isinstance(d, _dt.date) and lo <= d <= hi
        ).to_numpy(dtype=bool)
    return df[cfg.group_col].isin(
        set(cfg.train_groups)).to_numpy(dtype=bool)


# Maximum tolerated relative divergence between a persisted CSV frame
# cell and the artifact's bound ``input_values`` decode before the
# frame is declared not-the-input.  CSV serialization can lose the
# final float64 ulp (~1e-16 relative); anything larger is a real
# input mismatch, not transport noise.
_INPUT_FIDELITY_RTOL = 1e-12


def _input_values_match(df, feature_cols, recorded) -> tuple:
    """Compare the loaded frame's feature matrix against the
    artifact's bound ``input_values`` decode — the bound matrix is the
    AUTHORITATIVE declared model input; the CSV is a transport that
    can lose the last float64 ulp.

    Returns (fidelity, problem, decoded_array):
      "exact"           — frame equals the bound input bit-for-bit
      "roundtrip_drift" — every differing cell is within
                          _INPUT_FIDELITY_RTOL (CSV ulp loss); the
                          decoded matrix is spliced in for the refit
      "mismatch"        — a real divergence; the frame is NOT the
                          declared model input
      None              — the artifact carries no bound input matrix
    """
    regimes, _, _, _ = _engine()
    vals = recorded.get("input_values")
    schema = recorded.get("input_schema") or {}
    if vals is None:
        return None, "artifact carries no input_values — the " \
                     "declared input matrix is not bound", None
    try:
        arr = regimes._decode_input_values(vals)
    except (TypeError, ValueError) as exc:
        return None, f"input_values does not decode: {exc}", None
    if schema.get("shape") and \
            list(arr.shape) != list(schema["shape"]):
        return None, "input_values shape disagrees with the bound " \
                     "input_schema", None
    if arr.shape[0] != len(df) or arr.shape[1] != len(feature_cols):
        return None, "frame/feature matrix shape disagrees with " \
                     "input_values", None
    import numpy as np
    try:
        frame_arr = np.ascontiguousarray(
            df.loc[:, list(feature_cols)].to_numpy(dtype=np.float64))
    except (TypeError, ValueError) as exc:
        return None, f"frame feature matrix is not float64-" \
                     f"coercible: {exc}", None
    eq = (arr == frame_arr) | (np.isnan(arr) & np.isnan(frame_arr))
    if bool(eq.all()):
        return "exact", None, arr
    denom = np.abs(arr)
    rel = np.where(denom > 0, np.abs(arr - frame_arr) / denom,
                   np.abs(arr - frame_arr))
    rel = np.where(np.isnan(arr) & np.isnan(frame_arr), 0.0, rel)
    n_diff = int((~eq).sum())
    max_rel = float(np.nanmax(rel)) if rel.size else 0.0
    if n_diff and max_rel <= _INPUT_FIDELITY_RTOL:
        return "roundtrip_drift", (
            f"{n_diff} frame cell(s) differ from bound input_values "
            f"at max relative diff {max_rel:.3e} — within CSV float64 "
            "serialization ulp loss; the bound input_values matrix "
            "is spliced in as the authoritative declared input"), arr
    return "mismatch", (
        f"{n_diff} frame cell(s) differ from the artifact's bound "
        f"input_values beyond serialization noise (max relative diff "
        f"{max_rel:.3e}) — the persisted frame is not the declared "
        "model input"), arr


def _field_equal(a, b) -> bool:
    """Canonical-domain field equality — tuples and lists normalize
    identically, so a reconstructed config never false-diffs."""
    _, sha256_canonical, _, _ = _engine()
    if a is _ABSENT or b is _ABSENT:
        return a is b
    try:
        return sha256_canonical(a) == sha256_canonical(b)
    except (TypeError, ValueError):
        return a == b


def _env_only_run_manifest_diff(rec_rm, re_rm) -> bool:
    """True when two run_manifest records differ ONLY in their
    embedded environment_digest — env-derived volatility, not a
    semantic drift."""
    if not isinstance(rec_rm, dict) or not isinstance(re_rm, dict):
        return False
    a = {k: v for k, v in rec_rm.items() if k != "environment_digest"}
    b = {k: v for k, v in re_rm.items() if k != "environment_digest"}
    return _field_equal(a, b)


def reexecute_lane(lane: str, artifact_path, frame_path, *,
                   role_manifests_path=None, manifest_key: str = "event",
                   root_id: str | None = None,
                   artifact_relpath: str | None = None,
                   frame_relpath: str | None = None) -> dict:
    """Re-execute one regime lane and compare against the persisted
    frozen artifact.  Purely read-only on the lane root; returns the
    lane report.  Importable and runnable against any root — nothing
    in this function assumes the canonical paths."""
    artifact_path = Path(artifact_path)
    frame_path = Path(frame_path)
    lane_report = {
        "lane": lane,
        "root_id": root_id or lane,
        "artifact": artifact_relpath or artifact_path.name,
        "frame": frame_relpath or frame_path.name,
        "artifact_sha256": None, "frame_sha256": None,
        "sidecars": {}, "checks": {},
        "recorded": {}, "recomputed": {},
        "differences": {"volatile": [], "semantic": []},
        "verdict": "UNAVAILABLE", "problems": []}
    problems = lane_report["problems"]
    checks = lane_report["checks"]

    # 1. sidecar byte-verification on every consumed persisted file
    consumed = {"artifact": artifact_path, "frame": frame_path}
    if role_manifests_path is not None:
        consumed["role_manifests"] = Path(role_manifests_path)
    for label, p in consumed.items():
        ok = p.is_file() and _sidecar_ok(p)
        lane_report["sidecars"][label] = ok
        if not ok:
            problems.append(f"{label} byte verification failed: {p}")
    if problems:
        return lane_report
    lane_report["artifact_sha256"] = _sha(artifact_path)
    lane_report["frame_sha256"] = _sha(frame_path)

    try:
        recorded = json.loads(artifact_path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        problems.append(f"artifact unreadable: {exc}")
        return lane_report
    if not isinstance(recorded, dict):
        problems.append("artifact payload is not a mapping")
        return lane_report
    rec_cfg_payload = recorded.get("config")
    feature_cols = recorded.get("feature_cols")
    if not isinstance(feature_cols, list) or not feature_cols or \
            not all(isinstance(c, str) for c in feature_cols):
        problems.append("artifact feature_cols missing or malformed")
        return lane_report
    lane_report["recorded"] = {
        "status": recorded.get("status"),
        "config_digest": recorded.get("config_digest"),
        "regime_artifact_digest":
            recorded.get("regime_artifact_digest"),
        "freeze_digest": recorded.get("freeze_digest"),
        "train_mask_digest": recorded.get("train_mask_digest"),
        "environment_digest": recorded.get("environment_digest"),
        "required_gates": (recorded.get("stability") or {})
            .get("required_gates"),
        "associable": recorded.get("associable")}

    # the recorded artifact's own envelope must be internally
    # consistent — a tampered baseline is a finding, not a reference
    _, sha256_canonical, _, _ = _engine()
    pre_freeze = {k: v for k, v in recorded.items()
                  if k not in ("frozen", "freeze_digest")}
    checks["recorded_envelope_digest_match"] = _field_equal(
        sha256_canonical({k: v for k, v in pre_freeze.items()
                          if k != "regime_artifact_digest"}),
        recorded.get("regime_artifact_digest"))
    checks["recorded_freeze_digest_match"] = (
        recorded.get("frozen") is True and
        _field_equal(sha256_canonical(pre_freeze),
                     recorded.get("freeze_digest")))

    # 2. strict config reconstruction from the recorded payload
    cfg, cfg_problems = _reconstruct_config(rec_cfg_payload)
    problems.extend(cfg_problems)
    if cfg is None:
        return lane_report
    serialized = _serialized_config(cfg)
    checks["reconstructed_config_digest_match"] = _field_equal(
        sha256_canonical(serialized), recorded.get("config_digest"))

    # daily lane: substitute the role manifest ONLY after proving the
    # persisted manifest record equals the recorded bound copy
    if role_manifests_path is not None:
        try:
            manifests = json.loads(
                Path(role_manifests_path).read_text())
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"role manifests unreadable: {exc}")
            return lane_report
        event_manifest = (manifests or {}).get(manifest_key)
        checks["manifest_substitution_verified"] = _field_equal(
            event_manifest, serialized.get("source_manifest"))
        if not checks["manifest_substitution_verified"]:
            problems.append(
                f"role_manifests[{manifest_key!r}] does not equal the "
                "recorded config.source_manifest — the declared "
                "manifest cannot be substituted")
            return lane_report
        cfg = dataclasses.replace(
            cfg, source_manifest=event_manifest)
        serialized = _serialized_config(cfg)

    # a non-fixture source manifest must byte-verify on this host —
    # freeze re-verifies it, so an unverifiable manifest makes the
    # lane unavailable rather than a freeze error
    _, _, verify_source_evidence, fixture_flag = _engine()
    sm = serialized.get("source_manifest")
    is_fixture, fix_problems = fixture_flag(sm)
    if fix_problems:
        problems.extend(
            f"source_manifest fixture marker invalid: {p}"
            for p in fix_problems)
        return lane_report
    if not is_fixture:
        ev_problems = verify_source_evidence(sm)
        checks["source_manifest_byte_verified"] = not ev_problems
        if ev_problems:
            problems.extend(
                f"source_manifest evidence unverifiable: {p}"
                for p in ev_problems[:4])
            return lane_report
    else:
        checks["source_manifest_byte_verified"] = "fixture"

    # 3. the persisted frame must decode to the bound input matrix
    import pandas as pd
    try:
        df = pd.read_csv(frame_path)
    except (OSError, ValueError) as exc:
        problems.append(f"frame unreadable: {exc}")
        return lane_report
    missing_cols = [c for c in feature_cols if c not in df.columns]
    if missing_cols:
        problems.append(f"frame lacks declared feature columns "
                        f"{missing_cols}")
        return lane_report
    fidelity, prob, decoded = _input_values_match(
        df, feature_cols, recorded)
    checks["input_fidelity"] = fidelity
    if fidelity in (None, "mismatch"):
        problems.append(prob)
        return lane_report
    if fidelity == "roundtrip_drift":
        # disclosed transport noise — the bound input matrix, not the
        # lossy CSV, is the declared model input
        checks["input_fidelity_note"] = prob
        import numpy as np
        declared_dtypes = (
            (recorded.get("input_schema") or {}).get("dtypes") or {})
        for j, col in enumerate(feature_cols):
            series = decoded[:, j]
            dtype = declared_dtypes.get(col)
            if dtype in ("int64", "int32") and \
                    np.isfinite(series).all() and \
                    (series == np.floor(series)).all():
                df[col] = series.astype(np.int64)
            else:
                df[col] = series

    # 4. derived train mask vs the bound mask digest — mask drift is
    # semantic, never a byte problem
    try:
        mask = _train_mask(df, cfg)
    except (ValueError, TypeError) as exc:
        problems.append(f"train mask cannot be derived: {exc}")
        return lane_report
    mask_digest = sha256_canonical(mask.tolist())
    checks["train_mask_digest_match"] = (
        mask_digest == recorded.get("train_mask_digest"))
    if not checks["train_mask_digest_match"]:
        problems.append("derived train_mask_digest differs from the "
                        "recorded digest — the declared lock has "
                        "drifted")

    # 5. re-execute — in memory, writing nothing
    regimes = _engine()[0]
    result = None
    try:
        result = regimes.run_regimes(df, list(feature_cols), mask, cfg)
    except Exception as exc:  # an engine exception is a finding
        problems.append(f"run_regimes raised: {exc}")
    frozen = None
    freeze_error = None
    if isinstance(result, dict):
        try:
            frozen = regimes.freeze_regime_artifact(dict(result))
        except Exception as exc:
            freeze_error = str(exc)
            problems.append(f"freeze_regime_artifact raised: {exc}")
    checks["freeze_ok"] = frozen is not None
    if freeze_error is not None:
        checks["freeze_error"] = freeze_error[:200]
    recomputed_surface = frozen if frozen is not None else (
        result if isinstance(result, dict) else {})
    lane_report["recomputed"] = {
        "status": recomputed_surface.get("status"),
        "reason": recomputed_surface.get("reason"),
        "config_digest": recomputed_surface.get("config_digest"),
        "regime_artifact_digest":
            recomputed_surface.get("regime_artifact_digest"),
        "freeze_digest": recomputed_surface.get("freeze_digest"),
        "train_mask_digest":
            recomputed_surface.get("train_mask_digest"),
        "environment_digest":
            recomputed_surface.get("environment_digest"),
        "required_gates": (recomputed_surface.get("stability") or {})
            .get("required_gates"),
        "associable": recomputed_surface.get("associable")}

    # 6. field-level diff; classify volatile vs semantic
    keys = sorted(set(recorded) | set(recomputed_surface))
    differing = [k for k in keys if not _field_equal(
        recorded.get(k, _ABSENT),
        recomputed_surface.get(k, _ABSENT))]
    env_derived = set()
    if "run_manifest" in differing and _env_only_run_manifest_diff(
            recorded.get("run_manifest"),
            recomputed_surface.get("run_manifest")):
        env_derived.add("run_manifest")
        if "run_manifest_digest" in differing:
            env_derived.add("run_manifest_digest")
    volatile_diff = sorted(
        k for k in differing
        if k in VOLATILE_KEYS or k in env_derived)
    semantic_diff = sorted(
        k for k in differing
        if k not in VOLATILE_KEYS and k not in env_derived)
    if not checks["train_mask_digest_match"] and \
            "train_mask_digest" not in semantic_diff:
        semantic_diff.append("train_mask_digest")
    lane_report["differences"] = {
        "volatile": volatile_diff, "semantic": semantic_diff,
        "n_differing_fields": len(differing)}

    rec_gates = lane_report["recorded"].get("required_gates") or {}
    re_gates = lane_report["recomputed"].get("required_gates") or {}
    checks["required_gates_equality"] = {
        g: (rec_gates.get(g, _ABSENT) is re_gates.get(g, _ABSENT) or
            rec_gates.get(g) == re_gates.get(g))
        for g in sorted(set(rec_gates) | set(re_gates))}
    checks["status_match"] = _field_equal(
        recorded.get("status"), recomputed_surface.get("status"))
    checks["config_digest_match"] = _field_equal(
        recorded.get("config_digest"),
        recomputed_surface.get("config_digest"))
    checks["environment_digest_match"] = _field_equal(
        recorded.get("environment_digest"),
        recomputed_surface.get("environment_digest"))
    # the recomputed artifact may never carry an authority grant —
    # absent or explicitly-false flags only
    checks["authority_flags_all_false_recomputed"] = all(
        recomputed_surface.get(f) in (None, False)
        for f in AUTHORITY_FIELDS)

    digest_equal = _field_equal(
        recorded.get("regime_artifact_digest"),
        recomputed_surface.get("regime_artifact_digest"))
    freeze_equal = _field_equal(
        recorded.get("freeze_digest"),
        recomputed_surface.get("freeze_digest"))
    if semantic_diff or freeze_error is not None or \
            not checks["train_mask_digest_match"]:
        lane_report["verdict"] = "MISMATCH"
    elif digest_equal and freeze_equal:
        lane_report["verdict"] = "REPRODUCED"
    elif differing:
        lane_report["verdict"] = "SEMANTIC_MATCH_ENV_DIFF"
    else:
        # digests disagree without a field-level explanation cannot
        # happen honestly — fail closed
        lane_report["verdict"] = "MISMATCH"
        problems.append("digests disagree but no differing field was "
                        "identified — fail closed")
    return lane_report


def overall_status(lanes: dict) -> str:
    verdicts = [l.get("verdict") for l in lanes.values()]
    if "MISMATCH" in verdicts:
        return "MODEL_REEXECUTION_MISMATCH"
    if "SEMANTIC_MATCH_ENV_DIFF" in verdicts:
        return "MODEL_REEXECUTION_PARTIAL"
    if "UNAVAILABLE" in verdicts or any(v not in (
            "REPRODUCED",) for v in verdicts):
        return "MODEL_REEXECUTION_UNAVAILABLE"
    return "MODEL_REEXECUTED"


def build_report(daily_root: Path, seasonal_root: Path,
                 only: str | None = None) -> dict:
    report = {
        "schema": "P5_MODEL_REEXECUTION_PROOF_V0",
        "activity_id": _uuid.uuid4().hex,
        "proof_scope": "model_reexecution",
        "started_utc": _utc_now(),
        "root_ids": {"daily": "daily_p5a2",
                     "seasonal": "seasonal_v1_current"},
        "claim_scope": "research_only_no_operational_authorization",
        "authority": {f: False for f in AUTHORITY_FIELDS},
        "execution_context": {
            "host_daily_root": str(daily_root),
            "host_seasonal_root": str(seasonal_root),
            "note": "host paths are execution context only — "
                    "evidence references are logical root ids + "
                    "relative paths"},
        "lanes": {}}
    if only in (None, "daily"):
        report["lanes"]["daily"] = reexecute_lane(
            "daily",
            daily_root / DAILY_ARTIFACT_REL,
            daily_root / DAILY_FRAME_REL,
            role_manifests_path=daily_root / DAILY_MANIFESTS_REL,
            manifest_key="event",
            root_id="daily_p5a2",
            artifact_relpath=DAILY_ARTIFACT_REL,
            frame_relpath=DAILY_FRAME_REL)
    if only in (None, "seasonal"):
        report["lanes"]["seasonal"] = reexecute_lane(
            "seasonal",
            seasonal_root / SEASONAL_ARTIFACT_REL,
            seasonal_root / SEASONAL_FRAME_REL,
            root_id="seasonal_v1_current",
            artifact_relpath=SEASONAL_ARTIFACT_REL,
            frame_relpath=SEASONAL_FRAME_REL)
    report["status"] = overall_status(report["lanes"])
    report["completed_utc"] = _utc_now()
    return report


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--daily-root", default=str(DEFAULT_DAILY_ROOT))
    ap.add_argument("--seasonal-root", "--lane-root",
                    dest="seasonal_root",
                    default=str(DEFAULT_SEASONAL_ROOT))
    ap.add_argument("--repo-root", default=str(REPO),
                    help="repo root for engine imports "
                         "(default: this file's repo)")
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--write", action="store_true",
                      help="publish the report to --report-out "
                           "(exclusive-create + sidecar)")
    mode.add_argument("--dry-run", action="store_true",
                      help="full re-execution proof, stdout only — "
                           "writes nothing")
    ap.add_argument("--report-out", default=None,
                    help="new path for the report; required with "
                         "--write, refused with --dry-run")
    ap.add_argument("--only", choices=("daily", "seasonal"),
                    default=None,
                    help="restrict to one lane (verification/debug "
                         "short-circuit; the publishable proof "
                         "report always covers both lanes)")
    args = ap.parse_args(argv)
    repo_root = Path(args.repo_root).resolve()
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    if args.write and not args.report_out:
        print("REFUSED — --write requires --report-out")
        return 2
    if args.dry_run and args.report_out:
        print("REFUSED — --dry-run never writes; drop --report-out")
        return 2
    daily_root = Path(args.daily_root).resolve()
    seasonal_root = Path(args.seasonal_root).resolve()
    report = build_report(daily_root, seasonal_root, only=args.only)
    out = None
    if args.write and args.only:
        print("REFUSED — a publishable proof report must cover both "
              "lanes; --only is a dry-run/debug selector")
        return 2
    if args.write:
        from p5_safe_io import (ExistingEvidenceError,
                                write_once_json, write_once_sidecar)
        out = Path(args.report_out).resolve()
        try:
            write_once_json(out, report, indent=1)
            write_once_sidecar(out)
        except ExistingEvidenceError as exc:
            print(f"REPORT_WRITE_REFUSED: {exc}")
            return 2
    print(json.dumps(report, indent=1))
    print(f"\nstatus: {report['status']}" +
          (f"  -> {out}" if out else "  -> stdout only"))
    return 0 if report["status"] == "MODEL_REEXECUTED" else 1


if __name__ == "__main__":
    sys.exit(main())
