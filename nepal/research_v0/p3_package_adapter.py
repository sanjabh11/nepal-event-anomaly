"""P3 intake output -> frozen ten-key runner package (INT-01/02, OP-01).

``p3_intake.run_intake`` emits a *research* package (schema
``P3_EVENT_PACKAGE_V0``) carrying serialized typed records plus
report/ledger sections.  The frozen GLOF runner contract admits a
different, exact ten-key surface::

    source_record, event_labels, opportunities, controls,
    holdout_plan, source_manifest_digest, event_digest,
    opportunity_digest, control_digest, holdout_digest

plus the optional ratified ``run_evidence_manifest`` wrapper key.

This module is the explicit adapter between the two shapes.  It:

- re-deserializes every section into typed records and rejects on any
  ``problems()`` — untrusted intake output can never reach the runner;
- recomputes all section digests from the *decoded* content (never
  trusts caller-supplied digests);
- preserves the lake-level opportunity evidence verbatim (Option A
  frame: ``pdgl:<GL_ID>`` unit IDs) and binds the lake-to-basin linkage
  as a separate digest-bound artifact — no silent ID coercion;
- builds the four distinct role manifests (event / opportunity /
  feature / sidecar) and instantiates the ratified
  ``RunEvidenceManifestV0`` wrapper with all four roles present;
- keeps ``SourceRecordV0`` at ``CANDIDATE_ONLY`` / ``UNREVIEWED`` —
  independent review and adjudication are owner-side gates this module
  cannot and must not satisfy.

Nothing here authorizes intake, promotion, or any operational claim.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from ._hashing import sha256_canonical, verify_source_evidence
from .policy import EventTimeClass
from .records import (
    ControlWindowV0, EventLabelV0, HoldoutPlanV0,
    ObservationOpportunityV0, SourceRecordV0,
    RunEvidenceManifestV0, deserialize_record)
from .source_intake import build_source_manifest

P3_SCHEMA = "P3_EVENT_PACKAGE_V0"

#: Frozen runner package — required ten-key surface (audit contract).
RUNNER_PACKAGE_KEYS = (
    "source_record", "event_labels", "opportunities", "controls",
    "holdout_plan", "source_manifest_digest", "event_digest",
    "opportunity_digest", "control_digest", "holdout_digest")

_P3_SECTIONS = {
    "event_labels": ("event_labels", "event_digest"),
    "opportunities": ("observation_opportunities",
                      "opportunity_digest"),
    "controls": ("controls", "control_digest"),
    "holdout_plan": ("holdout_plan", "holdout_digest"),
}


def load_p3_package(path: Path) -> dict:
    """Read a P3 research package, verifying its sidecar digest first.

    The package is evidence — bytes are never trusted without the
    recorded SHA-256 match (fail closed on missing/mismatched sidecar).
    """
    path = Path(path)
    sidecar = path.with_name(path.name + ".sha256")
    if not sidecar.exists():
        raise ValueError(f"p3 package lacks digest sidecar: {sidecar}")
    declared = sidecar.read_text().strip().split()[0]
    actual = hashlib.sha256(path.read_bytes()).hexdigest()
    if actual != declared:
        raise ValueError(
            f"p3 package digest mismatch: {actual} != {declared}")
    pkg = json.loads(path.read_text(encoding="utf-8"))
    if pkg.get("schema") != P3_SCHEMA:
        raise ValueError(
            f"unexpected package schema {pkg.get('schema')!r}")
    return pkg


def _decode_section(name: str, section: Any) -> list[dict]:
    """Re-deserialize one package section into typed records and
    return canonical dicts.  Any record-level problem rejects."""
    if name == "holdout_plan":
        recs = [deserialize_record(section)]
        single = True
    else:
        if not isinstance(section, list):
            raise ValueError(f"{name} section must be a list")
        recs = [deserialize_record(r) for r in section]
        single = False
    out = []
    for i, rec in enumerate(recs):
        probs = rec.problems()
        if probs:
            raise ValueError(
                f"{name}[{i}] typed record inadmissible: {probs}")
        out.append(rec.to_dict())
    return out[0] if single else out


def build_runner_package(
        p3_package: Mapping[str, Any],
        *,
        source_record: SourceRecordV0,
        event_manifest: Mapping[str, Any],
        run_evidence: RunEvidenceManifestV0 | None = None) -> dict:
    """Translate the P3 research package into the frozen runner shape.

    Sections are decoded to typed records (problems() must be empty),
    re-serialized, and digested from content — the emitted digests are
    recomputed, never copied from intake output.
    """
    if not isinstance(p3_package, Mapping):
        raise ValueError("p3_package must be a mapping")
    if p3_package.get("schema") != P3_SCHEMA:
        raise ValueError(
            f"p3_package schema {p3_package.get('schema')!r} "
            f"!= {P3_SCHEMA!r}")
    if not isinstance(source_record, SourceRecordV0):
        raise ValueError("source_record must be SourceRecordV0")
    sr_problems = source_record.problems()
    if sr_problems:
        raise ValueError(
            f"source_record inadmissible: {sr_problems}")
    ev_problems = verify_source_evidence(event_manifest)
    if ev_problems:
        raise ValueError(
            f"event manifest evidence problems: {ev_problems}")

    package: dict[str, Any] = {
        "source_record": source_record.to_dict()}
    for out_key, (p3_key, dkey) in _P3_SECTIONS.items():
        if p3_key not in p3_package:
            raise ValueError(
                f"p3_package lacks required section {p3_key!r}")
        decoded = _decode_section(out_key, p3_package[p3_key])
        package[out_key] = decoded
        package[dkey] = sha256_canonical(decoded)
    package["source_manifest_digest"] = sha256_canonical(
        dict(event_manifest))
    if sorted(package) != sorted(RUNNER_PACKAGE_KEYS):
        raise ValueError(
            f"runner package key drift: {sorted(package)}")
    if run_evidence is not None:
        w_problems = run_evidence.problems() + \
            run_evidence.verify_problems()
        if w_problems:
            raise ValueError(
                f"run_evidence wrapper inadmissible: {w_problems}")
        package["run_evidence_manifest"] = run_evidence.to_dict()
    return package


def build_lake_basin_linkage(anchor_record: Mapping[str, Any]) -> dict:
    """Explicit digest-bound pdgl:<GL_ID> -> basin linkage (OP-01).

    The mapping comes from the anchor derivation record's
    ``basin_coverage`` — the same digested PDGL inventory used for the
    anchors.  Lakes absent from the coverage are not invented; they
    land in ``unmapped`` and opportunities for them stay censored.
    """
    coverage = anchor_record.get("basin_coverage")
    if not isinstance(coverage, Mapping):
        raise ValueError("anchor record lacks basin_coverage")
    linkage: dict[str, str] = {}
    conflicts: list[dict] = []
    for basin, info in coverage.items():
        for gid in info.get("gl_ids", ()):
            unit = f"pdgl:{gid}"
            if unit in linkage and linkage[unit] != basin.lower():
                conflicts.append(
                    {"unit_id": unit,
                     "basins": [linkage[unit], basin.lower()]})
            linkage[unit] = basin.lower()
    return {
        "schema": "LAKE_BASIN_LINKAGE_V0",
        "rule": "basin_coverage from anchor_derivation_record.json — "
                "RDS7952-attributed PDGL inventory membership; "
                "hydrological attribution, no agent-chosen geography",
        "source_relpath": "retrieval/anchor_derivation_record.json",
        "source_sha256": hashlib.sha256(
            json.dumps(dict(anchor_record), sort_keys=True)
            .encode()).hexdigest(),
        "linkage": linkage,
        "conflicts": conflicts,
        "n_lakes": len(linkage)}


def build_event_manifest(evidence_root: Path) -> dict:
    """Event role: HMAGLOFDB acquired bytes + derived intake artifacts."""
    def dg(rel):
        return hashlib.sha256((evidence_root / rel).read_bytes()) \
            .hexdigest()
    files = [
        {"relpath": "glof-events/HMAGLOFDB-v1.3.0.zip",
         "sha256": dg("glof-events/HMAGLOFDB-v1.3.0.zip")},
        {"relpath": "glof-events/HMAGLOFDB.csv",
         "sha256": dg("glof-events/HMAGLOFDB.csv")},
        {"relpath": "glof-events/p3_event_package_v0.json",
         "sha256": dg("glof-events/p3_event_package_v0.json")},
    ]
    return build_source_manifest(
        evidence_root,
        source_id="icimod_hmaglofdb_v1_3_0",
        source_version="1.3.0",
        source_files=files,
        units=["koshi", "gandaki", "karnali", "bagmati"],
        # the event manifest is the run-level authorization surface:
        # regime_config.source_manifest must equal it (package digest
        # binding), so its allowlist declares BOTH the event schema
        # fields AND the predictor columns the run is authorized to
        # fit — a predictor absent here can never enter a governed run
        feature_allowlist=["GF_ID", "basin", "interval_start",
                           "interval_end", "declared_precision",
                           "mechanism"] + list(_PREDICTORS),
        lineage="event role: HMAGLOFDB v1.3.0 acquired zip + verified "
                "extracted member + P3 intake package; "
                "P3_EVENT_PACKAGE_V0")


def build_opportunity_manifest(evidence_root: Path) -> dict:
    """Opportunity role: PDGL inventory + explicit linkage artifact."""
    def dg(rel):
        return hashlib.sha256((evidence_root / rel).read_bytes()) \
            .hexdigest()
    files = [
        {"relpath": "glof-lakes/icimod_pdgl_2015_rds1971950.zip",
         "sha256": dg("glof-lakes/icimod_pdgl_2015_rds1971950.zip")},
        {"relpath": "glof-lakes/lake_to_basin_linkage_v0.json",
         "sha256": dg("glof-lakes/lake_to_basin_linkage_v0.json")},
    ]
    return build_source_manifest(
        evidence_root,
        source_id="icimod_pdgl_2015_opportunity_frame",
        source_version="rds1971950",
        source_files=files,
        units=["koshi", "gandaki", "karnali"],
        feature_allowlist=["GL_ID"],
        lineage="opportunity role: ICIMOD PDGL 2015 inventory "
                "(Option A frame, 47 lakes) + digest-bound lake-to-basin "
                "linkage derived from RDS7952 attribution")


def build_feature_manifest(evidence_root: Path) -> dict:
    """Feature role: both reanalysis channels bound distinctly (MULTI-01).

    CDS timeseries (5 vars) and EE ERA5_LAND/HOURLY snow (sd+sf) keep
    separate channel identities in lineage; derived frames and
    provenance are bound so the transform chain is byte-anchored.
    """
    def dg(rel):
        return hashlib.sha256((evidence_root / rel).read_bytes()) \
            .hexdigest()
    files = []
    for basin in ("koshi", "gandaki", "karnali"):
        files.append({
            "relpath": f"era5-multibasin/era5land_ts_{basin}"
                       f"_hma_2001-2025.zip",
            "sha256": dg(f"era5-multibasin/era5land_ts_{basin}"
                         f"_hma_2001-2025.zip")})
    for basin in ("koshi", "gandaki", "karnali"):
        for year in range(2001, 2026):
            rel = (f"era5-multibasin/era5land_snow_{basin}_{year}"
                   "_hma_JJA_ee.csv")
            files.append({"relpath": rel, "sha256": dg(rel)})
    for rel in (
            "era5-multibasin/features/features_koshi_hma_jja_2001_2025.csv",
            "era5-multibasin/features/features_gandaki_hma_jja_2001_2025.csv",
            "era5-multibasin/features/features_karnali_hma_jja_2001_2025.csv",
            "era5-multibasin/features/regime_frame_hma_jja_2001_2025.csv",
            "era5-multibasin/features/regime_frame_provenance.json",
            "retrieval/basin_model_elevations.json",
            "retrieval/anchor_derivation_record.json",
            "retrieval/retrieval_record_era5_hma_operative.json"):
        files.append({"relpath": rel, "sha256": dg(rel)})
    return build_source_manifest(
        evidence_root,
        source_id="era5_land_multibasin_anchor_frames",
        source_version="jja-2001-2025",
        source_files=files,
        units=["koshi", "gandaki", "karnali"],
        feature_allowlist=list(_PREDICTORS),
        lineage="feature role: channel A = CDS reanalysis-era5-land-"
                "timeseries (t2m d2m u10 v10 tp, contiguous 2001-2025); "
                "channel B = Google Earth Engine ECMWF/ERA5_LAND/HOURLY "
                "(snow_depth_water_equivalent + snowfall_hourly, JJA) — "
                "distinct source identities per MULTI-01; box-mean over "
                "frozen HMA anchors, hourly->daily via "
                "feature_extraction; derived regime frame + provenance "
                "bound")


_PREDICTORS = (
    "t2m_daily", "d2m_daily", "tp_daily", "sf_daily", "sd_daily",
    "wind_speed_daily", "wind_dir_sin", "wind_dir_cos", "rh_daily",
    "pdd_daily")


def build_sidecar_manifest(evidence_root: Path) -> dict:
    """Sidecar role: licence snapshots, retrieval records, ledgers,
    and every control document the run consumes (R11.9-19).

    Bound control documents include the review packet, holdout gate
    report, anchor derivation record, FMX report, cutoff record,
    preprocessing provenance, and any review report present under
    ``retrieval/`` — a control document absent from this role is
    unbound and may not be consumed.  The runner package,
    descriptive receipt, and replay report are deliberately NOT bound
    here: they are downstream products of the wrapper (binding them
    would be self-referential — the package embeds this manifest).
    They carry their own sha256 sidecars and are cross-checked by the
    replay gate instead.
    """
    def dg(rel):
        return hashlib.sha256((evidence_root / rel).read_bytes()) \
            .hexdigest()
    files = []
    for rel in sorted(
            str(p.relative_to(evidence_root))
            for p in list(evidence_root.glob("licence/*.json"))
            + list(evidence_root.glob("retrieval/*review*.json"))
            + list(evidence_root.glob(
                "retrieval/*adjudication*.json"))) + [
            "licence/HMAGLOFDB_LICENSE.txt",
            "licence/PDGL_metadata.xml",
            "licence/PDGL_DataDownloadAgreement.pdf",
            "licence/RDS7952_Data Download Agreement.pdf",
            "retrieval/retrieval_record_hmaglofdb.json",
            "retrieval/retrieval_record_pdgl_lakes.json",
            "retrieval/retrieval_record_hma_inventory.json",
            "retrieval/retrieval_record_hkh_basins.json",
            "retrieval/retrieval_record_era5_snow_ee.json",
            "retrieval/retrieval_record_era5_multibasin.json",
            "retrieval/route_probe_era5_timeseries.json",
            "retrieval/p5_coverage_ledger_20260919.json",
            "retrieval/p3_review_packet_v0.json",
            "retrieval/holdout_feature_gate_report.json",
            "retrieval/anchor_derivation_record.json",
            "retrieval/hydrology_adjudication_v0.json",
            "retrieval/p5_amendment_v2_temporal_holdout.json",
            "era5-multibasin/features/fmx_audit_report_v0.json",
            "era5-multibasin/features/cutoff_record_v0.json",
            "era5-multibasin/features/preprocessing_provenance_v0.json"
            ]:
        p = evidence_root / rel
        if p.exists() and rel not in {f["relpath"] for f in files}:
            files.append({"relpath": rel, "sha256": dg(rel)})
    return build_source_manifest(
        evidence_root,
        source_id="p5_sidecar_records",
        source_version="2026-09-19",
        source_files=files,
        units=["koshi", "gandaki", "karnali"],
        feature_allowlist=["retrieval_records", "licence_snapshots",
                           "coverage_ledger", "control_documents"],
        lineage="sidecar role: licence snapshots (4 role sources), "
                "retrieval records, coverage ledger, review packet, "
                "holdout gate report, anchor derivation, FMX/cutoff/"
                "preprocessing control records — licence metadata is "
                "not scientific qualification; run products (package, "
                "receipt, replay report) are self-sidecarred, not "
                "bound here, to avoid a self-referential manifest")


def build_all_role_manifests(evidence_root: Path) -> dict:
    """All four distinct role manifests for the real run."""
    return {
        "event": build_event_manifest(evidence_root),
        "opportunity": build_opportunity_manifest(evidence_root),
        "feature": build_feature_manifest(evidence_root),
        "sidecar": build_sidecar_manifest(evidence_root)}


def build_p3_source_record(
        evidence_root: Path | None = None) -> SourceRecordV0:
    """Source record — posture is EVIDENCE_VERIFIED only when the
    adjudication sidecar exists and digests bind (R11.9-28/29).

    Two reviews + an adjudication record live under
    ``retrieval/``; when they are absent the record honestly
    degrades to CANDIDATE_ONLY/UNREVIEWED — posture is derived from
    evidence, never asserted.
    """
    verified = False
    sidecar_rel = ""
    sidecar_sha = ""
    if evidence_root is not None:
        side = Path(evidence_root) / "retrieval" / \
            "source_evidence_sidecar_v0.json"
        adj = Path(evidence_root) / "retrieval" / \
            "adjudication_record_v0.json"
        if side.exists() and adj.exists():
            body = json.loads(side.read_bytes())
            adj_body = json.loads(adj.read_bytes())
            if body.get("decision") == "VERIFIED" and \
                    adj_body.get("result", "").startswith(
                        "evidence_review_state="
                        "INDEPENDENTLY_VERIFIED"):
                verified = True
                sidecar_rel = \
                    "retrieval/source_evidence_sidecar_v0.json"
                sidecar_sha = hashlib.sha256(
                    side.read_bytes()).hexdigest()
    return SourceRecordV0(
        source_id="icimod_hmaglofdb_v1_3_0",
        provider="ICIMOD RDS",
        doi_or_url="10.26066/RDS.1973283",
        version="1.3.0",
        as_of_date="2026-09-19",
        license_id="CC BY 4.0",
        redistribution="permitted",
        geography="Hindu Kush Himalaya (Nepal + adjacent headwaters)",
        temporal_coverage="1533-2025",
        event_time_class=EventTimeClass.COARSE_OR_UNRESOLVED.value,
        spatial_semantics="lake_point",
        observation_method="published GLOF event database",
        non_event_frame="ICIMOD PDGL 2015 lake inventory (Option A)",
        update_cadence="irregular",
        access_status="acquired_byte_bound",
        posture="EVIDENCE_VERIFIED" if verified else "CANDIDATE_ONLY",
        license_notes="CC BY 4.0; licence snapshot bound in sidecar role",
        evidence_sidecar_path=sidecar_rel,
        evidence_sidecar_sha256=sidecar_sha,
        evidence_as_of="2026-09-19" if verified else "",
        evidence_review_state=("INDEPENDENTLY_VERIFIED" if verified
                               else "UNREVIEWED"))
