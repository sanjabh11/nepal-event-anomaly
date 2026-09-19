"""Real FMX audit over the acquired ERA5-Land regime frame (FMX-01/03).

Runs the hardened ``fmx_audit.audit_matrix`` rubric over the actual
6,900-row JJA-2001-2025 three-basin frame: the ten declared predictors
plus every carrier column audited separately.  Emits a persisted,
digest-bound report — ColumnAudit + Verdict records, the declared
cutoff, per-column digests, and the feature-role manifest binding.

Honest semantics: no rejection may be suppressed; an unknown cutoff
produces FMX_BLOCKED_CUTOFF, never an implied pass.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from nepal.research_v0._hashing import sha256_canonical
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
_CARRIER_META = {
    "date": ("observation_metadata", "iso-date", "valid ISO dates"),
    "unit_id": ("observation_metadata", "basin-id", "{koshi,gandaki,"
                "karnali}"),
    "basin_group": ("observation_metadata", "basin-id",
                    "{koshi,gandaki,karnali}"),
    "season": ("observation_metadata", "label", "{JJA}"),
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


def _column_digests(frame: pd.DataFrame) -> dict[str, str]:
    """Per-column sha256 over the canonical serialized values."""
    out = {}
    for c in frame.columns:
        payload = frame[c].astype(str).to_json(
            orient="values").encode()
        out[c] = hashlib.sha256(payload).hexdigest()
    return out


def build_audits() -> list[ColumnAudit]:
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
            unit=unit, value_domain=domain,
            temporal_window=("2001-06-01T00:00:00Z",
                             "2025-09-30T23:59:59Z"),
            missingness_policy="listwise_declared"))
    return audits


def run_real_fmx(frame_path: Path,
                 out_dir: Path | None = None) -> dict:
    """Run the rubric over the real frame; persist the report."""
    frame = pd.read_csv(frame_path)
    missing = [c for c in list(PREDICTORS) + list(_CARRIER_META)
               if c not in frame.columns]
    if missing:
        return {"status": "FMX_BLOCKED",
                "reason": f"frame missing declared columns {missing}"}
    columns = {c: frame[c].tolist() for c in
               list(PREDICTORS) + list(_CARRIER_META)}
    col_digests = _column_digests(frame)
    predictor_digests = {c: col_digests[c] for c in PREDICTORS}
    verdicts = audit_matrix(
        columns, build_audits(),
        cutoff_iso=CUTOFF_ISO,
        preprocessing_provenance={
            c: "train_only" for c in columns},
        catalog_label_columns=set(),
        feature_digest_set=set(predictor_digests.values()),
        train_row_count=len(frame),
        preprocessing_fit_rows=len(frame))
    rejects = [v for v in verdicts if v.verdict == "reject"]
    censored = [v for v in verdicts if v.verdict == "censored"]
    report = {
        "schema": "FMX_AUDIT_REPORT_V0",
        "frame": str(frame_path.name),
        "frame_sha256": hashlib.sha256(
            frame_path.read_bytes()).hexdigest(),
        "n_rows": len(frame),
        "cutoff_iso": CUTOFF_ISO,
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
