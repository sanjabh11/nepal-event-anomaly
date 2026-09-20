"""One-station seismic observability contract (V1) — DESIGN SURFACE ONLY.

Authorized by the owner-approved preflight amendment
``retrieval/p5_amendment_v4_seismic_observability_preflight.json``
(P5_AMENDMENT_V4, 2026-09-20): ONE-station benchmark path *design* is
authorized — nothing here retrieves waveforms, opens a network
connection, or imports ObsPy.  The existing multi-station runner
(``runner.run_seismic_descriptive_poc``) enforces
``min_trained_stations >= 3`` and is deliberately NOT reused; this
module binds the vocabulary and the receipt shape a future dedicated
one-station observability runner must emit.

The lane is a research-only, retrospective observability /
anomaly-triage surface — never a detector, locator, warning, forecast,
or operational claim, and never a seismic-to-GLOF association.  The
owner-approved ceilings are declared here as constants: the authorized
waveform window 2023-04-01..2023-05-09 (inclusive UTC dates) and the
authorized station list {"374", "312", "302", "1158"}.

BLOCKED semantics: ``BLOCKED`` is a PREFLIGHT terminal state — the
lane stopped before waveform bytes existed and no event is bound into
evidence.  It is the one-station analog of v0's ``UNOBSERVABLE``
surfaced *before* retrieval, and it is NOT a scientific result: it
carries a ``blocked_reason`` and must never carry a
``waveform_digest`` or an ``event_anchor``.  The scientific terminal
statuses (``OBSERVABILITY_PASS``, ``CANDIDATE_ANOMALIES_ONLY``,
``NO_QUALIFIED_SIGNAL``) are the only statuses that may bind byte
evidence and an event anchor.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime as _dt, timezone
from typing import Any, Mapping, Optional

from nepal.research_v0.policy import parse_strict_utc

ONE_STATION_RECEIPT_TYPE = "ONE_STATION_OBSERVABILITY_RECEIPT_V1"

#: The one-station lane's fixed claim scope — retrospective
#: observability / anomaly triage with no operational authorization
#: of any kind.
ONE_STATION_CLAIM_SCOPE = "research_only_no_operational_authorization"

#: V1 status vocabulary for the one-station lane (amendment
#: ``observability_outcome_vocabulary`` plus the explicit preflight
#: terminal BLOCKED):
#:
#: - OBSERVABILITY_PASS — byte-qualified data; the predeclared
#:   event/control contrast is reproducible; retrospective,
#:   non-operational.
#: - CANDIDATE_ANOMALIES_ONLY — candidate windows exist but
#:   reproducibility or independent verification is insufficient.
#: - NO_QUALIFIED_SIGNAL — bytes and gates pass; the predeclared
#:   contrast is not reproduced (an honest null result).
#: - NOT_OPERATIONAL — the fixed claim ceiling; a ceiling marker,
#:   never a success status.
#: - RUN_ERROR — contract/manifest/config failure.
#: - BLOCKED — preflight terminal state (no bytes/event bound);
#:   NOT a scientific result.
ONE_STATION_STATUSES = frozenset({
    "OBSERVABILITY_PASS", "CANDIDATE_ANOMALIES_ONLY",
    "NO_QUALIFIED_SIGNAL", "NOT_OPERATIONAL",
    "RUN_ERROR", "BLOCKED"})

#: The statuses that terminate a scientific evaluation — each REQUIRES
#: a bound in-window event anchor and byte digests (waveform +
#: StationXML).  BLOCKED/RUN_ERROR/NOT_OPERATIONAL are non-scientific
#: terminals.
ONE_STATION_SCIENTIFIC_STATUSES = frozenset({
    "OBSERVABILITY_PASS", "CANDIDATE_ANOMALIES_ONLY",
    "NO_QUALIFIED_SIGNAL"})

#: Documented mapping from the existing v0 sidecar receipt statuses
#: (``contracts.RECEIPT_STATUSES``) to the V1 one-station vocabulary.
#: Every v0 status has exactly one declared analog; the V1-only
#: statuses (NO_QUALIFIED_SIGNAL, NOT_OPERATIONAL, BLOCKED) are
#: commented below the mapping.
ONE_STATION_STATUS_MAP = {
    # v0 UNOBSERVABLE (missing/invalid/inadequate waveform evidence)
    # surfaces in the one-station lane BEFORE retrieval — the
    # preflight terminal analog is BLOCKED, not a scientific result.
    "UNOBSERVABLE": "BLOCKED",
    # v0 CANDIDATE_ONLY (regime candidates without stability) maps to
    # candidate anomaly windows without independent reproducibility.
    "CANDIDATE_ONLY": "CANDIDATE_ANOMALIES_ONLY",
    # v0 UNDERPOWERED_DESCRIPTIVE_ONLY (observability passes but
    # diversity/support cannot carry the declared holdout) maps to
    # candidate-level output: gates passed, evidence insufficient for
    # a stable verdict — a single station is structurally underpowered
    # for the multi-station contract.
    "UNDERPOWERED_DESCRIPTIVE_ONLY": "CANDIDATE_ANOMALIES_ONLY",
    # v0 DESCRIPTIVE_REGIME_ONLY (the descriptive-stable ceiling) maps
    # to byte-qualified observability with the predeclared contrast
    # reproduced — still retrospective and non-operational.
    "DESCRIPTIVE_REGIME_ONLY": "OBSERVABILITY_PASS",
    # engine/config/manifest failure maps through unchanged.
    "RUN_ERROR": "RUN_ERROR",
    # V1-only statuses with no direct v0 analog:
    #   NO_QUALIFIED_SIGNAL — an explicit honest-null terminal; v0
    #     folds "contrast not reproduced" into CANDIDATE_ONLY via the
    #     regime engine's UNSUPERVISED_STRUCTURE_NOT_STABLE verdict.
    #   NOT_OPERATIONAL — the fixed claim-ceiling marker; v0 has no
    #     ceiling-marker status.
    #   BLOCKED — preflight terminal; v0's UNOBSERVABLE is its
    #     post-retrieval analog.
}

#: Owner-approved ceilings (P5_AMENDMENT_V4).  A receipt may narrow
#: these (a subset of stations, a sub-window) but may NEVER widen
#: them — declarations outside the ceiling reject.
AUTHORIZED_STATION_IDS = ("374", "312", "302", "1158")
AUTHORIZED_WAVEFORM_WINDOW = ("2023-04-01", "2023-05-09")  # ISO dates, inclusive

_AUTHORITY_FLAGS = (
    "promotion_eligible", "production_authorized",
    "warning_path_authorized", "detector_authorized",
    "locator_authorized")

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_ISO_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")

#: Forecast/warning/operational claim vocabulary that must never
#: appear in a receipt's free-text fields (reason, blocked_reason,
#: notes).  ``detector``/``locator`` are included because the lane's
#: own authority flags name them — writing the claim into prose is
#: the same violation as flipping the flag.
_FORBIDDEN_CLAIM_TERMS = re.compile(
    r"\b(forecast\w*|forewarn\w*|warn(?:ing|ings|ed|s)?|alert\w*|"
    r"alarm\w*|operation\w*|predict\w*|prognos\w*|bulletin\w*|"
    r"evacuat\w*|real[-_ ]?time|detector\w*|locator\w*|siren\w*|"
    r"early[-_ ]?warn\w*|public[-_ ]?safety)\b",
    re.IGNORECASE)

_FREE_TEXT_FIELDS = ("reason", "blocked_reason")


def _req(problems: list[str], name: str, value: Any) -> None:
    if value is None or value == "" or value == [] or value == () \
            or value == {}:
        problems.append(f"{name} is required")


def _iso_date(value: Any) -> Optional[_dt]:
    """Parse an ISO YYYY-MM-DD calendar date; malformed or impossible
    dates return None."""
    if not isinstance(value, str) or not _ISO_DATE_RE.match(value):
        return None
    try:
        return _dt.strptime(value, "%Y-%m-%d")
    except ValueError:
        return None


def _utc_date(value: Any) -> Optional[Any]:
    """Epoch/UTC timestamp -> calendar date (None when unparseable)."""
    parsed = parse_strict_utc(value)
    if parsed is None:
        return None
    return _dt.fromtimestamp(parsed, timezone.utc).date()


def _scan_free_text(problems: list[str], name: str,
                    value: Any) -> None:
    if value in (None, ""):
        return
    if not isinstance(value, str):
        problems.append(f"{name} must be a string")
        return
    hit = _FORBIDDEN_CLAIM_TERMS.search(value)
    if hit:
        problems.append(
            f"{name} carries forbidden claim vocabulary "
            f"{hit.group(0)!r} — forecast/warning/operational "
            "terms may never appear in free text")


def _window_dates(receipt: "OneStationReceiptV1",
                  problems: list[str]) -> tuple:
    """Validate the declared analysis window; return
    (start_date, end_date) or (None, None)."""
    start_raw, end_raw = receipt.window_start, receipt.window_end
    if not start_raw and not end_raw:
        return None, None
    ws = parse_strict_utc(start_raw) if start_raw else None
    we = parse_strict_utc(end_raw) if end_raw else None
    if ws is None or we is None:
        problems.append("window_start/window_end must both be "
                        "explicit-UTC timestamps when a window is "
                        "declared")
        return None, None
    if we <= ws:
        problems.append("window_end precedes window_start — "
                        "inverted window")
    return _utc_date(start_raw), _utc_date(end_raw)


@dataclass(frozen=True)
class OneStationReceiptV1:
    """One-station observability lane receipt (V1 design surface).

    ``record_type`` is a declared field so a forged tag is a
    validation defect, not a serialization detail.  All five
    authority flags are fail-closed: exactly ``False`` by
    construction, rejected when True, non-bool, or absent from a
    serialized payload (deserialization requires the exact field
    surface — the flags can never be absent).
    """

    record_type: str = ONE_STATION_RECEIPT_TYPE
    status: str = "RUN_ERROR"
    station_id: str = ""
    claim_scope: str = ONE_STATION_CLAIM_SCOPE
    window_start: str = ""
    window_end: str = ""
    # Documented in-window event descriptor: a Mapping carrying at
    # least ``date`` (ISO YYYY-MM-DD, inside the declared window) and
    # ``source`` (the documenting catalog/literature).  Required for
    # the scientific terminal statuses; forbidden on BLOCKED — a
    # preflight terminal binds no event.
    event_anchor: Mapping = field(default_factory=dict)
    waveform_digest: str = ""
    stationxml_digest: str = ""
    reason: str = ""
    blocked_reason: str = ""
    notes: tuple = ()
    # Declared authorization this receipt runs under — defaults to the
    # owner-approved ceilings; may narrow, never widen.
    authorized_stations: tuple = AUTHORIZED_STATION_IDS
    authorized_window: tuple = AUTHORIZED_WAVEFORM_WINDOW
    # Authority flags — fail-closed, exactly False, never absent.
    promotion_eligible: bool = False
    production_authorized: bool = False
    warning_path_authorized: bool = False
    detector_authorized: bool = False
    locator_authorized: bool = False

    def problems(self) -> list[str]:
        """Structural validation — a non-empty list means the
        receipt is inadmissible."""
        problems: list[str] = []
        if self.record_type != ONE_STATION_RECEIPT_TYPE:
            problems.append(
                f"record_type {self.record_type!r} must be exactly "
                f"{ONE_STATION_RECEIPT_TYPE!r}")
        if self.status not in ONE_STATION_STATUSES:
            problems.append(
                f"status {self.status!r} not in "
                f"{sorted(ONE_STATION_STATUSES)}")
        if self.claim_scope != ONE_STATION_CLAIM_SCOPE:
            problems.append(
                f"claim_scope {self.claim_scope!r} must be exactly "
                f"{ONE_STATION_CLAIM_SCOPE!r}")
        # ---- authority flags: present and exactly False ------------
        for name in _AUTHORITY_FLAGS:
            if getattr(self, name) is not False:
                problems.append(
                    f"{name} must be exactly False — the "
                    "one-station lane carries no promotion, "
                    "production, warning, detector, or locator "
                    "authority")
        # ---- declared authorization ceilings -----------------------
        stations_ok = isinstance(
            self.authorized_stations, (list, tuple)) and \
            all(isinstance(s, str) and s.strip()
                for s in self.authorized_stations)
        if not stations_ok:
            problems.append("authorized_stations must be a tuple of "
                            "non-empty strings")
        else:
            outside = sorted(set(self.authorized_stations) -
                             set(AUTHORIZED_STATION_IDS))
            if outside:
                problems.append(
                    f"authorized_stations {outside} exceed the "
                    "owner-approved station ceiling — a receipt may "
                    "narrow, never widen")
        if not isinstance(self.station_id, str) or \
                not self.station_id.strip():
            problems.append("station_id must be a non-empty string")
        elif stations_ok and self.authorized_stations and \
                self.station_id not in self.authorized_stations:
            problems.append(
                f"station_id {self.station_id!r} is outside the "
                "declared authorized station list")
        # authorized_window: a declared (lo, hi) ISO-date pair inside
        # the owner-approved waveform window.
        aw = self.authorized_window
        aw_lo = aw_hi = None
        if not isinstance(aw, (list, tuple)) or len(aw) != 2:
            problems.append("authorized_window must be a "
                            "(start_date, end_date) ISO-date pair")
        else:
            aw_lo = _iso_date(aw[0])
            aw_hi = _iso_date(aw[1])
            if aw_lo is None or aw_hi is None:
                problems.append("authorized_window bounds must be "
                                "real ISO calendar dates")
            else:
                if aw_hi < aw_lo:
                    problems.append("authorized_window end precedes "
                                    "its start")
                ceil_lo = _iso_date(AUTHORIZED_WAVEFORM_WINDOW[0])
                ceil_hi = _iso_date(AUTHORIZED_WAVEFORM_WINDOW[1])
                if aw_lo.date() < ceil_lo.date() or \
                        aw_hi.date() > ceil_hi.date():
                    problems.append(
                        "authorized_window exceeds the "
                        "owner-approved waveform window "
                        f"{AUTHORIZED_WAVEFORM_WINDOW} — a receipt "
                        "may narrow, never widen")
        # ---- declared analysis window ------------------------------
        win_lo, win_hi = _window_dates(self, problems)
        window_required = self.status in \
            ONE_STATION_SCIENTIFIC_STATUSES or \
            self.status == "BLOCKED"
        if window_required and not self.window_start:
            problems.append(
                f"status {self.status} requires a declared "
                "window_start/window_end")
        if win_lo is not None and aw_lo is not None and \
                aw_hi is not None:
            if win_lo < aw_lo.date() or win_hi > aw_hi.date():
                problems.append(
                    "declared window falls outside the authorized "
                    "window — observability outside the approved "
                    "interval is inadmissible")
        # ---- digests ------------------------------------------------
        for name in ("waveform_digest", "stationxml_digest"):
            v = getattr(self, name)
            if v and (not isinstance(v, str) or
                      not _SHA256_RE.match(v)):
                problems.append(f"{name} must be a 64-hex sha256 "
                                "digest when present")
        # ---- event anchor -------------------------------------------
        anchor_present = bool(self.event_anchor)
        if anchor_present:
            if not isinstance(self.event_anchor, Mapping):
                problems.append("event_anchor must be a mapping "
                                "carrying at least date and source")
            else:
                if not _iso_date(self.event_anchor.get("date")):
                    problems.append("event_anchor.date must be a "
                                    "real ISO calendar date")
                src = self.event_anchor.get("source")
                if not isinstance(src, str) or not src.strip():
                    problems.append("event_anchor.source must name "
                                    "the documenting catalog or "
                                    "literature source")
                ev_date = _iso_date(
                    self.event_anchor.get("date"))
                if ev_date is not None:
                    if win_lo is not None and win_hi is not None and \
                            not (win_lo <= ev_date.date() <= win_hi):
                        problems.append(
                            "event_anchor.date is outside the "
                            "declared window — the anchor must be "
                            "documented in-window")
                    if aw_lo is not None and aw_hi is not None and \
                            not (aw_lo.date() <= ev_date.date()
                                 <= aw_hi.date()):
                        problems.append(
                            "event_anchor.date is outside the "
                            "authorized window")
        # ---- status-specific terminals ------------------------------
        if self.status in ONE_STATION_SCIENTIFIC_STATUSES:
            _req(problems,
                 f"{self.status} requires event_anchor",
                 self.event_anchor)
            if not self.waveform_digest:
                problems.append(
                    f"{self.status} requires waveform_digest — a "
                    "scientific verdict binds the qualified bytes")
            if not self.stationxml_digest:
                problems.append(
                    f"{self.status} requires stationxml_digest — "
                    "StationXML metadata evidence is bound with the "
                    "verdict")
        if self.status == "BLOCKED":
            if not isinstance(self.blocked_reason, str) or \
                    not self.blocked_reason.strip():
                problems.append("BLOCKED requires a non-empty "
                                "blocked_reason")
            if self.waveform_digest:
                problems.append(
                    "BLOCKED must not carry waveform_digest — "
                    "preflight terminal: no bytes exist")
            if anchor_present:
                problems.append(
                    "BLOCKED must not carry event_anchor — "
                    "preflight terminal: no event is bound")
        elif self.blocked_reason:
            problems.append("blocked_reason is admissible only on a "
                            "BLOCKED receipt")
        if self.status in ("RUN_ERROR", "NOT_OPERATIONAL") and \
                not (isinstance(self.reason, str) and
                     self.reason.strip()):
            problems.append(f"{self.status} requires a non-empty "
                            "reason")
        # ---- free-text claim scan ------------------------------------
        for name in _FREE_TEXT_FIELDS:
            _scan_free_text(problems, name, getattr(self, name))
        if not isinstance(self.notes, (list, tuple)) or \
                any(not isinstance(n, str) for n in self.notes):
            problems.append("notes must be a tuple of strings")
        else:
            for i, note in enumerate(self.notes):
                _scan_free_text(problems, f"notes[{i}]", note)
        return problems

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        if isinstance(self.event_anchor, Mapping):
            d["event_anchor"] = dict(self.event_anchor)
        d["notes"] = list(self.notes)
        d["authorized_stations"] = list(self.authorized_stations)
        d["authorized_window"] = list(self.authorized_window)
        return d


_RECEIPT_FIELDS = frozenset(
    {f for f in OneStationReceiptV1.__dataclass_fields__}
    - {"record_type"})


def one_station_receipt_from_dict(
        payload: Mapping[str, Any]) -> OneStationReceiptV1:
    """Exact typed deserialization — the ``record_type`` tag must
    name ``ONE_STATION_OBSERVABILITY_RECEIPT_V1``, every declared
    field must be present (the authority flags can never be absent),
    and unknown fields reject."""
    if not isinstance(payload, Mapping):
        raise ValueError("one-station receipt payload must be a "
                         "mapping")
    tag = payload.get("record_type")
    if tag != ONE_STATION_RECEIPT_TYPE:
        raise ValueError(
            f"record_type {tag!r} must name "
            f"{ONE_STATION_RECEIPT_TYPE}")
    keys = set(payload) - {"record_type"}
    missing = _RECEIPT_FIELDS - keys
    extra = keys - _RECEIPT_FIELDS
    if missing:
        raise ValueError(f"one-station receipt missing fields "
                         f"{sorted(missing)} — authority flags may "
                         "never be absent")
    if extra:
        raise ValueError(f"one-station receipt carries undeclared "
                         f"fields {sorted(extra)}")
    kwargs = {k: payload[k] for k in keys}
    for name in ("notes", "authorized_stations", "authorized_window"):
        kwargs[name] = tuple(kwargs[name])
    kwargs["event_anchor"] = dict(kwargs["event_anchor"])
    return OneStationReceiptV1(**kwargs)


def one_station_receipt_skeleton() -> dict[str, Any]:
    """The receipt's exact field surface with fail-closed defaults —
    status RUN_ERROR, every authority flag False by construction, no
    bytes and no event bound."""
    return OneStationReceiptV1().to_dict()


__all__ = [
    "ONE_STATION_RECEIPT_TYPE", "ONE_STATION_CLAIM_SCOPE",
    "ONE_STATION_STATUSES", "ONE_STATION_SCIENTIFIC_STATUSES",
    "ONE_STATION_STATUS_MAP", "AUTHORIZED_STATION_IDS",
    "AUTHORIZED_WAVEFORM_WINDOW", "OneStationReceiptV1",
    "one_station_receipt_from_dict", "one_station_receipt_skeleton"]
