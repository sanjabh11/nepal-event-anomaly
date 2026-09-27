"""Round-10 provenance-hardening tests — the R10 floor findings
(R10-P01..P12) must fail closed at the shared floor AND at every
boundary the payload type reaches.

Five boundaries are exercised for every payload mutation:

* ``floor`` — ``research_v0.producer_validation.validate_producer_payload``
  must RETURN problems: a propagated exception is a failure (P01's
  overflow-safety requirement), and so is silent acceptance.
* ``freeze`` — ``science_v0.regimes.freeze_regime_artifact`` on the
  pre-freeze artifact (``frozen``/``freeze_digest`` stripped); must
  raise ``ValueError`` — never a raw ``OverflowError``/``TypeError``
  escaping a decode.
* ``adapter`` — ``experiment_v0.adapters.regime_assignment_from_artifact``
  on the fully frozen payload; must raise ``ValueError``.
* ``audit`` — ``experiment_v0.audit.audit_producer_payload``; a
  non-empty Finding list is required.
* ``association`` — ``experiment_v0.association.run_association``
  with the mutated payload passed as ``producer_payload``.

R10 association evidence — the R9 matrix's weakness was falling back
to the CANONICAL artifact when the adapter rejected, which tested the
binding on a trivially-mismatched freeze_digest.  Here every case
forges the artifact deterministically:
``dataclasses.replace(canonical_artifact,
    regime_digest=payload["freeze_digest"],
    producer_payload_digest=payload["freeze_digest"],
    unit_basin_map=<the mutated partition>,
    unit_basin_map_digest=<recomputed over the mutated pairs>)``.
The binding's rejection then stands on the finding's own semantics —
either the shared floor's problem text (embedded verbatim, prefixed
``producer_payload ``) or the artifact↔payload mismatch the mutation
creates (assignments, seeds, mode, fitted_on, label_blinding).  Each
case's evidence regex must match the rejection text — a bare
``producer_payload`` token is not evidence.

Cases that cannot meaningfully reach the association lane are noted
per-case: FORECAST-mode payloads never adapt (the adapter rejects the
mode outright), so their association rejection rides on the mode
mismatch plus whatever floor problems the mutation adds; mutations an
artifact cannot carry (e.g. a forged ``unit_basin_map_digest`` over an
unchanged map) are covered because the binding runs the same floor.

Canonical payloads are reused from ``tests.test_r9_promotion`` — the
same honest-forger envelope applies (full rehash of every bound
digest), so a mutation that fails must fail on its own semantics.
Mutations carrying non-JSON values (``float('nan')``/``inf``) are
applied AFTER the honest rehash (``post=True``): canonical JSON
cannot encode them, so the strongest envelope a forger can produce is
the honest one plus the non-serializable field.

PENDING-FLOOR NOTE — the R10 shared-floor checks land concurrently
(Worker A).  A case whose check is not yet landed fails here with the
boundary's actual output — that failure IS the pending signal this
file exists to record.
"""
from __future__ import annotations

import copy
import dataclasses
import hashlib
import re
from pathlib import Path
from typing import Any, Callable, Mapping

import pytest

from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.producer_validation import (
    validate_producer_payload)
from nepal.science_v0.regimes import freeze_regime_artifact
from nepal.experiment_v0.adapters import regime_assignment_from_artifact
from nepal.experiment_v0.association import (
    BINDING_UNVERIFIED, STATUS_SUPPORTED, RegimeAssignmentArtifact,
    run_association)
from nepal.experiment_v0.audit import audit_producer_payload

# Association-geometry fixtures (same synthetic universe as the B1
# and R9 lanes — six units across three basins/eval regions).
from tests.test_experiment_v0_b1_association import (
    REGION_BASINS, UNIT_BASINS, make_controls, make_events,
    make_holdout, make_opportunities)

# The R9 canonical-payload machinery: complete NEW-floor payloads,
# the honest-forger envelope rehash, and the manifest helpers.
from tests.test_r9_promotion import (
    _FIT_GROUPS, _HELDOUT_GROUPS, _SEEDS, _bind_manifest,
    _canonical_payload, _nonfixture_manifest, _pre_freeze, _rehash,
    _sha_text, _ubm_digest)


# ---------------------------------------------------------------------
# Forecast-vintage records (ForecastVintageV0.to_dict() shape —
# nepal/research_v0/records.py:877).  ``evidence_root == ""`` marks a
# metadata-only candidate vintage; a non-empty root names a real
# directory whose two declared files byte-verify.
# ---------------------------------------------------------------------

def _vintage(vid: str, *, evidence_root: str = "") -> dict:
    """A schema-complete serialized ForecastVintageV0 — the typed
    record the R10 ``forecast_vintages`` section binds."""
    return {
        "record_type": "ForecastVintageV0",
        "vintage_id": vid,
        "provider": "r10-synth-provider",
        "data_class": "ARCHIVED_OPERATIONAL",
        "initialization_time": "2020-05-01T00:00:00Z",
        "issue_time": "2020-05-01T06:00:00Z",
        "valid_start": "2020-05-02T00:00:00Z",
        "valid_end": "2020-12-15T00:00:00Z",
        "archive_availability": "2020-05-01T12:00:00Z",
        "archive_payload_sha256": _sha_text(f"{vid}-archive-payload"),
        "retrieval_record_sha256": _sha_text(f"{vid}-retrieval"),
        "archive_payload_path": f"archive/{vid}.bin",
        "retrieval_record_path": f"retrieval/{vid}.json",
        "model_version": "r10-synth-model-v1",
        "license_id": "r10-synth-license",
        "archive_mechanism": "r10-synth-archive",
        "evidence_root": evidence_root}


def _vintage_with_bytes(root: Path, vid: str) -> dict:
    """A byte-verified vintage: real files under ``root`` whose
    sha256s are the declared digests (verify_vintage_evidence-clean)."""
    (root / "archive").mkdir(parents=True, exist_ok=True)
    (root / "retrieval").mkdir(parents=True, exist_ok=True)
    payload_blob = f"{vid} archived forecast payload".encode()
    record_blob = f"{vid} retrieval record".encode()
    (root / "archive" / f"{vid}.bin").write_bytes(payload_blob)
    (root / "retrieval" / f"{vid}.json").write_bytes(record_blob)
    v = _vintage(vid, evidence_root=str(root))
    v["archive_payload_sha256"] = hashlib.sha256(
        payload_blob).hexdigest()
    v["retrieval_record_sha256"] = hashlib.sha256(
        record_blob).hexdigest()
    return v


def _vintage_digest(v: Mapping) -> str:
    """The declared ``forecast_vintage_digests`` entry a serialized
    vintage record binds.  COORDINATION ASSUMPTION (Worker A owns the
    landed semantics): the record's ``sha256_canonical`` digest over
    its full serialized mapping — the same binding every other typed
    section in this envelope uses."""
    return sha256_canonical(dict(v))


def _forecast_with_vintages(
        vintages: list[dict], *, associable: bool) -> dict:
    """A FORECAST_REGIME canonical payload carrying a typed
    ``forecast_vintages`` section whose declared
    ``forecast_vintage_digests`` the records honestly cover — bound
    consistently across the flat field, the serialized config, and
    the typed forecast_feature_payload."""
    p = _canonical_payload("FORECAST_REGIME")
    p["forecast_vintages"] = copy.deepcopy(vintages)
    digests = [_vintage_digest(v) for v in p["forecast_vintages"]]
    p["forecast_vintage_digests"] = list(digests)
    p["config"]["forecast_vintage_digests"] = list(digests)
    p["forecast_feature_payload"]["forecast_vintage_digests"] = \
        list(digests)
    if not associable:
        # An honest demotion: CANDIDATE_ONLY over one open gate,
        # non-terminal, non-associable — the legal state a
        # metadata-only forecast artifact may sit in.
        p["status"] = "CANDIDATE_ONLY"
        p["terminal"] = False
        p["associable"] = False
        p["stability"]["required_gates"]["loro"] = False
    return _rehash(p)


# ---------------------------------------------------------------------
# Association inputs (module scope — built once)
# ---------------------------------------------------------------------

@pytest.fixture(scope="module")
def assoc() -> dict:
    events = make_events(21)
    controls = make_controls()
    return {
        "events": events,
        "controls": controls,
        "unit_basins": dict(UNIT_BASINS),
        "holdout": make_holdout(events),
        "opportunities": make_opportunities(controls),
        "artifact": regime_assignment_from_artifact(
            _canonical_payload(), artifact_id="r10-canonical")}


def _forged_artifact(artifact: RegimeAssignmentArtifact,
                   payload: Mapping) -> RegimeAssignmentArtifact:
    """The strongest artifact a forger can stamp for a mutated
    payload (R10 association evidence): the payload's own
    ``freeze_digest`` rebound onto both binding digests, and the
    mutated ``unit_basin_map`` carried verbatim with its recomputed
    digest so the partition-equality leg is not trivially failing.
    """
    kwargs: dict[str, Any] = {
        "regime_digest": payload["freeze_digest"],
        "producer_payload_digest": payload["freeze_digest"]}
    ubm = payload.get("unit_basin_map")
    if isinstance(ubm, (list, tuple)):
        kwargs["unit_basin_map"] = tuple(
            (str(e[0]), str(e[1])) for e in ubm
            if isinstance(e, (list, tuple)) and len(e) == 2)
        kwargs["unit_basin_map_digest"] = _ubm_digest(ubm)
    return dataclasses.replace(artifact, **kwargs)


# ---------------------------------------------------------------------
# Boundary runners — each returns the rejection text for evidence.
# A boundary that ACCEPTS, or that raises a non-contract exception
# where the contract demands problem lists / ValueError, fails the
# test with the actual surface recorded.
# ---------------------------------------------------------------------

def _boundary_output(boundary: str, payload: dict,
                     assoc: dict) -> str:
    if boundary == "floor":
        try:
            problems = validate_producer_payload(payload)
        except Exception as exc:
            pytest.fail(
                f"validate_producer_payload raised "
                f"{type(exc).__name__} instead of returning problem "
                f"strings — structured rejection required: {exc}")
        assert problems, \
            "validate_producer_payload accepted a mutated payload"
        return " || ".join(problems)
    if boundary == "freeze":
        try:
            freeze_regime_artifact(_pre_freeze(payload))
        except ValueError as exc:
            return str(exc)
        except Exception as exc:
            pytest.fail(
                f"freeze_regime_artifact raised "
                f"{type(exc).__name__} instead of ValueError — "
                f"structured rejection required: {exc}")
        pytest.fail(
            "freeze_regime_artifact accepted a mutated payload")
    if boundary == "adapter":
        try:
            regime_assignment_from_artifact(
                payload, artifact_id="r10-probe")
        except ValueError as exc:
            return str(exc)
        except Exception as exc:
            pytest.fail(
                f"regime_assignment_from_artifact raised "
                f"{type(exc).__name__} instead of ValueError — "
                f"structured rejection required: {exc}")
        pytest.fail(
            "regime_assignment_from_artifact accepted a mutated "
            "payload")
    if boundary == "audit":
        try:
            findings = audit_producer_payload(payload)
        except Exception as exc:
            pytest.fail(
                f"audit_producer_payload raised "
                f"{type(exc).__name__} instead of returning "
                f"findings — structured rejection required: {exc}")
        assert findings, \
            "audit_producer_payload accepted a mutated payload"
        return " || ".join(
            f"{f.code} {f.path} {f.detail}" for f in findings)
    if boundary == "association":
        # R10: bind the FORGED artifact — the payload's own
        # freeze_digest and (when mutated) its own partition — so the
        # rejection stands on the finding's semantics, not on a
        # trivially-mismatched digest.
        art = _forged_artifact(assoc["artifact"], payload)
        try:
            run_association(
                art, assoc["events"], assoc["controls"],
                assoc["unit_basins"], holdout=assoc["holdout"],
                region_basins=REGION_BASINS,
                opportunities=assoc["opportunities"],
                n_boot=10, producer_payload=payload)
        except ValueError as exc:
            return str(exc)
        except Exception as exc:
            pytest.fail(
                f"run_association raised {type(exc).__name__} "
                f"instead of ValueError — structured rejection "
                f"required: {exc}")
        pytest.fail(
            "run_association bound a mutated payload — the forged "
            "artifact verified")
    raise AssertionError(f"unknown boundary {boundary!r}")


# ---------------------------------------------------------------------
# Mutation registry — (finding, case-id, mutate, evidence-regex,
# note, forecast, post).  mutate(payload, tmp_path) -> set[str] of
# flat digest fields to leave stale in _rehash; ``post=True`` applies
# the mutation AFTER the honest rehash (non-JSON-serializable values
# — the envelope cannot carry them by construction).
# ---------------------------------------------------------------------

_Mut = Callable[[dict, Path], set]


def _m_set(field: str, value: Any, section: str | None = None):
    """Set one field (flat, or inside a digested section)."""
    def mut(p: dict, _tmp: Path) -> set:
        (p[section] if section else p)[field] = value
        return set()
    return mut


def _m_config(field: str, value: Any):
    return _m_set(field, value, "config")


def _m_pre(field: str, value: Any):
    return _m_set(field, value, "preprocessing")


def _m_null(field: str, value: Any, *, fam: str = "shuffled"):
    def mut(p: dict, _tmp: Path) -> set:
        p["nulls"][fam][field] = value
        return set()
    return mut


def _m_null_rep(field: str, value: Any = None, *,
                delete: bool = False):
    """Mutate one field of the first serialized replicate record."""
    def mut(p: dict, _tmp: Path) -> set:
        rep = p["nulls"]["shuffled"]["replicates"][0]
        if delete:
            rep.pop(field, None)
        else:
            rep[field] = value
        return set()
    return mut


def _m_forecast_field(field: str, value: Any):
    """Mutate a forecast declaration in EVERY bound copy (flat,
    config, typed forecast_feature_payload) so the copies still agree
    and only the finding's own check can reject.  ``value`` may be a
    callable evaluated against the payload."""
    def mut(p: dict, _tmp: Path) -> set:
        v = value(p) if callable(value) else value
        p[field] = copy.deepcopy(v)
        p["config"][field] = copy.deepcopy(v)
        if "forecast_feature_payload" in p:
            p["forecast_feature_payload"][field] = copy.deepcopy(v)
        return set()
    return mut


def _m_ffp(field: str, value: Any):
    return _m_set(field, value, "forecast_feature_payload")


def _m_status(status: str, terminal: bool, associable: bool, *,
              open_gate: bool = False):
    """A status/terminal/associable triple — ``open_gate`` keeps the
    gate map consistent with a demoted status so only the flag under
    test can reject."""
    def mut(p: dict, _tmp: Path) -> set:
        p["status"] = status
        p["terminal"] = terminal
        p["associable"] = associable
        if open_gate:
            p["stability"]["required_gates"]["loro"] = False
        return set()
    return mut


def _verified_source_files(root: Path, name: str = "real.bin",
                           blob: bytes = b"r10 real source bytes",
                           extra_entries: list | None = None,
                           extra_digests: list | None = None):
    """Materialize real evidence under ``root`` and return
    ``(source_files, source_digests)`` that byte-verify cleanly."""
    root.mkdir(parents=True, exist_ok=True)
    (root / name).write_bytes(blob)
    sha = hashlib.sha256(blob).hexdigest()
    files = [{"relpath": name, "sha256": sha}]
    files.extend(extra_entries or [])
    digests = [sha] + list(extra_digests or [])
    return files, digests


def _m_manifest_field(field: str, value: Any = None, *,
                      delete: bool = False):
    """A byte-verified non-fixture manifest with ONE schema field
    corrupted — real evidence on disk, so only the manifest-schema
    check can reject."""
    def mut(p: dict, tmp: Path) -> set:
        root = tmp / "evidence-src"
        files, digests = _verified_source_files(root)
        m = _nonfixture_manifest(
            root, source_files=files, source_digests=digests)
        if delete:
            m.pop(field, None)
        else:
            m[field] = value
        _bind_manifest(p, m)
        return set()
    return mut


def _m_manifest_dup_relpath(p: dict, tmp: Path) -> set:
    """Two source_files entries naming the SAME relpath, both
    byte-verified, source_digests matching the verified multiset —
    only a duplicate-relpath check can reject."""
    root = tmp / "evidence-src"
    blob = b"r10 dup-relpath bytes"
    root.mkdir(parents=True, exist_ok=True)
    (root / "real.bin").write_bytes(blob)
    sha = hashlib.sha256(blob).hexdigest()
    m = _nonfixture_manifest(
        root,
        source_files=[{"relpath": "real.bin", "sha256": sha},
                      {"relpath": "real.bin", "sha256": sha}],
        source_digests=[sha, sha])
    _bind_manifest(p, m)
    return set()


def _m_manifest_missing_sha(p: dict, tmp: Path) -> set:
    root = tmp / "evidence-src"
    files, digests = _verified_source_files(root)
    m = _nonfixture_manifest(
        root,
        source_files=[{"relpath": "real.bin"}],
        source_digests=digests)
    _bind_manifest(p, m)
    return set()


def _m_symlink_component(p: dict, tmp: Path) -> set:
    """``evidence/links`` is a symlink to ``evidence/realdir`` — the
    relpath ``links/ev.bin`` resolves INSIDE the root and hashes
    correctly, so only a per-component symlink walk rejects."""
    root = tmp / "evidence"
    real = root / "realdir"
    real.mkdir(parents=True, exist_ok=True)
    blob = b"r10 component-symlink target bytes"
    (real / "ev.bin").write_bytes(blob)
    sha = hashlib.sha256(blob).hexdigest()
    try:
        (root / "links").symlink_to("realdir",
                                    target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unsupported here: {exc}")
    m = _nonfixture_manifest(
        root,
        source_files=[{"relpath": "links/ev.bin", "sha256": sha}],
        source_digests=[sha])
    _bind_manifest(p, m)
    return set()


def _m_symlink_root(p: dict, tmp: Path) -> set:
    """The evidence_root itself is a symlink to a real directory —
    resolution hides the indirection, so only a refuse-symlinked-root
    policy rejects."""
    real_root = tmp / "real-evidence"
    files, digests = _verified_source_files(real_root)
    link_root = tmp / "root-link"
    try:
        link_root.symlink_to(real_root, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"symlink unsupported here: {exc}")
    m = _nonfixture_manifest(
        link_root, source_files=files, source_digests=digests)
    _bind_manifest(p, m)
    return set()


def _m_forecast_vintages(vintages: list[dict]):
    """Carry a typed forecast_vintages section whose declared digests
    the records honestly cover."""
    def mut(p: dict, _tmp: Path) -> set:
        p["forecast_vintages"] = copy.deepcopy(vintages)
        digests = [_vintage_digest(v) for v in vintages]
        p["forecast_vintage_digests"] = digests
        p["config"]["forecast_vintage_digests"] = list(digests)
        if "forecast_feature_payload" in p:
            p["forecast_feature_payload"][
                "forecast_vintage_digests"] = list(digests)
        return set()
    return mut


def _m_vintages_uncovered(p: dict, _tmp: Path) -> set:
    """Well-formed records bound into forecast_vintages, but the
    DECLARED forecast_vintage_digests name a digest no record
    produces — the records do not cover the declaration."""
    p["forecast_vintages"] = [_vintage("r10-v0")]
    declared = [_sha_text("r10-uncovered-vintage")]
    p["forecast_vintage_digests"] = declared
    p["config"]["forecast_vintage_digests"] = list(declared)
    if "forecast_feature_payload" in p:
        p["forecast_feature_payload"][
            "forecast_vintage_digests"] = list(declared)
    return set()


class _Case:
    def __init__(self, finding: str, case_id: str, mut: _Mut,
                 evidence: str, note: str, forecast: bool = False,
                 post: bool = False):
        self.finding = finding
        self.case_id = case_id
        self.mut = mut
        self.evidence = re.compile(evidence, re.IGNORECASE)
        self.note = note
        self.forecast = forecast
        self.post = post

    def __repr__(self):
        return f"<Case {self.finding}/{self.case_id}>"


_OVERFLOW = 10 ** 400     # int — JSON-serializable, float() overflows

MUTATION_CASES: list[_Case] = [
    # ---- P01: overflow-safe numerics — 10**400 is an int (JSON-
    # legal) whose float() raises OverflowError; the floor must
    # return a problem and every boundary a structured rejection,
    # never a propagated exception -------------------------------
    _Case("P01", "input_values-int-overflow",
          lambda p, _t: (p["input_values"][0].__setitem__(
              0, _OVERFLOW), set())[1],
          r"input_values|undecodable|finite|overflow|decode|SCHEMA",
          "float(10**400) overflows — the input_values decode must "
          "be overflow-safe at every boundary"),
    _Case("P01", "model-weights-int-overflow",
          lambda p, _t: (p["model"]["weights"].__setitem__(
              0, _OVERFLOW), set())[1],
          r"weights|finite|overflow|model|SCHEMA",
          "model.weights finiteness must not crash on float() "
          "overflow"),
    _Case("P01", "model-means-int-overflow",
          lambda p, _t: (p["model"]["means"][0].__setitem__(
              0, _OVERFLOW), set())[1],
          r"means|finite|overflow|model|SCHEMA",
          "model.means finiteness must not crash on float() "
          "overflow"),
    _Case("P01", "occupancy-int-overflow",
          lambda p, _t: (p["occupancy"].__setitem__(
              0, _OVERFLOW), set())[1],
          r"occupancy|finite|overflow|SCHEMA",
          "occupancy non-negativity must not crash on float() "
          "overflow"),
    # literal nan/inf — non-JSON, applied post-rehash
    _Case("P01", "input_values-nan-literal",
          lambda p, _t: (p["input_values"][0].__setitem__(
              0, float("nan")), set())[1],
          r"input_values|undecodable|finite|canonically|SCHEMA",
          "a literal NaN is outside the encoded domain (None/'"
          "Infinity' tokens) — undecodable or unserializable",
          post=True),
    _Case("P01", "input_values-inf-literal",
          lambda p, _t: (p["input_values"][0].__setitem__(
              0, float("inf")), set())[1],
          r"input_values|undecodable|finite|canonically|SCHEMA",
          "a literal +inf is outside the encoded domain",
          post=True),
    _Case("P01", "occupancy-nan",
          lambda p, _t: (p["occupancy"].__setitem__(
              0, float("nan")), set())[1],
          r"occupancy|finite|canonically|SCHEMA",
          "NaN is not < 0 — only a finiteness check rejects it",
          post=True),
    _Case("P01", "occupancy-inf",
          lambda p, _t: (p["occupancy"].__setitem__(
              0, float("inf")), set())[1],
          r"occupancy|finite|canonically|SCHEMA",
          "+inf is not < 0 — only a finiteness check rejects it",
          post=True),
    _Case("P01", "model-weights-nan",
          lambda p, _t: (p["model"]["weights"].__setitem__(
              0, float("nan")), set())[1],
          r"weights|finite|canonically|model|SCHEMA",
          "NaN weights defeat the sum-to-1 check only if finiteness "
          "runs first", post=True),

    # ---- P02: complete config semantics --------------------------
    _Case("P02", "config-n_bootstrap-zero",
          _m_config("n_bootstrap", 0),
          r"n_bootstrap|config",
          "a zero bootstrap count is not a stability protocol"),
    _Case("P02", "config-n_null_replicates-zero",
          _m_config("n_null_replicates", 0),
          r"n_null_replicates|null|config",
          "zero declared null replicates cannot back the bound null "
          "evidence"),
    _Case("P02", "config-null_alpha-two",
          _m_config("null_alpha", 2),
          r"null_alpha|alpha|config",
          "an alpha of 2 is outside (0, 1)"),
    _Case("P02", "config-max_missingness-two",
          _m_config("max_missingness", 2),
          r"max_missingness|missingness|config",
          "a missingness cap of 2.0 is outside [0, 1]"),
    _Case("P02", "config-date_col-empty",
          _m_config("date_col", ""),
          r"date_col|config",
          "an empty column name is shape-illegal at the floor — "
          "frame membership of a non-empty name is run-preflight "
          "(regimes.py:644) and covered by the run-level test"),
    _Case("P02", "config-unit_col-nonstr",
          _m_config("unit_col", 5),
          r"unit_col|config",
          "a non-string column name is shape-illegal at the "
          "floor — membership is the run boundary's job"),
    _Case("P02", "config-gap_policy-bogus",
          _m_config("gap_policy", "sliding"),
          r"gap_policy|config",
          "'sliding' is outside the declared gap-policy vocabulary"),
    _Case("P02", "config-cadence-bogus",
          _m_config("cadence", "nonsense"),
          r"cadence|config",
          "'nonsense' is outside the declared cadence vocabulary"),
    _Case("P02", "config-effort_split-bogus",
          _m_config("effort_split", "bogus"),
          r"effort_split|config",
          "'bogus' is outside the declared effort-split vocabulary"),
    _Case("P02", "config-fold_seed_policy-bogus",
          _m_config("fold_seed_policy", "bogus"),
          r"fold_seed_policy|config",
          "'bogus' is outside the declared fold-seed-policy "
          "vocabulary"),
    _Case("P02", "config-fitted_on-all-data",
          _m_config("fitted_on", "ALL_DATA"),
          r"fitted_on|config",
          "config.fitted_on must be TRAIN_ONLY, matching the "
          "artifact's flat declaration"),
    _Case("P02", "config-label_blinding-string",
          _m_config("label_blinding", "yes"),
          r"label_blinding|config",
          "the string 'yes' is not a boolean blinding verdict"),
    _Case("P02", "config-elevation_col-int",
          _m_config("elevation_col", 5),
          r"elevation_col|config",
          "elevation_col must be a column name or null, not an int"),
    _Case("P02", "config-era_boundaries-int",
          _m_config("era_boundaries", [5]),
          r"era_boundaries|era|config",
          "era boundaries must be real boundary declarations, not "
          "bare ints"),
    _Case("P02", "config-k_candidates-dup",
          _m_config("k_candidates", [1, 2, 2]),
          r"k_candidates|config|distinct|duplicate",
          "a duplicated candidate is not a new sweep point (also "
          "leaves modal k=3 outside the set — either check must "
          "name the config defect)"),
    _Case("P02", "config-train_groups-bogus-dup",
          _m_config("train_groups", ["a", "a"]),
          r"train_groups|config",
          "undeclared AND duplicated train groups"),
    _Case("P02", "config-heldout_groups-empty",
          _m_config("heldout_groups", []),
          r"heldout_groups|config|heldout",
          "an empty config holdout diverges from "
          "heldout_groups_declared"),
    _Case("P02", "config-seeds-dup",
          _m_config("seeds", [1, 2, 2]),
          r"seeds|config",
          "config.seeds must equal the declared seed set — [1,2,2] "
          "diverges and duplicates"),
    _Case("P02", "config-train-heldout-overlap",
          _m_config("train_groups",
                    list(_FIT_GROUPS) + [_HELDOUT_GROUPS[0]]),
          r"overlap|train_groups|heldout|config",
          "config.train_groups straddles a held-out group — the "
          "fit/holdout universes are disjoint inside config too"),
    _Case("P02", "config-train_groups-empty",
          _m_config("train_groups", []),
          r"train_groups|config|fit_groups",
          "an empty config train_groups cannot have produced the "
          "declared fit surface"),

    # ---- P03: duplicates inside declared lists --------------------
    _Case("P03", "config-k_candidates-dup-contains-k",
          _m_config("k_candidates", [1, 2, 3, 3]),
          r"k_candidates|distinct|duplicate|config",
          "the candidate set still covers modal k=3 — ONLY the "
          "distinctness check can reject"),
    _Case("P03", "config-train_groups-dup",
          _m_config("train_groups",
                    list(_FIT_GROUPS) + [_FIT_GROUPS[0]]),
          r"train_groups|distinct|duplicate|config",
          "the fit surface is fully covered — ONLY the distinctness "
          "check can reject"),
    _Case("P03", "forecast_feature_set-dup",
          _m_forecast_field("forecast_feature_set",
                            ["synth_f1", "synth_f1"]),
          r"forecast_feature_set|distinct|duplicate|forecast",
          "a duplicated forecast feature is not a second feature",
          forecast=True),
    _Case("P03", "forecast_vintage_digests-dup",
          _m_forecast_field(
              "forecast_vintage_digests",
              lambda p: [p["forecast_vintage_digests"][0]] * 2),
          r"forecast_vintage_digests|distinct|duplicate|vintage|"
          r"forecast",
          "the same vintage digest twice is not two vintages",
          forecast=True),

    # ---- P05: preprocessing + forecast-payload bindings -----------
    _Case("P05", "row_keys_digest-bogus",
          _m_pre("row_keys_digest", "bogus"),
          r"row_keys_digest|preprocessing|digest",
          "a truthy non-hex row_keys_digest is malformed, not "
          "merely mismatched"),
    _Case("P05", "row_keys_digest-short-string",
          _m_pre("row_keys_digest", "5"),
          r"row_keys_digest|preprocessing|digest",
          "'5' is not a 64-hex sha256"),
    _Case("P05", "row_keys_digest-int",
          _m_pre("row_keys_digest", 5),
          r"row_keys_digest|preprocessing|digest",
          "an int is not a sha256 binding"),
    _Case("P05", "row_keys_digest-forged-hex",
          _m_pre("row_keys_digest", "f" * 64),
          r"row_keys_digest|recompute|preprocessing",
          "well-formed 64-hex, wrong value — only recomputation "
          "rejects"),
    _Case("P05", "feature_order-permuted",
          _m_pre("feature_order",
                 ["synth_f2", "synth_f1"]),
          r"feature_order|preprocessing|feature",
          "the recorded transform order must equal the declared "
          "feature_cols"),
    _Case("P05", "scaler_mean-wrong-length",
          _m_pre("scaler_mean", [0.0]),
          r"scaler|preprocessing",
          "one scaler statistic cannot cover two features"),
    _Case("P05", "imputer_statistics-nan",
          lambda p, _t: (p["preprocessing"][
              "imputer_statistics"].__setitem__(0, float("nan")),
              set())[1],
          r"imputer|preprocessing|finite|canonically|SCHEMA",
          "a NaN imputation statistic is not a median — finiteness "
          "must run on the serialized statistics",
          post=True),
    _Case("P05", "ffp-row_count-string",
          _m_ffp("row_count", "10"),
          r"row_count|forecast",
          "'10' is not an integer row count — the typed record "
          "admits ints only",
          forecast=True),
    _Case("P05", "ffp-row_count-zero",
          _m_ffp("row_count", 0),
          r"row_count|forecast",
          "zero rows is not the bound frame",
          forecast=True),
    _Case("P05", "ffp-row_count-negative",
          _m_ffp("row_count", -1),
          r"row_count|forecast",
          "a negative row count is not a frame size",
          forecast=True),
    _Case("P05", "ffp-row_keys-forged-hex",
          _m_ffp("row_keys_digest", "e" * 64),
          r"row_keys|forecast",
          "well-formed 64-hex, wrong value — only recomputation "
          "rejects",
          forecast=True),

    # ---- P07: non-fixture source-manifest schema ------------------
    _Case("P07", "manifest-source_id-int",
          _m_manifest_field("source_id", 7),
          r"source_id|source_manifest",
          "an int source_id is not a source identifier — verified "
          "bytes cannot launder a malformed schema field"),
    _Case("P07", "manifest-units-string",
          _m_manifest_field("units", "all"),
          r"units|source_manifest",
          "'all' is a string, not a unit list — the declared unit "
          "universe is unbound"),
    _Case("P07", "manifest-feature_allowlist-string",
          _m_manifest_field("feature_allowlist", "x"),
          r"feature_allowlist|source_manifest",
          "'x' is not a feature allowlist"),
    _Case("P07", "manifest-lineage-int",
          _m_manifest_field("lineage", 123),
          r"lineage|source_manifest",
          "an int is not a lineage declaration"),
    _Case("P07", "manifest-missing-evidence_root",
          _m_manifest_field("evidence_root", delete=True),
          r"evidence_root|source_manifest|evidence",
          "a non-fixture manifest without evidence_root cannot "
          "name its bytes"),
    _Case("P07", "manifest-duplicate-relpaths",
          _m_manifest_dup_relpath,
          r"duplicate|relpath|source_files",
          "two entries binding the same relpath — the multiset "
          "still verifies, so only a duplicate-relpath check "
          "rejects"),
    _Case("P07", "manifest-entry-missing-sha256",
          _m_manifest_missing_sha,
          r"sha256|source_files",
          "a source_files entry without its declared digest"),

    # ---- P08: symlink-component policy on evidence paths ----------
    _Case("P08", "evidence-intermediate-dir-symlink",
          _m_symlink_component,
          r"symlink|evidence|source_files|link",
          "a symlinked intermediate directory — the final component "
          "is a real file inside the root, so only a per-component "
          "walk rejects"),
    _Case("P08", "evidence-root-symlinked",
          _m_symlink_root,
          r"symlink|evidence_root|evidence|root",
          "a symlinked evidence_root — resolution hides the "
          "indirection; the policy must refuse the link itself"),

    # ---- P09: status × terminal × associable state machine --------
    _Case("P09", "descriptive-terminal-false",
          _m_status("DESCRIPTIVE_REGIME_ONLY", False, True),
          r"terminal|status|DESCRIPTIVE",
          "a terminal status claim cannot carry terminal=false"),
    _Case("P09", "descriptive-associable-false",
          _m_status("DESCRIPTIVE_REGIME_ONLY", True, False),
          r"associable|status|DESCRIPTIVE",
          "a fully-gated descriptive artifact marked "
          "non-associable is incoherent"),
    _Case("P09", "candidate-terminal-true",
          _m_status("CANDIDATE_ONLY", True, False, open_gate=True),
          r"terminal|status|CANDIDATE",
          "a demoted candidate cannot claim terminal status"),
    _Case("P09", "candidate-associable-true",
          _m_status("CANDIDATE_ONLY", False, True, open_gate=True),
          r"associable|status|CANDIDATE",
          "a demoted candidate cannot be associable"),
    _Case("P09", "unstable-associable-true",
          _m_status("UNSUPERVISED_STRUCTURE_NOT_STABLE", True, True,
                    open_gate=True),
          r"associable|status|STABLE",
          "an unstable partition can never be associable"),
    _Case("P09", "unstable-terminal-false",
          _m_status("UNSUPERVISED_STRUCTURE_NOT_STABLE", False,
                    False, open_gate=True),
          r"terminal|status|STABLE",
          "the declared unstable verdict is a terminal producer "
          "status — terminal=false is incoherent"),
    _Case("P09", "status-run_error",
          _m_status("RUN_ERROR", False, False),
          r"RUN_ERROR|status",
          "an errored artifact cannot freeze or bind"),

    # ---- P10: null-family validation -------------------------------
    _Case("P10", "family-status-maybe",
          _m_null("status", "MAYBE"),
          r"status|null|family",
          "'MAYBE' is outside the declared null-family status "
          "vocabulary"),
    _Case("P10", "family-pass-with-reason",
          lambda p, _t: (p["nulls"]["shuffled"].__setitem__(
              "reason", "post-hoc excuse"), set())[1],
          r"reason|status|PASS|null",
          "a PASS verdict carries no failure reason — reason must "
          "be null under PASS"),
    _Case("P10", "family-fail-without-reason",
          _m_null("status", "FAIL"),
          r"reason|status|FAIL|null",
          "a FAIL verdict without a reason is unaccountable "
          "(reason stays null — canonical record already null)"),
    _Case("P10", "family-p_value-negative",
          _m_null("p_value", -0.1),
          r"p_value|null",
          "a p-value below 0 is not a probability"),
    _Case("P10", "family-p_value-above-one",
          _m_null("p_value", 2.5),
          r"p_value|null",
          "a p-value above 1 is not a probability"),
    _Case("P10", "family-p_value-string",
          _m_null("p_value", "x"),
          r"p_value|null",
          "'x' is not a probability"),
    _Case("P10", "family-alpha-zero",
          _m_null("alpha", 0),
          r"alpha|null",
          "an alpha of 0 is not a significance level"),
    _Case("P10", "family-alpha-one",
          _m_null("alpha", 1),
          r"alpha|null",
          "an alpha of 1 is not a significance level"),
    _Case("P10", "family-alpha-string",
          _m_null("alpha", "x"),
          r"alpha|null",
          "'x' is not a significance level"),
    _Case("P10", "family-statistic-rand_index",
          _m_null("statistic", "rand_index"),
          r"statistic|null",
          "'rand_index' is outside the declared null statistic "
          "vocabulary — the bound evidence names its statistic"),
    _Case("P10", "nulls-unknown-family",
          lambda p, _t: (p["nulls"].__setitem__(
              "bogus_null", copy.deepcopy(
                  p["nulls"]["shuffled"])), set())[1],
          r"family|null|undeclared|bogus",
          "a third family record the protocol never declared — "
          "honestly digested, so only the family vocabulary "
          "rejects"),
    _Case("P10", "family-unknown-field",
          _m_null("forged_field", 1),
          r"field|family|null|undeclared",
          "an undeclared field inside a bound family record"),
    _Case("P10", "replicate-k-outside-candidates",
          _m_null_rep("k", 99),
          r"replicate|k|null",
          "a replicate fitted at k=99 was never a declared "
          "candidate"),
    _Case("P10", "replicate-stat-string",
          _m_null_rep("stat", "x"),
          r"replicate|stat|null",
          "'x' is not a replicate statistic value"),
    _Case("P10", "replicate-ok-string",
          _m_null_rep("ok", "yes"),
          r"replicate|ok|null",
          "'yes' is not a boolean convergence verdict"),
    _Case("P10", "replicate-i-string",
          _m_null_rep("i", "0"),
          r"replicate|i|null",
          "'0' is not an integer replicate index"),
    _Case("P10", "replicate-missing-field",
          _m_null_rep("fit_seed", delete=True),
          r"replicate|null|fit_seed|field",
          "a replicate record missing a required serialized field "
          "— the bound replicate surface is exact"),
    _Case("P10", "replicate-extra-field",
          _m_null_rep("forged", 1),
          r"replicate|null|field|undeclared",
          "an undeclared field inside a bound replicate record"),
    _Case("P10", "n_succeeded-exceeds-n_replicates",
          lambda p, _t: (p["nulls"]["shuffled"].__setitem__(
              "n_succeeded",
              p["nulls"]["shuffled"]["n_replicates"] + 5),
              set())[1],
          r"n_replicates|n_succeeded|null",
          "executed replicates cannot exceed the declared count — "
          "the bound (not equality) check already landed in R9"),
    _Case("P10", "replicate-fit_seed-outside-seeds",
          _m_null_rep("fit_seed", 999),
          r"fit_seed|replicate|seed|null",
          "a replicate fit seed outside seeds_declared is an "
          "undeclared run"),
    _Case("P10", "null_k-outside-candidates",
          _m_null("null_k_distribution", {"9": 50}),
          r"null_k_distribution|null|k",
          "null replicates fitted at k=9 were never declared "
          "candidates"),

    # ---- P11: exact field sets everywhere --------------------------
    _Case("P11", "top-level-unknown-field",
          _m_set("forged_top_field", 1),
          r"field|undeclared|unknown|payload",
          "an unbound top-level field — honestly rehashed, so only "
          "the exact field set rejects"),
    _Case("P11", "config-unknown-field",
          _m_config("forged_field", 1),
          r"config|field|undeclared|unknown",
          "the serialized producer config admits exactly the "
          "declared RegimeRunConfig fields"),
    _Case("P11", "model-unknown-field",
          lambda p, _t: (p["model"].__setitem__(
              "forged_field", 1), set())[1],
          r"model|field|weights|means|covariances",
          "model admits exactly {weights, means, covariances} — "
          "already enforced"),
    _Case("P11", "input_schema-unknown-field",
          lambda p, _t: (p["input_schema"].__setitem__(
              "forged_field", 1), set())[1],
          r"input_schema|field|undeclared",
          "the input schema record admits exactly its declared "
          "fields"),
    _Case("P11", "preprocessing-unknown-field",
          _m_pre("forged_field", 1),
          r"preprocessing|field|undeclared",
          "the preprocessing record admits exactly its declared "
          "fields"),
    _Case("P11", "stability-unknown-field",
          lambda p, _t: (p["stability"].__setitem__(
              "forged_field", 1), set())[1],
          r"stability|field|undeclared",
          "the stability record admits exactly its declared "
          "fields"),
    _Case("P11", "missingness-unknown-field",
          lambda p, _t: (p["missingness_applied"].__setitem__(
              "forged_field", 1), set())[1],
          r"missingness|field|undeclared",
          "the applied-missingness record admits exactly its "
          "declared fields"),
    _Case("P11", "nulls-unknown-field",
          lambda p, _t: (p["nulls"].__setitem__(
              "forged_field", 1), set())[1],
          r"nulls|null|field|undeclared",
          "the nulls section admits exactly its declared keys — "
          "a bare non-mapping value is not a family record"),
    _Case("P11", "nulls-family-unknown-field",
          _m_null("another_forged", 2),
          r"family|null|field|undeclared",
          "the family record admits exactly its declared fields"),
    _Case("P11", "fit_partition-unknown-field",
          lambda p, _t: (p["fit_partition"].__setitem__(
              "forged_field", 1), set())[1],
          r"fit_partition|field|undeclared",
          "the typed partition admits exactly its declared fields "
          "— already enforced"),
    _Case("P11", "ffp-unknown-field",
          _m_ffp("forged_field", 1),
          r"forecast_feature_payload|field|undeclared|forecast",
          "the typed forecast payload admits exactly its declared "
          "fields — already enforced",
          forecast=True),

    # ---- P12: typed forecast_vintages section ----------------------
    _Case("P12", "forecast-associable-no-vintages",
          lambda p, _t: (p.pop("forecast_vintages", None), set())[1],
          # pop explicitly — robust whether or not the canonical
          # builder carries the section
          r"forecast_vintages|vintage|forecast",
          "an associable FORECAST artifact must bind its declared "
          "vintages as typed records — declared digests alone are "
          "not evidence",
          forecast=True),
    _Case("P12", "forecast-associable-metadata-only-vintages",
          _m_forecast_vintages([_vintage("r10-v0")]),
          r"forecast_vintages|vintage|evidence|forecast",
          "metadata-only candidate vintages (evidence_root='') "
          "cannot ground an associable forecast claim — byte "
          "evidence is owed",
          forecast=True),
    _Case("P12", "retro-with-forecast_vintages",
          lambda p, _t: (p.__setitem__(
              "forecast_vintages", [_vintage("r10-v0")]), set())[1],
          r"forecast_vintages|vintage|forecast|RETROSPECTIVE",
          "forecast evidence cannot ride a retrospective artifact "
          "— the section is forbidden under RETROSPECTIVE_REGIME"),
    _Case("P12", "vintages-do-not-cover-declared",
          _m_vintages_uncovered,
          r"forecast_vintages|vintage|digest|cover|forecast",
          "the declared forecast_vintage_digests name a vintage no "
          "bound record produces",
          forecast=True),
    _Case("P12", "vintage-record-malformed",
          _m_forecast_vintages(
              [{"record_type": "ForecastVintageV0",
                "vintage_id": "r10-broken"}]),
          r"forecast_vintages|vintage|ForecastVintageV0",
          "an incomplete vintage record cannot deserialize as "
          "ForecastVintageV0",
          forecast=True),
]

BOUNDARIES = ("floor", "freeze", "adapter", "audit", "association")


def _mutated_payload(case: _Case, tmp_path: Path) -> dict:
    payload = _canonical_payload(
        mode="FORECAST_REGIME" if case.forecast
        else "RETROSPECTIVE_REGIME")
    if case.post:
        # Non-JSON-serializable mutation — applied over the honest
        # envelope; carried digests stay stale by construction.
        case.mut(payload, tmp_path)
        return payload
    skip = case.mut(payload, tmp_path) or set()
    return _rehash(payload, skip=skip)


# ---------------------------------------------------------------------
# Positive controls — the unmutated payloads pass every boundary
# ---------------------------------------------------------------------

class TestPositiveControls:
    def test_canonical_payload_floor_clean(self):
        assert validate_producer_payload(_canonical_payload()) == []

    def test_canonical_payload_freezes(self):
        frozen = freeze_regime_artifact(
            _pre_freeze(_canonical_payload()))
        assert frozen["frozen"] is True
        assert frozen["freeze_digest"] == sha256_canonical(
            {k: v for k, v in frozen.items()
             if k not in ("freeze_digest", "frozen")})

    def test_canonical_payload_adapts(self):
        artifact = regime_assignment_from_artifact(
            _canonical_payload(), artifact_id="r10-canonical")
        assert dict(artifact.unit_basin_map) == dict(UNIT_BASINS)

    def test_canonical_payload_audits_clean(self):
        assert audit_producer_payload(_canonical_payload()) == []

    def test_canonical_payload_binds_verified(self, assoc):
        report = run_association(
            assoc["artifact"], assoc["events"], assoc["controls"],
            assoc["unit_basins"], holdout=assoc["holdout"],
            region_basins=REGION_BASINS,
            opportunities=assoc["opportunities"],
            n_boot=10, producer_payload=_canonical_payload())
        assert report.binding == "verified_producer_payload"

    def test_canonical_forecast_payload_floor_state(self):
        """The r9 canonical forecast builder declares
        forecast_vintage_digests + associable=True but carries no
        forecast_vintages section.  Under the landed R10-P12
        contract an associable FORECAST artifact owes byte-bound
        vintage records, so the honest outcomes are exactly two:
        clean (the builder was upgraded to carry bound records)
        or a failure naming ONLY the missing-vintages
        requirement — any other rejection of the canonical
        artifact is a floor defect.  The honest associable-
        forecast control lives in
        TestP12ForecastVintageAcceptance."""
        problems = validate_producer_payload(
            _canonical_payload("FORECAST_REGIME"))
        if problems:
            assert all("forecast_vintages" in p
                       for p in problems), problems

    def test_descriptive_over_open_gate_rejected(self):
        """Regression — the R9 state coherence: a terminal
        descriptive status may not sit over an open required
        gate."""
        payload = _canonical_payload()
        payload["stability"]["required_gates"]["loro"] = False
        _rehash(payload)
        out = validate_producer_payload(payload)
        assert out and any(
            re.search(r"gate|required_gates|DESCRIPTIVE", p,
                      re.IGNORECASE) for p in out)


# ---------------------------------------------------------------------
# The mutation matrix — every finding fails closed at every boundary
# ---------------------------------------------------------------------

class TestR10MutationMatrix:
    @pytest.mark.parametrize("case", MUTATION_CASES,
                             ids=lambda c: f"{c.finding}-{c.case_id}")
    @pytest.mark.parametrize("boundary", BOUNDARIES)
    def test_finding_fails_closed(self, case: _Case, boundary: str,
                                  tmp_path: Path, assoc: dict):
        payload = _mutated_payload(case, tmp_path)
        out = _boundary_output(boundary, payload, assoc)
        # Evidence: the rejection text must name the finding's
        # category — at the association boundary this is the
        # finding's own token embedded in the binding's problem
        # list (floor problems prefix "producer_payload"), never a
        # bare ValueError on a stale envelope.
        assert case.evidence.search(out), (
            f"{case.finding}/{case.case_id}@{boundary}: rejection "
            f"does not name the finding: {out[:600]}")


# ---------------------------------------------------------------------
# P06 — direct RegimeAssignmentArtifact construction: the partition
# floor is the artifact's own problems(), and even a fully
# well-formed direct artifact binds only descriptively.
# ---------------------------------------------------------------------

class TestP06DirectArtifactConstruction:
    def _kwargs(self, artifact, **over) -> dict:
        kw = dict(
            artifact_id="r10-direct",
            regime_digest="0" * 64,
            assignments=artifact.assignments,
            producer_payload_digest="1" * 64,
            unit_basin_map=artifact.unit_basin_map,
            fitted_on="TRAIN_ONLY",
            label_blinding=True,
            seeds=artifact.seeds,
            mode="RETROSPECTIVE_REGIME")
        kw.update(over)
        return kw

    @pytest.mark.parametrize("ubm_fn,token", [
        (lambda m: (("unit-koshi-0", None),) + tuple(m[1:]),
         r"unit|group|pair|non-empty|None"),
        (lambda m: (("unit-koshi-0", ""),) + tuple(m[1:]),
         r"non-empty|unit|group"),
        (lambda m: tuple(m) + (("unit-ghost-0", "koshi"),),
         r"cover|extra|unit|assignment|partition"),
        (lambda m: tuple(m[:-1]),
         r"cover|missing|unit|assignment|partition"),
    ], ids=["unit-none", "unit-empty", "extra-unit",
            "missing-unit"])
    def test_malformed_partitions_named(self, assoc, ubm_fn,
                                        token):
        """problems() must be non-empty and name the partition
        defect — a (None,) group, an empty group, a unit outside
        the assignment universe, and an uncovered unit are all
        invalid partitions."""
        artifact = RegimeAssignmentArtifact(
            **self._kwargs(
                assoc["artifact"],
                unit_basin_map=ubm_fn(
                    list(assoc["artifact"].unit_basin_map))))
        problems = artifact.problems()
        assert problems, (
            "direct artifact with a malformed unit_basin_map "
            "produced no problems")
        assert re.search(token, " | ".join(problems), re.IGNORECASE), \
            f"problems do not name the defect: {problems}"

    def test_wellformed_direct_artifact_stays_descriptive(
            self, assoc):
        """A direct construction that clears its own problems() is
        still admitted ONLY as a local record: binding stays
        local_artifact_unverified and SUPPORTED is unreachable."""
        artifact = RegimeAssignmentArtifact(
            **self._kwargs(assoc["artifact"]))
        assert artifact.problems() == []
        rep = run_association(
            artifact, assoc["events"], assoc["controls"],
            assoc["unit_basins"], holdout=assoc["holdout"],
            region_basins=REGION_BASINS,
            opportunities=assoc["opportunities"], n_boot=10,
            producer_payload=None)
        assert rep.binding == BINDING_UNVERIFIED
        assert rep.status != STATUS_SUPPORTED


# ---------------------------------------------------------------------
# P12 — forecast_vintages acceptance semantics: the positive side of
# the section contract (the rejection half lives in the matrix).
# ---------------------------------------------------------------------

class TestP12ForecastVintageAcceptance:
    def test_wellformed_vintages_accept_under_demotion(self):
        """Metadata-only candidate vintages + an honest demotion
        (CANDIDATE_ONLY, non-terminal, non-associable): the floor
        must ACCEPT — candidates may flow through descriptive lanes;
        only the associable claim owes bytes."""
        payload = _forecast_with_vintages(
            [_vintage("r10-v0")], associable=False)
        assert validate_producer_payload(payload) == []

    def test_byte_verified_vintages_accept_when_associable(
            self, tmp_path):
        """Honest associable forecast control: real evidence files
        under tmp_path, digests bound, associable=True — the floor
        accepts (and the audit reports no findings)."""
        v = _vintage_with_bytes(tmp_path / "vintage-evidence",
                                "r10-v0")
        payload = _forecast_with_vintages([v], associable=True)
        assert validate_producer_payload(payload) == []
        assert audit_producer_payload(payload) == []

    def test_metadata_only_vintages_reject_when_associable(self):
        """The matrix half, stated positively at the floor: the same
        candidate vintages under associable=True must reject."""
        payload = _forecast_with_vintages(
            [_vintage("r10-v0")], associable=True)
        assert validate_producer_payload(payload), (
            "metadata-only vintages must not ground an associable "
            "forecast claim")


# ---------------------------------------------------------------------
# Matrix accounting — prove the census this file claims
# ---------------------------------------------------------------------

class TestMatrixAccounting:
    def test_every_finding_has_cases(self):
        covered = {c.finding for c in MUTATION_CASES}
        # P06 is direct-artifact construction (dedicated class);
        # R10 has no P04 lane — run_manifest forgery was R9-P04.
        expected = {"P01", "P02", "P03", "P05", "P07", "P08",
                    "P09", "P10", "P11", "P12"}
        assert covered == expected, (
            f"findings without mutation cases: "
            f"{sorted(expected - covered)}")

    def test_case_count_per_finding(self):
        counts: dict[str, int] = {}
        for c in MUTATION_CASES:
            counts[c.finding] = counts.get(c.finding, 0) + 1
        floors = {"P01": 8, "P02": 15, "P03": 4, "P05": 10,
                  "P07": 6, "P08": 2, "P09": 7, "P10": 15,
                  "P11": 10, "P12": 5}
        for f, n in floors.items():
            assert counts.get(f, 0) >= n, \
                f"{f}: {counts.get(f, 0)} mutations < spec floor {n}"

    def test_every_case_uses_all_boundaries(self):
        assert len(BOUNDARIES) == 5
        assert all(c.finding and c.case_id and c.mut
                   for c in MUTATION_CASES)


class TestRunLevelColumnMembership:
    """R10-P02 boundary arbitration: the serialized-payload floor
    enforces column-name TYPE/vocabulary (non-empty strings) but
    cannot see the frame — membership in the frame is the run
    boundary's check.  These tests prove the run boundary fails
    closed on bogus-but-shape-legal column declarations."""

    def test_bogus_column_names_run_error(self):
        import dataclasses
        from tests.test_experiment_v0_b4_audit import (
            _mini_regime_frame)
        from nepal.science_v0.regimes import run_regimes
        df, feature_cols, train_mask, config = _mini_regime_frame()
        for field in ("date_col", "unit_col", "season_col",
                      "group_col"):
            cfg = dataclasses.replace(
                config, **{field: "bogus_column"})
            rep = run_regimes(df, feature_cols, train_mask, cfg)
            assert rep.get("status") == "RUN_ERROR", field
            assert "bogus_column" in str(rep.get("reason")), field

    def test_bogus_optional_column_names_run_error(self):
        import dataclasses
        from tests.test_experiment_v0_b4_audit import (
            _mini_regime_frame)
        from nepal.science_v0.regimes import run_regimes
        df, feature_cols, train_mask, config = _mini_regime_frame()
        for field in ("elevation_col", "effort_col", "era_col"):
            cfg = dataclasses.replace(
                config, **{field: "bogus_column"})
            rep = run_regimes(df, feature_cols, train_mask, cfg)
            assert rep.get("status") == "RUN_ERROR", field


class TestR10Point1Residuals:
    """Round-10.1 residual regressions — the concrete malformed-value
    families the post-R10 audit reproduced, now closed by the
    JSON-shape preflight + str-guarded vocab lookups + the
    exception-safe wrapper (R10.1-A) and the exact fixture surface
    (R10.1-B).  Deferred items R10.1-C..L are registered in
    GAP_REGISTER_V0.md with their stage gates — not hidden here."""

    @pytest.mark.parametrize("mut", [
        lambda p: p.__setitem__("status", []),
        lambda p: p.__setitem__("status", {}),
        lambda p: p.__setitem__("status", {"x": 1}),
        lambda p: p["config"].__setitem__("mode", []),
        lambda p: p["config"].__setitem__("mode", {}),
        lambda p: p["config"].__setitem__(
            "missingness_policy", []),
        lambda p: p["config"].__setitem__("fold_seed_policy", {}),
        lambda p: p["seed_coverage"].__setitem__("11", []),
        lambda p: p["nulls"]["shuffled"].__setitem__("status", []),
        lambda p: p["nulls"]["shuffled"].__setitem__(
            "statistic", {"k": 1}),
    ], ids=["status-list", "status-dict", "status-dictv",
            "cfg-mode-list", "cfg-mode-dict", "cfg-misspol-list",
            "cfg-foldpol-dict", "coverage-val-list",
            "null-status-list", "null-statistic-dict"])
    def test_unhashable_values_reject_not_crash(self, mut):
        # Valid JSON types that are unhashable at Python set
        # membership — previously raw TypeError at the floor.
        payload = _canonical_payload()
        mut(payload)
        _rehash(payload)
        problems = validate_producer_payload(payload)
        assert problems, "unhashable payload accepted"
        # the exception boundary or a site guard — either is a
        # structured SCHEMA finding
        assert any("SCHEMA_MALFORMED" in pr for pr in problems)

    @pytest.mark.parametrize("extra", [
        {"evil": 1},
        {"source_files": [{"relpath": "x", "sha256": "a" * 64}]},
        {"evidence_root": "/tmp"},
        {"source_id": "real-src", "lineage": "x"},
    ], ids=["evil", "source_files", "evidence_root",
            "source_id+lineage"])
    def test_fixture_manifest_extra_fields_reject(self, extra):
        # R10.1-B: the synthetic bypass is exactly
        # {"fixture": true} — evidence-shaped fields smuggled past
        # byte verification must reject even after honest rehash.
        payload = _canonical_payload()
        payload["source_manifest"] = {"fixture": True, **extra}
        payload["config"]["source_manifest"] = dict(
            payload["source_manifest"])
        _rehash(payload)
        problems = validate_producer_payload(payload)
        assert any("fixture" in pr.lower() or
                   "source_manifest" in pr.lower()
                   for pr in problems), problems

    def test_fixture_manifest_exact_surface_clean(self):
        payload = _canonical_payload()
        payload["source_manifest"] = {"fixture": True}
        payload["config"]["source_manifest"] = {"fixture": True}
        _rehash(payload)
        assert validate_producer_payload(payload) == []
