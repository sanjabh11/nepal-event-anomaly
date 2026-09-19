"""Real FMX audit over the acquired ERA5-Land regime frame (FMX-01/03).

Runs the hardened ``fmx_audit.audit_matrix`` rubric over the actual
6,900-row JJA-2001-2025 three-basin frame: the ten declared predictors
plus every carrier column audited separately.  Emits a persisted,
digest-bound report — ColumnAudit + Verdict records, a declared
CutoffRecord, per-column digests, and the feature-role manifest
binding.

Honest semantics: the frame is only read AFTER its feature-role
manifest byte-verifies through ``verify_source_evidence``; the cutoff
is a declared record bound to a retrieval record's completion time;
preprocessing provenance is a persisted record whose fitted-row count
and train-row digest are recomputed from live frame bytes — no
rejection may be suppressed, an unknown cutoff produces
FMX_BLOCKED_CUTOFF, and missing/mismatched provenance produces
FMX_BLOCKED_PROVENANCE.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from nepal.research_v0._hashing import (
    sha256_canonical, verify_source_evidence)
from nepal.science_v0.fmx_audit import ColumnAudit, audit_matrix

#: The ten declared regime predictors — frozen by contract.
PREDICTORS = (
    "t2m_daily", "d2m_daily", "tp_daily", "sf_daily", "sd_daily",
    "wind_speed_daily", "wind_dir_sin", "wind_dir_cos", "rh_daily",
    "pdd_daily")

#: Declared run cutoff — every frame value precedes it; the frame
#: covers JJA 2001-2025 and ERA5-Land publishes with ~5-day lag, so all
#: bytes are available at this cutoff by construction.
CUTOFF_ISO = "2025-10-01T00:00:00Z"

#: Declared source availability lag — ERA5-Land publishes ~5 days
#: behind analysis time; the cutoff record proves the margin against
#: the actual retrieval completion timestamp.
AVAILABILITY_LAG_DAYS = 5

#: Schema tags for the two evidence records this module consumes and
#: emits.  Both are persisted JSON artifacts with their own sha256
#: sidecars — callers may never pass the metadata inline as a claim.
CUTOFF_RECORD_SCHEMA = "CUTOFF_RECORD_V0"
PREPROCESSING_PROVENANCE_SCHEMA = "PREPROCESSING_PROVENANCE_V0"

#: The declared regime-fit train partition (basin_group values).  The
#: preprocessing record must prove its fit covered exactly these rows.
TRAIN_GROUPS = ("koshi", "gandaki")

#: The operative basin universe — anchor-set equality is checked
#: against this, never against a hardcoded carrier string.
FROZEN_BASINS = ("koshi", "gandaki", "karnali")

_PREDICTOR_META = {
    "t2m_daily": ("meteorological_archived_operational", "degC",
                  "(-90, 60)"),
    "d2m_daily": ("meteorological_archived_operational", "degC",
                  "(-90, 60)"),
    "tp_daily": ("meteorological_archived_operational", "mm/day",
                 "[0, 2000)"),
    "sf_daily": ("cryosphere_state", "mm/day w.e.", "[0, 500)"),
    "sd_daily": ("cryosphere_state", "mm w.e.", "[0, 50000)"),
    "wind_speed_daily": ("meteorological_archived_operational", "m/s",
                         "[0, 150)"),
    "wind_dir_sin": ("meteorological_archived_operational", "1",
                     "[-1, 1]"),
    "wind_dir_cos": ("meteorological_archived_operational", "1",
                     "[-1, 1]"),
    "rh_daily": ("meteorological_archived_operational", "%",
                 "[0, 100]"),
    "pdd_daily": ("hydrology_state", "degC-day", "[0, inf)"),
}

#: Non-predictor carriers audited separately — bookkeeping and
#: auxiliary meteorology must never silently enter the predictor
#: matrix.
#: Carrier metadata — group-valued domains are DERIVED from the
#: audited frame at run time (R11.9-27), never hardcoded to a basin
#: set: a fourth group added under a scope amendment is described by
#: the bytes, not by an edit here.
_CARRIER_META = {
    "date": ("observation_metadata", "iso-date", "valid ISO dates"),
    "unit_id": ("observation_metadata", "basin-id", "frame-derived"),
    "basin_group": ("observation_metadata", "basin-id",
                    "frame-derived"),
    "season": ("observation_metadata", "label", "frame-derived"),
    "era": ("observation_metadata", "label", "{pre_2013,post_2013}"),
    "elevation_m": ("terrain_static", "m", "[0, 9000]"),
    "edge_censored": ("observation_metadata", "bool", "{true,false}"),
    "pdd_7day": ("hydrology_state", "degC-day", "[0, inf) — 7-day "
                 "window; edge rows censored"),
    "freezing_height_m": ("meteorological_archived_operational", "m",
                          "[0, 9000]"),
}

_LINEAGE = (
    "cds:reanalysis-era5-land-timeseries",
    "ee:ECMWF/ERA5_LAND/HOURLY",
    "transform:box-mean->hourly->daily(feature_extraction)")

_AVAILABILITY = ("ERA5-Land published reanalysis (~5-day lag); every "
                 "JJA 2001-2025 value precedes the declared run cutoff "
                 "— availability is byte-bound, not inferred")


def _parse_utc(text: Any) -> datetime | None:
    if not isinstance(text, str) or not text.strip():
        return None
    try:
        return datetime.fromisoformat(
            text.replace("Z", "+00:00")).astimezone(timezone.utc)
    except ValueError:
        return None


def _column_digests(frame: pd.DataFrame) -> dict[str, str]:
    """Per-column sha256 over the canonical serialized values."""
    out = {}
    for c in frame.columns:
        payload = frame[c].astype(str).to_json(
            orient="values").encode()
        out[c] = hashlib.sha256(payload).hexdigest()
    return out


def _row_universe_digest(frame: pd.DataFrame) -> str:
    """Digest of the (unit_id, date) row-key universe — order-free."""
    keys = sorted(
        f"{u}|{d}" for u, d in zip(
            frame["unit_id"].astype(str), frame["date"].astype(str)))
    return sha256_canonical(keys)


def _train_row_digest(frame: pd.DataFrame,
                      train_groups: Sequence[str]) -> str:
    mask = frame["basin_group"].isin(list(train_groups))
    keys = sorted(
        f"{u}|{d}" for u, d in zip(
            frame.loc[mask, "unit_id"].astype(str),
            frame.loc[mask, "date"].astype(str)))
    return sha256_canonical(keys)


def build_preprocessing_provenance(
        frame: pd.DataFrame,
        *,
        frame_sha256: str,
        train_groups: Sequence[str] = TRAIN_GROUPS) -> dict:
    """Derive the PREPROCESSING_PROVENANCE_V0 record from live bytes.

    The regime frame is produced by deterministic, row-independent
    transforms (box-mean -> daily aggregation); no scaler or imputer
    was fit.  The record therefore declares that policy honestly and
    binds the actual train partition: fitted_row_count equals the
    count of frame rows in the declared train groups, and
    train_row_digest is the digest of their (unit_id, date) keys —
    recomputed from bytes, never asserted.
    """
    groups = tuple(train_groups)
    mask = frame["basin_group"].isin(list(groups))
    return {
        "schema": PREPROCESSING_PROVENANCE_SCHEMA,
        "frame_sha256": frame_sha256,
        "fit_policy": "deterministic_row_independent — box-mean over "
                      "frozen anchors, hourly->daily aggregation; no "
                      "learned scaler/imputer exists to leak",
        "train_groups": list(groups),
        "fitted_row_count": int(mask.sum()),
        "train_row_digest": _train_row_digest(frame, groups),
        "columns_train_only": [
            c for c in list(PREDICTORS) + list(_CARRIER_META)
            if c in frame.columns],
    }


def cutoff_record_problems(record: Any,
                           *,
                           evidence_root: Path,
                           frame: pd.DataFrame | None = None,
                           ) -> list[str]:
    """Validate a declared CUTOFF_RECORD_V0 against live evidence."""
    problems: list[str] = []
    if not isinstance(record, Mapping):
        return ["cutoff_record must be a mapping"]
    if record.get("schema") != CUTOFF_RECORD_SCHEMA:
        problems.append(
            f"cutoff_record.schema must be {CUTOFF_RECORD_SCHEMA!r}")
    cutoff = _parse_utc(record.get("cutoff_iso"))
    if cutoff is None:
        problems.append("cutoff_record.cutoff_iso is not a valid "
                        "ISO-8601 timestamp")
    rel = record.get("retrieval_record_relpath")
    declared_sha = record.get("retrieval_record_sha256")
    retrieval: dict[str, Any] | None = None
    if not isinstance(rel, str) or not rel.strip():
        problems.append(
            "cutoff_record.retrieval_record_relpath is required")
    elif not isinstance(declared_sha, str) or len(declared_sha) != 64:
        problems.append(
            "cutoff_record.retrieval_record_sha256 must be 64-hex")
    else:
        try:
            blob = (Path(evidence_root) / rel).read_bytes()
        except OSError as exc:
            problems.append(
                f"cutoff_record retrieval record unreadable: {exc}")
        else:
            actual = hashlib.sha256(blob).hexdigest()
            if actual != declared_sha:
                problems.append(
                    "cutoff_record retrieval record digest mismatch — "
                    "the bound retrieval evidence changed")
            else:
                try:
                    retrieval = json.loads(blob)
                except (ValueError, UnicodeDecodeError):
                    problems.append(
                        "cutoff_record retrieval record is not JSON")
    # canonical-identity gate: only the operative retrieval record may
    # back a cutoff — a superseded/interim record fails closed even
    # when its bytes and timestamps are well-formed (R11.9-21)
    if retrieval is not None:
        if retrieval.get("canonical") is not True:
            problems.append(
                "bound retrieval record is not the canonical "
                "operative record (canonical != true)")
        anchors = retrieval.get("anchors")
        if isinstance(anchors, Mapping):
            have = {str(k).lower() for k in anchors}
            if have != set(FROZEN_BASINS):
                problems.append(
                    f"bound retrieval record anchor set {sorted(have)} "
                    f"!= operative universe {sorted(FROZEN_BASINS)}")
        files = retrieval.get("files")
        if isinstance(files, list) and files:
            non_op = [f.get("relpath") for f in files
                      if isinstance(f, Mapping)
                      and "_hma_" not in str(f.get("relpath", ""))
                      and "hma_JJA_ee" not in str(f.get("relpath", ""))
                      and "retrieval/" not in str(f.get("relpath", ""))]
            if non_op:
                problems.append(
                    f"bound retrieval record names non-operative "
                    f"(non-HMA) files: {non_op[:3]}")
    completed = _parse_utc(record.get("retrieval_completed_utc"))
    if completed is None:
        problems.append(
            "cutoff_record.retrieval_completed_utc is not a valid "
            "ISO-8601 timestamp")
    if retrieval is not None and completed is not None:
        actual_end = _parse_utc(retrieval.get("pull_utc_end"))
        if actual_end is None:
            problems.append(
                "bound retrieval record lacks pull_utc_end")
        elif actual_end != completed:
            problems.append(
                "cutoff_record.retrieval_completed_utc does not equal "
                "the bound retrieval record's pull_utc_end")
        chan = retrieval.get("channel_completions")
        if isinstance(chan, Mapping):
            for name, ts in chan.items():
                t = _parse_utc(ts)
                if t is not None and completed < t:
                    problems.append(
                        f"retrieval completion precedes channel "
                        f"{name!r} completion {ts}")
    if not isinstance(record.get("source_version"), str) or \
            not record["source_version"].strip():
        problems.append("cutoff_record.source_version is required")
    if frame is not None and cutoff is not None:
        try:
            max_date = pd.to_datetime(frame["date"]).max()
            if max_date.tzinfo is None:
                max_date = max_date.tz_localize(timezone.utc)
            if max_date >= cutoff:
                problems.append(
                    "frame contains dates on/after the declared "
                    "cutoff — availability cannot be proven")
            elif completed is not None:
                margin = (completed - max_date).days
                if margin < AVAILABILITY_LAG_DAYS:
                    problems.append(
                        f"availability margin {margin}d < declared "
                        f"{AVAILABILITY_LAG_DAYS}d ERA5-Land lag")
        except (KeyError, ValueError, TypeError) as exc:
            problems.append(f"frame dates not parseable: {exc}")
    return problems


def preprocessing_record_problems(
        record: Any,
        *,
        frame: pd.DataFrame,
        frame_sha256: str) -> list[str]:
    """Validate PREPROCESSING_PROVENANCE_V0 against the live frame."""
    problems: list[str] = []
    if not isinstance(record, Mapping):
        return ["preprocessing_record must be a mapping"]
    if record.get("schema") != PREPROCESSING_PROVENANCE_SCHEMA:
        problems.append(
            f"preprocessing_record.schema must be "
            f"{PREPROCESSING_PROVENANCE_SCHEMA!r}")
    if record.get("frame_sha256") != frame_sha256:
        problems.append(
            "preprocessing_record.frame_sha256 does not equal the "
            "verified frame digest")
    groups = record.get("train_groups")
    if not isinstance(groups, (list, tuple)) or not groups or \
            not all(isinstance(g, str) and g.strip() for g in groups):
        problems.append(
            "preprocessing_record.train_groups must be non-empty "
            "strings")
        return problems
    mask = frame["basin_group"].isin(list(groups))
    actual_rows = int(mask.sum())
    if record.get("fitted_row_count") != actual_rows:
        problems.append(
            f"preprocessing_record.fitted_row_count "
            f"{record.get('fitted_row_count')} != actual train rows "
            f"{actual_rows}")
    actual_digest = _train_row_digest(frame, groups)
    if record.get("train_row_digest") != actual_digest:
        problems.append(
            "preprocessing_record.train_row_digest does not match "
            "the live train partition")
    declared_cols = record.get("columns_train_only")
    required = [c for c in list(PREDICTORS) + list(_CARRIER_META)
                if c in frame.columns]
    if not isinstance(declared_cols, (list, tuple)) or \
            set(declared_cols) != set(required):
        problems.append(
            "preprocessing_record.columns_train_only must cover "
            "exactly the audited columns")
    return problems


def build_audits(frame: pd.DataFrame | None = None
                 ) -> list[ColumnAudit]:
    """ColumnAudit set — group-valued carrier domains are derived
    from the audited frame's own unique values (R11.9-27)."""
    derived: dict[str, str] = {}
    if frame is not None:
        for col in ("unit_id", "basin_group", "season"):
            if col in frame.columns:
                vals = sorted(str(v) for v in frame[col].unique())
                derived[col] = "{" + ",".join(vals) + "}"
    audits = []
    for name, (cls, unit, domain) in _PREDICTOR_META.items():
        audits.append(ColumnAudit(
            column_name=name, declared_field_class=cls,
            source_lineage=_LINEAGE,
            availability_semantics=_AVAILABILITY,
            unit=unit, value_domain=domain,
            temporal_window=("2001-06-01T00:00:00Z",
                             "2025-09-30T23:59:59Z"),
            missingness_policy="listwise_declared"))
    for name, (cls, unit, domain) in _CARRIER_META.items():
        audits.append(ColumnAudit(
            column_name=name, declared_field_class=cls,
            source_lineage=("derived:regime_frame_carriers",),
            availability_semantics=_AVAILABILITY,
            unit=unit, value_domain=derived.get(name, domain),
            temporal_window=("2001-06-01T00:00:00Z",
                             "2025-09-30T23:59:59Z"),
            missingness_policy="listwise_declared"))
    return audits


def _blocked(status: str, reason: str,
             manifest: Mapping[str, Any] | None = None) -> dict:
    report: dict[str, Any] = {
        "schema": "FMX_AUDIT_REPORT_V0",
        "status": status,
        "reason": reason,
        "n_reject": 0,
        "n_censored": 0,
        "predictors": list(PREDICTORS),
        "carriers": list(_CARRIER_META)}
    if isinstance(manifest, Mapping):
        report["feature_role_digest"] = sha256_canonical(
            dict(manifest))
    return report


def run_real_fmx(feature_manifest: Mapping[str, Any],
                 *,
                 frame_relpath: str,
                 cutoff_record: Mapping[str, Any] | None,
                 preprocessing_record: Mapping[str, Any] | None,
                 out_dir: Path | None = None) -> dict:
    """Run the rubric over the real frame through its VERIFIED role.

    The feature-role manifest is byte-verified before the frame is
    touched; the frame must be a declared ``source_files`` member; the
    cutoff must be a declared record bound to retrieval completion;
    preprocessing provenance must be a record recomputed from live
    bytes.  Nothing is asserted.
    """
    if not isinstance(feature_manifest, Mapping):
        return _blocked("FMX_BLOCKED_MANIFEST",
                        "feature_manifest must be a seven-key source "
                        "manifest mapping")
    evidence_root = Path(feature_manifest.get("evidence_root", "."))
    role_digest = sha256_canonical(dict(feature_manifest))
    manifest_problems = verify_source_evidence(feature_manifest)
    if manifest_problems:
        return _blocked("FMX_BLOCKED_MANIFEST",
                        "feature role evidence failed byte "
                        f"verification: {manifest_problems}",
                        feature_manifest)
    members = {f.get("relpath"): f.get("sha256")
               for f in feature_manifest.get("source_files", [])
               if isinstance(f, Mapping)}
    if frame_relpath not in members:
        return _blocked(
            "FMX_BLOCKED_FRAME",
            f"frame {frame_relpath!r} is not a declared feature-role "
            "source_files member", feature_manifest)
    frame_path = evidence_root / frame_relpath
    try:
        frame_bytes = frame_path.read_bytes()
    except OSError as exc:
        return _blocked("FMX_BLOCKED_FRAME",
                        f"frame unreadable: {exc}", feature_manifest)
    frame_sha = hashlib.sha256(frame_bytes).hexdigest()
    if frame_sha != members[frame_relpath]:
        return _blocked("FMX_BLOCKED_FRAME",
                        "frame bytes differ from the manifest-declared "
                        "digest — byte substitution fails closed",
                        feature_manifest)
    frame = pd.read_csv(frame_path)
    missing = [c for c in list(PREDICTORS) + list(_CARRIER_META)
               if c not in frame.columns]
    if missing:
        return _blocked(
            "FMX_BLOCKED",
            f"frame missing declared columns {missing}",
            feature_manifest)

    cutoff_problems = cutoff_record_problems(
        cutoff_record, evidence_root=evidence_root, frame=frame)
    if cutoff_problems:
        return _blocked("FMX_BLOCKED_CUTOFF",
                        "; ".join(cutoff_problems), feature_manifest)
    prov_problems = preprocessing_record_problems(
        preprocessing_record, frame=frame, frame_sha256=frame_sha)
    if prov_problems:
        return _blocked("FMX_BLOCKED_PROVENANCE",
                        "; ".join(prov_problems), feature_manifest)

    columns = {c: frame[c].tolist() for c in
               list(PREDICTORS) + list(_CARRIER_META)}
    col_digests = _column_digests(frame)
    predictor_digests = {c: col_digests[c] for c in PREDICTORS}
    train_groups = tuple(preprocessing_record["train_groups"])
    train_rows = int(
        frame["basin_group"].isin(list(train_groups)).sum())
    train_only = {
        c: "train_only" for c in
        preprocessing_record["columns_train_only"]}
    verdicts = audit_matrix(
        columns, build_audits(frame),
        cutoff_iso=str(cutoff_record["cutoff_iso"]),
        preprocessing_provenance=train_only,
        catalog_label_columns=set(),
        feature_digest_set=set(predictor_digests.values()),
        train_row_count=train_rows,
        preprocessing_fit_rows=int(
            preprocessing_record["fitted_row_count"]))
    rejects = [v for v in verdicts if v.verdict == "reject"]
    censored = [v for v in verdicts if v.verdict == "censored"]
    report = {
        "schema": "FMX_AUDIT_REPORT_V0",
        "frame": str(Path(frame_relpath).name),
        "frame_relpath": frame_relpath,
        "frame_sha256": frame_sha,
        "feature_role_digest": role_digest,
        "n_rows": len(frame),
        "row_universe_digest": _row_universe_digest(frame),
        "cutoff_record": dict(cutoff_record),
        "cutoff_record_digest": sha256_canonical(dict(cutoff_record)),
        "preprocessing_provenance": dict(preprocessing_record),
        "preprocessing_provenance_digest": sha256_canonical(
            dict(preprocessing_record)),
        "predictors": list(PREDICTORS),
        "carriers": list(_CARRIER_META),
        "column_digests": col_digests,
        "predictor_digest_set": predictor_digests,
        "semantic_matrix_digest": sha256_canonical(
            predictor_digests),
        "audits": [
            {"column_name": a.column_name,
             "declared_field_class": a.declared_field_class,
             "source_lineage": list(a.source_lineage),
             "availability_semantics": a.availability_semantics,
             "unit": a.unit, "value_domain": a.value_domain,
             "temporal_window": list(a.temporal_window),
             "missingness_policy": a.missingness_policy}
            for a in build_audits()],
        "verdicts": [
            {"column_name": v.column_name, "verdict": v.verdict,
             "checks_fired": list(v.checks_fired),
             "reasons": list(v.reasons)}
            for v in verdicts],
        "n_reject": len(rejects),
        "n_censored": len(censored),
        "status": ("FMX_BLOCKED" if rejects else "FMX_PASS")}
    if out_dir is not None:
        out_dir = Path(out_dir)
        out = out_dir / "fmx_audit_report_v0.json"
        b = json.dumps(report, indent=2, sort_keys=True).encode()
        out.write_bytes(b)
        out.with_suffix(".json.sha256").write_text(
            hashlib.sha256(b).hexdigest() + "\n")
    return report
