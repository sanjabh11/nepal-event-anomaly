"""Derive the India Phase-0 feasibility gate from local ledgers only.

The report intentionally cannot authorize acquisition.  It separates
catalog rows, adjudicated independent episodes, canonical lakes, and
verified non-event controls, and keeps the gate pending while
adjudication, evidence verification, or identity reconciliation is
incomplete.  Every input must pass its own validator and carry a
matching ``.sha256`` sidecar; a citation is not evidence until it
resolves to a BYTES_VERIFIED record in the evidence register.  A future
precision simulation must be supplied as a separate, versioned input
before an event-weather canary is considered.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
import sys
sys.path.insert(0, str(ROOT / "scripts"))
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402
import india_event_adjudication as event_adjudication  # noqa: E402
import india_event_crosswalk as event_crosswalk  # noqa: E402
import india_evidence_register as evidence_register  # noqa: E402
import india_lake_frame as lake_frame  # noqa: E402
import validate_nepal_science_gate_checklist as science_gate_checklist  # noqa: E402


SCHEMA = "INDIA_FEASIBILITY_REPORT_V0"
DEFAULT_CHECKLIST = science_gate_checklist.DEFAULT_CHECKLIST
AUTHORITY_FLAGS = {
    "bulk_acquisition_authorized": False,
    "weather_download_authorized": False,
    "satellite_bulk_authorized": False,
    "seismic_waveform_authorized": False,
    "forecast_authorized": False,
    "warning_authorized": False,
    "detector_authorized": False,
    "odds_authorized": False,
    "causal_authorized": False,
    "operational_authorized": False,
}
TERMINAL_ELIGIBILITY = {"ELIGIBLE", "INELIGIBLE", "UNCERTAIN"}
MECHANISM_CERTAINTY = {"CONFIRMED", "PROBABLE"}


def sha256_file(path: str | Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _verify_sidecar(path: Path) -> None:
    """Fail closed unless ``<path>.sha256`` matches the live bytes."""
    sidecar = Path(str(path) + ".sha256")
    if not sidecar.is_file():
        raise ValueError(f"missing evidence sidecar: {sidecar}")
    token = sidecar.read_text(encoding="utf-8").split()
    if not token or token[0] != sha256_file(path):
        raise ValueError(f"sidecar digest mismatch: {sidecar}")


def _load_verified_json(path: str | Path, validator, label: str) -> dict:
    """Load a governed JSON input, verify its sidecar, run its validator."""
    path = Path(path)
    _verify_sidecar(path)
    doc = json.loads(path.read_text(encoding="utf-8"))
    problems = validator(doc)
    if problems:
        raise ValueError(f"{label} validation failed: " + "; ".join(problems))
    return doc


def _independent_exact(
        records: list[dict[str, Any]],
        verified: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    eligible = []
    for record in records:
        if not isinstance(record, dict):
            continue
        adj = record.get("adjudication", {})
        episode = record.get("episode", {})
        identity = record.get("lake_identity", {})
        date = record.get("date", {})
        if not isinstance(adj, dict):
            adj = {}
        if not isinstance(episode, dict):
            episode = {}
        if not isinstance(identity, dict):
            identity = {}
        if not isinstance(date, dict):
            date = {}
        has_identity = bool(identity.get("canonical_lake_id") or
                            identity.get("status") == "SOURCE_ID_PRESENT")
        catalog = record.get("catalog_fields", {})
        if not isinstance(catalog, dict):
            catalog = {}
        resolved = evidence_register.resolved_evidence_ids(
            adj.get("evidence_citations"), verified,
            date.get("start"), date.get("end"),
            country="India", basin=catalog.get("river_basin") or None)
        mechanism_ok = (adj.get("mechanism_certainty") in MECHANISM_CERTAINTY
                        and bool(adj.get("mechanism")))
        if (adj.get("eligibility") == "ELIGIBLE"
                and adj.get("review_state") == "COMPLETED"
                and adj.get("reviewer_ids")
                and adj.get("location_confirmed") is True
                and adj.get("territory_status") == "IN_COUNTRY"
                and mechanism_ok
                and resolved
                and has_identity
                and episode.get("candidate_episode_id")
                and episode.get("independence_status") == "INDEPENDENT"
                and date.get("precision") == "day"):
            eligible.append(record)
    return eligible


def _evidence_verified_event(
        record: dict[str, Any],
        verified: dict[str, dict[str, Any]]) -> bool:
    adj = record.get("adjudication") if isinstance(record, dict) else None
    date = record.get("date") if isinstance(record, dict) else None
    if not isinstance(adj, dict) or not isinstance(date, dict):
        return False
    catalog = record.get("catalog_fields", {})
    if not isinstance(catalog, dict):
        catalog = {}
    return bool(evidence_register.resolved_evidence_ids(
        adj.get("evidence_citations"), verified,
        date.get("start"), date.get("end"),
        country="India", basin=catalog.get("river_basin") or None))


def _review_complete(record: dict[str, Any]) -> bool:
    if not isinstance(record, dict):
        return False
    adj = record.get("adjudication", {})
    if not isinstance(adj, dict):
        return False
    evidence = record.get("evidence", {})
    if not isinstance(evidence, dict):
        evidence = {}
    return bool(
        adj.get("review_state") == "COMPLETED"
        and adj.get("eligibility") in TERMINAL_ELIGIBILITY
        and adj.get("reviewer_ids")
        and (adj.get("evidence_citations") or evidence.get("references"))
    )


def _artifact_revision(out_path: Path | None) -> int:
    name = Path(out_path).name if out_path else ""
    m = re.search(r"_V(\d+)", name)
    return int(m.group(1)) if m else 0


def _lake_denominators(
        lake_records: list[dict[str, Any]],
        verified: dict[str, dict[str, Any]]) -> dict[str, int]:
    """Canonical-lake denominators: rows are source rows, not lakes.

    India-only accounting: records classified OUTSIDE remain visible but
    never enter India denominators; UNCERTAIN/UNASSESSED territory records
    block the frame until classified; only IN_COUNTRY reconciled
    identities contribute canonical lakes.
    """
    canonical_ids: set[str] = set()
    unresolved = 0
    outside = 0
    uncertain = 0
    control_ids: set[str] = set()
    control_candidates = 0
    unverified_controls = 0
    control_intervals: set[tuple[str, str, str]] = set()
    non_event_lake_years: set[tuple[str, int]] = set()
    observable_lake_years: set[tuple[str, int]] = set()
    observation_unknown = 0
    observation_partial = 0
    observation_breach = 0
    observation_non_event = 0
    observation_full = 0
    for record in lake_records:
        if not isinstance(record, dict):
            continue
        # Observation statuses partition the entire source-row frame,
        # including OUTSIDE and unresolved rows. India-only identity and
        # control denominators are filtered below.
        obs = record.get("observation", {})
        if isinstance(obs, dict):
            status = obs.get("status")
            observation_unknown += status == "UNKNOWN"
            observation_partial += status == "PARTIAL"
            observation_breach += status == "KNOWN_BREACH"
            observation_non_event += status == "VERIFIED_NON_EVENT"
            observation_full += obs.get("completeness") == "FULL"
        location = record.get("location", {})
        if not isinstance(location, dict):
            location = {}
        territory = location.get("territory_status")
        if territory == "OUTSIDE":
            outside += 1
            continue
        if territory != "IN_COUNTRY":
            uncertain += 1
            continue
        lake = record.get("lake", {})
        if not isinstance(lake, dict):
            lake = {}
        canonical = lake.get("canonical_lake_id")
        if lake.get("identity_status") == "RECONCILED" and isinstance(
                canonical, str) and canonical.strip():
            canonical_ids.add(canonical)
        else:
            unresolved += 1
            continue
        if not isinstance(obs, dict):
            continue
        status = obs.get("status")
        if obs.get("completeness") == "FULL":
            years = obs.get("observed_years", [])
            if isinstance(years, list):
                observable_lake_years.update(
                    (canonical, year) for year in years
                    if isinstance(year, int) and not isinstance(year, bool))
        if obs.get("control_eligible") is not True:
            continue
        control_candidates += 1
        resolved = evidence_register.resolved_evidence_ids(
            obs.get("evidence_refs"), verified,
            obs.get("at_risk_start"), obs.get("at_risk_end"),
            country="India", basin=location.get("basin") or None)
        if not resolved:
            unverified_controls += 1
            continue
        start, end = obs.get("at_risk_start"), obs.get("at_risk_end")
        if isinstance(start, str) and isinstance(end, str):
            control_intervals.add((canonical, start, end))
            if status == "VERIFIED_NON_EVENT" and obs.get("completeness") == "FULL":
                control_ids.add(canonical)
                observed_years = set(obs.get("observed_years", []))
                start_date = dt.date.fromisoformat(start)
                end_date = dt.date.fromisoformat(end)
                for year in observed_years:
                    if not isinstance(year, int):
                        continue
                    first = dt.date(year, 1, 1)
                    last = dt.date(year, 12, 31)
                    if start_date <= first and end_date >= last:
                        non_event_lake_years.add((canonical, year))
    return {
        "mapped_lake_rows": len(lake_records),
        "in_country_canonical_lakes": len(canonical_ids),
        "outside_lakes": outside,
        "uncertain_territory_lakes": uncertain,
        "unresolved_lake_identities": unresolved,
        "control_candidates": control_candidates,
        "verified_non_event_controls": len(control_ids),
        "verified_non_event_intervals": len(control_intervals),
        "verified_non_event_lake_years": len(non_event_lake_years),
        "observable_lake_years": len(observable_lake_years),
        "unverified_control_candidates": unverified_controls,
        "observation_unknown_lake_rows": observation_unknown,
        "observation_partial_lake_rows": observation_partial,
        "observation_known_breach_lake_rows": observation_breach,
        "observation_verified_non_event_lake_rows": observation_non_event,
        "observation_full_lake_rows": observation_full,
    }


def _event_screen(unreviewed: int, independent_exact: int) -> str:
    if unreviewed:
        return "ADJUDICATION_INCOMPLETE"
    if independent_exact < 10:
        return "CLOSE_EVENT_WEATHER_ROUTE"
    if independent_exact < 20:
        return "DESCRIPTIVE_ONLY_CANDIDATE"
    return "SIMULATION_REQUIRED"


def _lake_screen(uncertain: int, unresolved: int,
                 in_country_canonical: int, verified_controls: int) -> str:
    if uncertain:
        return "TERRITORY_REVIEW_REQUIRED"
    if unresolved:
        return "IDENTITY_RECONCILE_REQUIRED"
    if in_country_canonical < 150:
        return "INSUFFICIENT_LAKE_FRAME"
    if not verified_controls:
        return "CONTROL_FRAME_NOT_ESTABLISHED"
    return "OBSERVATION_FRAME_REQUIRES_REVIEW"


def _evidence_screen(unverified_eligible: int,
                     unverified_controls: int) -> str:
    if unverified_eligible or unverified_controls:
        return "UNVERIFIED_EVIDENCE_PRESENT"
    return "NO_UNVERIFIED_CLAIMS"


def build_report(crosswalk_path: str | Path, lake_frame_path: str | Path,
                 register_path: str | Path,
                 adjudication_path: str | Path | None = None,
                 checklist_path: str | Path = DEFAULT_CHECKLIST,
                 artifact_revision: int = 0) -> dict[str, Any]:
    crosswalk_path = Path(crosswalk_path)
    lake_frame_path = Path(lake_frame_path)
    register_path = Path(register_path)
    _, checklist_digest = science_gate_checklist.load_verified_checklist(
        Path(checklist_path))
    register = _load_verified_json(
        register_path, evidence_register.validate_register, "register")
    verified = evidence_register.verified_records(register)
    crosswalk = _load_verified_json(
        crosswalk_path, event_crosswalk.validate_crosswalk, "crosswalk")
    frame = _load_verified_json(
        lake_frame_path, lake_frame.validate_frame, "lake frame")
    if adjudication_path is not None:
        adjudication_path = Path(adjudication_path)
        adjudication = _load_verified_json(
            adjudication_path,
            lambda doc: event_adjudication.validate_adjudication(
                doc, crosswalk, sha256_file(crosswalk_path)),
            "adjudication")
        records = event_adjudication.apply_adjudication(
            crosswalk, adjudication, sha256_file(crosswalk_path))
    else:
        records = crosswalk.get("records", [])
    lake_records = frame.get("records", [])

    unreviewed = sum(not _review_complete(r) for r in records)
    candidate_rows = sum(
        isinstance(r, dict) and r.get("candidate_class") != "OUTSIDE"
        for r in records)
    reference_rows = len(records) - candidate_rows
    target_country_rows = sum(
        isinstance(r, dict) and r.get("candidate_class") == "TARGET_COUNTRY"
        for r in records)
    transboundary_rows = sum(
        isinstance(r, dict) and r.get("candidate_class") == "TRANSBOUNDARY"
        for r in records)
    other_candidate_rows = candidate_rows - target_country_rows - transboundary_rows
    eligible = [r for r in records
                if isinstance(r, dict)
                and isinstance(r.get("adjudication"), dict)
                and r["adjudication"].get("eligibility") == "ELIGIBLE"
                and r["adjudication"].get("review_state") == "COMPLETED"]
    unverified_eligible = sum(
        not _evidence_verified_event(r, verified) for r in eligible)
    independent_records = _independent_exact(records, verified)
    episode_ids = sorted({
        r["episode"]["candidate_episode_id"] for r in independent_records})
    lake_denominators = _lake_denominators(lake_records, verified)

    event_screen = _event_screen(unreviewed, len(episode_ids))
    lake_screen = _lake_screen(
        lake_denominators["uncertain_territory_lakes"],
        lake_denominators["unresolved_lake_identities"],
        lake_denominators["in_country_canonical_lakes"],
        lake_denominators["verified_non_event_controls"])
    evidence_screen = _evidence_screen(
        unverified_eligible,
        lake_denominators["unverified_control_candidates"])
    report = {
        "schema": SCHEMA,
        "version": 0,
        "claim_scope": "research_only_no_operational_authorization",
        "authority": dict(AUTHORITY_FLAGS),
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
        "inputs": {
            "crosswalk_sha256": sha256_file(crosswalk_path),
            "lake_frame_sha256": sha256_file(lake_frame_path),
            "evidence_register_sha256": sha256_file(register_path),
            "phase0_checklist_sha256": checklist_digest,
            "adjudication_sha256": (
                sha256_file(adjudication_path)
                if adjudication_path is not None else None),
            "sidecars_verified": True,
        },
        "artifact_revision": artifact_revision,
        "denominators": {
            "catalog_rows": len(records),
            "india_candidate_rows": candidate_rows,
            "target_country_candidate_rows": target_country_rows,
            "transboundary_candidate_rows": transboundary_rows,
            "other_candidate_rows": other_candidate_rows,
            "reference_only_rows": reference_rows,
            "adjudicated_eligible_rows": len(eligible),
            "eligible_unverified_evidence": unverified_eligible,
            "independent_exact_day_episodes": len(episode_ids),
            "mapped_lake_rows": lake_denominators["mapped_lake_rows"],
            "in_country_canonical_lakes":
                lake_denominators["in_country_canonical_lakes"],
            "outside_lakes": lake_denominators["outside_lakes"],
            "uncertain_territory_lakes":
                lake_denominators["uncertain_territory_lakes"],
            "unresolved_lake_identities":
                lake_denominators["unresolved_lake_identities"],
            "control_candidates": lake_denominators["control_candidates"],
            "verified_non_event_controls":
                lake_denominators["verified_non_event_controls"],
            "verified_non_event_intervals":
                lake_denominators["verified_non_event_intervals"],
            "verified_non_event_lake_years":
                lake_denominators["verified_non_event_lake_years"],
            "unverified_control_candidates":
                lake_denominators["unverified_control_candidates"],
            "observation_unknown_lake_rows":
                lake_denominators["observation_unknown_lake_rows"],
            "observation_partial_lake_rows":
                lake_denominators["observation_partial_lake_rows"],
            "observation_known_breach_lake_rows":
                lake_denominators["observation_known_breach_lake_rows"],
            "observation_verified_non_event_lake_rows":
                lake_denominators["observation_verified_non_event_lake_rows"],
            "observation_full_lake_rows":
                lake_denominators["observation_full_lake_rows"],
            "observable_lake_years": lake_denominators["observable_lake_years"],
            "unreviewed_event_rows": unreviewed,
            "verified_evidence_source_records": len(verified),
        },
        "independent_episode_ids": episode_ids,
        "gates": {
            "event_weather_screen": event_screen,
            "lake_year_screen": lake_screen,
            "evidence_screen": evidence_screen,
            "precision_simulation": "NOT_RUN",
            "bulk_acquisition_authorized": False,
            "weather_download_authorized": False,
            "satellite_bulk_authorized": False,
            "seismic_waveform_authorized": False,
        },
        "next_gate": ("COMPLETE_EVENT_ADJUDICATION" if unreviewed else
                      "VERIFY_EVENT_EVIDENCE" if unverified_eligible else
                      "RUN_PREDECLARED_PRECISION_SIMULATION"
                      if len(episode_ids) >= 20 else
                      "REVIEW_TERRITORY_CLASSIFICATION"
                      if lake_denominators["uncertain_territory_lakes"] else
                      "RECONCILE_LAKE_IDENTITIES"
                      if lake_denominators["unresolved_lake_identities"] else
                      "ESTABLISH_OBSERVATION_FRAME"
                      if lake_denominators["in_country_canonical_lakes"] >= 150
                      else "RECONCILE_LAKE_INVENTORY"),
        "phase0_decision_readiness": science_gate_checklist.phase0_readiness({
            "catalog_rows": len(records),
            "mapped_lake_rows": lake_denominators["mapped_lake_rows"],
            "verified_source_bytes": len(verified),
            "unreviewed_event_rows": unreviewed,
            "eligible_unverified_evidence": unverified_eligible,
            "uncertain_territory_lakes":
                lake_denominators["uncertain_territory_lakes"],
            "unresolved_lake_identities":
                lake_denominators["unresolved_lake_identities"],
            "in_country_canonical_lakes":
                lake_denominators["in_country_canonical_lakes"],
            "unverified_control_candidates":
                lake_denominators["unverified_control_candidates"],
            "observation_unknown_lake_rows":
                lake_denominators["observation_unknown_lake_rows"],
            "observation_partial_lake_rows":
                lake_denominators["observation_partial_lake_rows"],
            "observation_known_breach_lake_rows":
                lake_denominators["observation_known_breach_lake_rows"],
            "observation_verified_non_event_lake_rows":
                lake_denominators["observation_verified_non_event_lake_rows"],
            "observable_lake_years": lake_denominators["observable_lake_years"],
        }),
        "decision": "PHASE0_ONLY_NO_ACQUISITION",
        "notes": [
            "A catalog row is not an independent episode.",
            "A mapped or monitored lake is not a verified non-event control.",
            "Fixed count bands are workflow screens, not power guarantees.",
            "The 150-lake screen is a heuristic bound on frame size, never 150 positive events.",
            "A citation string is attribution until it resolves to a BYTES_VERIFIED register record with covering temporal coverage.",
            "Reviewer ids are attribution, not authenticated signoff.",
            "Rows sharing a candidate episode id count once in episode denominators.",
            "OUTSIDE and UNCERTAIN territory records stay visible but never enter India-only denominators.",
            "A BYTES_VERIFIED register record binds bytes to a digest and locator; it does not prove the source content is authentic — that rests on the recorded official source and access terms.",
            "No weather, satellite, or seismic payload may be retrieved from this report.",
        ],
    }
    if adjudication_path is not None:
        report["inputs"]["adjudication_sha256"] = sha256_file(adjudication_path)
    return report


def validate_report(doc: dict[str, Any],
                    checklist_path: str | Path = DEFAULT_CHECKLIST) -> list[str]:
    problems: list[str] = []
    try:
        _, checklist_digest = science_gate_checklist.load_verified_checklist(
            Path(checklist_path))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        return [f"science-gate checklist preflight blocked: {exc}"]
    if not isinstance(doc, dict):
        return ["report root must be an object"]
    if doc.get("schema") != SCHEMA:
        problems.append("unexpected schema")
    if doc.get("claim_scope") != "research_only_no_operational_authorization":
        problems.append("unexpected claim scope")
    if doc.get("authority") != AUTHORITY_FLAGS:
        problems.append("authority flags must all be present and false")
    d = doc.get("denominators", {})
    if not isinstance(d, dict):
        return problems + ["denominators must be an object"]
    for key in ("catalog_rows", "india_candidate_rows",
                "reference_only_rows", "adjudicated_eligible_rows",
                "eligible_unverified_evidence",
                "independent_exact_day_episodes", "mapped_lake_rows",
                "in_country_canonical_lakes", "outside_lakes",
                "uncertain_territory_lakes", "unresolved_lake_identities",
                "control_candidates", "verified_non_event_controls",
                "verified_non_event_intervals", "verified_non_event_lake_years",
                "unverified_control_candidates", "observation_unknown_lake_rows",
                "observation_partial_lake_rows",
                "observation_known_breach_lake_rows",
                "observation_verified_non_event_lake_rows",
                "observation_full_lake_rows", "observable_lake_years",
                "unreviewed_event_rows",
                "verified_evidence_source_records"):
        if (not isinstance(d.get(key), int)
                or isinstance(d.get(key), bool) or d[key] < 0):
            problems.append(f"invalid denominator: {key}")
    if d.get("adjudicated_eligible_rows", 0) > d.get("catalog_rows", 0):
        problems.append("eligible rows exceed catalog rows")
    if d.get("india_candidate_rows", 0) + d.get("reference_only_rows", 0) \
            != d.get("catalog_rows", 0):
        problems.append("candidate + reference rows must equal catalog rows")
    if d.get("adjudicated_eligible_rows", 0) > d.get(
            "india_candidate_rows", 0):
        problems.append("eligible rows exceed India candidate rows")
    if d.get("independent_exact_day_episodes", 0) > d.get(
            "adjudicated_eligible_rows", 0):
        problems.append("independent episodes exceed eligible rows")
    if d.get("eligible_unverified_evidence", 0) > d.get(
            "adjudicated_eligible_rows", 0):
        problems.append("unverified-eligible rows exceed eligible rows")
    if d.get("verified_non_event_controls", 0) > d.get(
            "control_candidates", 0):
        problems.append("verified controls exceed control candidates")
    if d.get("verified_non_event_lake_years", 0) > d.get(
            "verified_non_event_intervals", 0) * 10000:
        problems.append("verified non-event lake-years exceed interval bounds")
    if d.get("outside_lakes", 0) + d.get("uncertain_territory_lakes", 0) > \
            d.get("mapped_lake_rows", 0):
        problems.append("territory-excluded lakes exceed mapped lake rows")
    if sum(d.get(k, 0) for k in (
            "observation_unknown_lake_rows", "observation_partial_lake_rows",
            "observation_known_breach_lake_rows",
            "observation_verified_non_event_lake_rows")) != d.get("mapped_lake_rows", 0):
        problems.append("observation-state rows do not partition mapped lake rows")
    if d.get("observation_full_lake_rows", 0) > d.get("mapped_lake_rows", 0):
        problems.append("full-observation rows exceed mapped lake rows")
    if d.get("observable_lake_years", 0) and not d.get(
            "observation_full_lake_rows", 0):
        problems.append("observable lake-years require full-observation rows")
    if d.get("in_country_canonical_lakes", 0) > (
            d.get("mapped_lake_rows", 0) - d.get("outside_lakes", 0)
            - d.get("uncertain_territory_lakes", 0)):
        problems.append("canonical lakes exceed in-country mapped rows")
    ids = doc.get("independent_episode_ids")
    if (not isinstance(ids, list)
            or any(not isinstance(v, str) or not v for v in ids)):
        problems.append("independent_episode_ids must be unique strings")
    else:
        if len(ids) != len(set(ids)):
            problems.append("independent_episode_ids must be unique strings")
        if len(ids) != d.get("independent_exact_day_episodes"):
            problems.append("episode id list does not match episode denominator")
    expected_event = _event_screen(
        d.get("unreviewed_event_rows", 0),
        d.get("independent_exact_day_episodes", 0),
    )
    expected_lake = _lake_screen(
        d.get("uncertain_territory_lakes", 0),
        d.get("unresolved_lake_identities", 0),
        d.get("in_country_canonical_lakes", 0),
        d.get("verified_non_event_controls", 0),
    )
    expected_evidence = _evidence_screen(
        d.get("eligible_unverified_evidence", 0),
        d.get("unverified_control_candidates", 0),
    )
    gates = doc.get("gates", {})
    if not isinstance(gates, dict):
        problems.append("gates must be an object")
        gates = {}
    if gates.get("event_weather_screen") != expected_event:
        problems.append("event screen does not match denominators")
    if gates.get("lake_year_screen") != expected_lake:
        problems.append("lake screen does not match denominators")
    if gates.get("evidence_screen") != expected_evidence:
        problems.append("evidence screen does not match denominators")
    for key in ("bulk_acquisition_authorized", "weather_download_authorized",
                "satellite_bulk_authorized", "seismic_waveform_authorized"):
        if gates.get(key) is not False:
            problems.append(f"authority flag must remain false: {key}")
    if doc.get("decision") != "PHASE0_ONLY_NO_ACQUISITION":
        problems.append("unexpected decision state")
    expected_next = ("COMPLETE_EVENT_ADJUDICATION"
                     if d.get("unreviewed_event_rows", 0) else
                     "VERIFY_EVENT_EVIDENCE"
                     if d.get("eligible_unverified_evidence", 0) else
                     "RUN_PREDECLARED_PRECISION_SIMULATION"
                     if d.get("independent_exact_day_episodes", 0) >= 20 else
                     "REVIEW_TERRITORY_CLASSIFICATION"
                     if d.get("uncertain_territory_lakes", 0) else
                     "RECONCILE_LAKE_IDENTITIES"
                     if d.get("unresolved_lake_identities", 0) else
                     "ESTABLISH_OBSERVATION_FRAME"
                     if d.get("in_country_canonical_lakes", 0) >= 150 else
                     "RECONCILE_LAKE_INVENTORY")
    if doc.get("next_gate") != expected_next:
        problems.append("next_gate does not match denominators")
    inputs = doc.get("inputs", {})
    if not isinstance(inputs, dict):
        return problems + ["inputs must be an object"]
    if inputs.get("sidecars_verified") is not True:
        problems.append("inputs.sidecars_verified must be true")
    if not isinstance(inputs.get("evidence_register_sha256"), str):
        problems.append("inputs must bind evidence_register_sha256")
    if inputs.get("phase0_checklist_sha256") != checklist_digest:
        problems.append("inputs.phase0_checklist_sha256 differs from verified checklist")
    expected_readiness = science_gate_checklist.phase0_readiness(d)
    if doc.get("phase0_decision_readiness") != expected_readiness:
        problems.append("phase0_decision_readiness differs from prerequisites")
    return problems


def verify_report(report_path: str | Path, crosswalk_path: str | Path,
                  lake_frame_path: str | Path, register_path: str | Path,
                  adjudication_path: str | Path | None = None,
                  checklist_path: str | Path = DEFAULT_CHECKLIST) -> list[str]:
    """Independently recompute a stored report from validated inputs.

    A forged but internally consistent report fails here: denominators,
    gates, episode ids, and bound input digests are recomputed from the
    sidecar-verified inputs and compared against the stored artifact.
    """
    problems: list[str] = []
    stored = json.loads(Path(report_path).read_text(encoding="utf-8"))
    invalid = validate_report(stored, checklist_path)
    if invalid:
        return ["stored report fails its own validator: "
                + "; ".join(invalid)]
    computed = build_report(crosswalk_path, lake_frame_path, register_path,
                            adjudication_path, checklist_path)
    for key, expected in computed["denominators"].items():
        if stored["denominators"].get(key) != expected:
            problems.append(
                f"denominator {key}: stored "
                f"{stored['denominators'].get(key)!r} != recomputed "
                f"{expected!r}")
    if stored.get("independent_episode_ids") != computed.get(
            "independent_episode_ids"):
        problems.append("independent_episode_ids differ from recomputation")
    if stored.get("gates") != computed.get("gates"):
        problems.append("gates differ from recomputation")
    if stored.get("next_gate") != computed.get("next_gate"):
        problems.append("next_gate differs from recomputation")
    for key, expected in computed["inputs"].items():
        if stored["inputs"].get(key) != expected:
            problems.append(
                f"inputs.{key}: stored report does not bind the "
                "provided inputs")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--crosswalk", required=True)
    parser.add_argument("--lake-frame", required=True)
    parser.add_argument("--evidence-register", required=True)
    parser.add_argument("--adjudication")
    parser.add_argument("--checklist", default=str(DEFAULT_CHECKLIST))
    parser.add_argument("--out")
    parser.add_argument("--verify",
                        help="recompute-check a stored report instead of "
                             "building a new one")
    args = parser.parse_args()
    if args.verify:
        problems = verify_report(args.verify, args.crosswalk,
                                 args.lake_frame, args.evidence_register,
                                 args.adjudication, args.checklist)
        if problems:
            for problem in problems:
                print(f"FEASIBILITY_VERIFY_FAIL: {problem}")
            return 1
        print(f"FEASIBILITY_VERIFY_OK: {args.verify} recomputes from "
              "validated inputs")
        return 0
    if not args.out:
        parser.error("--out is required unless --verify is given")
    doc = build_report(args.crosswalk, args.lake_frame,
                       args.evidence_register, args.adjudication,
                       args.checklist,
                       artifact_revision=_artifact_revision(args.out))
    problems = validate_report(doc, args.checklist)
    if problems:
        raise SystemExit("feasibility report validation failed: " + "; ".join(problems))
    write_once_json(args.out, doc, indent=2)
    write_once_sidecar(args.out)
    print(json.dumps({"status": doc["decision"], "out": str(args.out),
                      "gates": doc["gates"], "next_gate": doc["next_gate"]},
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
