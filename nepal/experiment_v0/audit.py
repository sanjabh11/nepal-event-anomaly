"""Independent leakage, timing, and reproducibility auditor (SWE-B4).

The audit surface for swarm B's ``experiment_v0`` modules:

* ``audit_module_source`` — an AST-level static scan of one module
  file.  It flags downstream fitting surfaces (``fit``/
  ``fit_transform``/``partial_fit`` outside the declared supervised
  baseline site), mutation attempts on frozen artifacts, nondeterminism
  (wall-clock reads, unseeded randomness, environment reads), network
  and subprocess surfaces, file access for review, dynamic execution,
  and forbidden claim phrasing (via ``gates.scan_claims_text``).
* ``audit_pipeline`` — structural checks over a serialized replay
  bundle: locked-test-region reuse, cascade-group split collisions,
  missing provenance digests, mutable output surfaces, and a
  label-ordering probe that reruns the association harness with a
  permuted label sequence.
* ``replay_bundle`` / ``replay_problems`` — deterministic replay of a
  serialized bundle through the real ``run_association`` and
  ``evaluate`` entry points.  Failures are collected into ``problems``
  instead of raising, so replaying a defective bundle yields findings.

The auditor is read-only by contract: it mutates nothing it inspects,
performs no network access, and reads only the module sources it is
explicitly asked to scan.
"""
from __future__ import annotations

import ast
import dataclasses
import inspect
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from nepal.research_v0._hashing import sha256_canonical

try:  # PROV-03: lands with the research_v0 byte-verification helper
    from nepal.research_v0._hashing import verify_source_evidence
except ImportError:  # pragma: no cover - contract pending
    verify_source_evidence = None
from nepal.research_v0.gates import (REQUIRED_REGIME_GATE_NAMES,
                                   scan_claims_text)
from nepal.research_v0.producer_validation import (
    PRODUCER_DIGEST_FIELDS, PRODUCER_REQUIRED_FIELDS,
    validate_producer_payload)
from nepal.research_v0.records import (ControlWindowV0, EventLabelV0,
                                     ForecastVintageV0, HoldoutPlanV0,
                                     ObservationOpportunityV0,
                                     deserialize_record)

from .adapters import regime_assignment_from_artifact
from .association import (AssociationReport, RegimeAssignmentArtifact,
                          run_association)
from .baselines import REQUIRED_BASELINE_NAMES
from .evaluation import EvaluationReport, ForecastCase, evaluate
from .vintages import VintageRequest, build_vintage, ledger_problems

REPLAY_SCHEMA = "experiment_v0.replay_bundle/v0"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


@dataclass(frozen=True)
class Finding:
    """One audit observation: a rule ``code``, the ``path`` it was
    found under (file, bundle section, or class), and a human-readable
    ``detail``."""

    code: str
    path: str
    detail: str


# ---------------------------------------------------------------------
# Static module scan (AST)
# ---------------------------------------------------------------------

_FIT_ATTRS = frozenset({"fit", "fit_transform", "partial_fit"})

#: The single declared supervised-fit site: ``baselines.py`` fits the
#: predeclared regularized baseline by contract.  Everywhere else —
#: above all ``association.py``, where regime fitting must be
#: impossible downstream — a ``.fit(``-family call is a violation.
_FIT_EXEMPT_MODULE = "baselines.py"

_MUTATOR_METHODS = frozenset({
    "append", "extend", "insert", "pop", "remove", "clear", "sort",
    "reverse", "update", "setdefault", "add", "discard"})

_MUTATION_OK_FUNCS = frozenset({"__init__", "__post_init__"})

_NETWORK_ROOTS = frozenset({
    "requests", "urllib", "urllib3", "socket", "cdsapi", "ecmwfapi",
    "openmeteo", "httpx", "aiohttp", "ftplib", "smtplib", "telnetlib",
    "paramiko", "boto3", "botocore", "s3fs", "gcsfs", "websocket",
    "websockets", "grpc"})

_FILE_DOTTED = frozenset({
    "open", "io.open", "os.listdir", "os.scandir", "os.walk",
    "glob.glob", "glob.iglob", "pathlib.Path.open"})
_FILE_ATTRS = frozenset({
    "read_text", "read_bytes", "write_text", "write_bytes", "open"})

_ENV_DOTTED = frozenset({
    "os.getenv", "os.environ.get", "os.environ.setdefault",
    "os.environ.pop"})

_EXEC_DOTTED = frozenset({
    "eval", "exec", "compile", "__import__", "importlib.import_module",
    "builtins.eval", "builtins.exec"})

_SUBPROCESS_DOTTED = frozenset({
    "os.system", "os.popen", "subprocess.run", "subprocess.call",
    "subprocess.Popen", "subprocess.check_output",
    "subprocess.check_call"})


def _resolve_dotted(node: ast.AST, aliases: Mapping[str, str]) -> str:
    """Dotted name of an attribute/name chain with import-alias
    resolution (``import numpy as np`` -> ``np`` resolves to
    ``numpy``; ``from time import time`` -> ``time()`` resolves to
    ``time.time``)."""
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
    parts.reverse()
    if parts and parts[0] in aliases:
        parts = aliases[parts[0]].split(".") + parts[1:]
    return ".".join(parts)


def _rooted_at_self(node: ast.AST) -> bool:
    while isinstance(node, ast.Attribute):
        node = node.value
    return isinstance(node, ast.Name) and node.id == "self"


class _SourceVisitor(ast.NodeVisitor):
    """Collects audit findings from a parsed module."""

    def __init__(self, path: str, aliases: Mapping[str, str],
                 fit_allowed: bool) -> None:
        self.path = path
        self.aliases = dict(aliases)
        self.fit_allowed = fit_allowed
        self.findings: list[Finding] = []
        self._funcs: list[str] = []
        # Stack of frozen-dataclass flags, one per enclosing class.
        self._frozen_class: list[bool] = []

    @property
    def _in_frozen_record(self) -> bool:
        """True when the current position sits inside a frozen
        dataclass body — ``self.<field>`` mutation is an artifact
        violation only there (helper objects may mutate freely)."""
        return bool(self._frozen_class) and self._frozen_class[-1]

    def _flag(self, code: str, node: ast.AST, detail: str) -> None:
        self.findings.append(Finding(
            code, self.path,
            f"line {getattr(node, 'lineno', '?')}: {detail}"))

    @property
    def _func(self) -> Optional[str]:
        return self._funcs[-1] if self._funcs else None

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # A frozen record may not reopen its own mutation surface.
        if self._in_frozen_record and \
                node.name in ("__setattr__", "__delattr__"):
            self._flag("MUTATION_ATTEMPT", node,
                       f"frozen dataclass defines {node.name} — a "
                       "frozen record's mutation surface must not be "
                       "re-opened")
        self._funcs.append(node.name)
        self.generic_visit(node)
        self._funcs.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        is_frozen_dataclass = False
        for dec in node.decorator_list:
            name = _resolve_dotted(dec, self.aliases) if \
                isinstance(dec, (ast.Name, ast.Attribute)) else \
                _resolve_dotted(dec.func, self.aliases) if \
                isinstance(dec, ast.Call) else ""
            if name in ("dataclass", "dataclasses.dataclass"):
                frozen = isinstance(dec, ast.Call) and any(
                    kw.arg == "frozen" and isinstance(kw.value,
                                                      ast.Constant)
                    and kw.value.value is True
                    for kw in dec.keywords)
                if not frozen:
                    self._flag("UNFROZEN_DATACLASS", node,
                               f"class {node.name!r} is a dataclass "
                               "without frozen=True — experiment "
                               "records must be immutable")
                is_frozen_dataclass = bool(frozen)
        self._frozen_class.append(is_frozen_dataclass)
        self.generic_visit(node)
        self._frozen_class.pop()

    def _check_mutation_call(self, node: ast.Call, dotted: str,
                             attr: Optional[str]) -> None:
        if dotted in ("setattr", "object.__setattr__",
                      "builtins.setattr"):
            post_init_self = (
                self._func == "__post_init__" and bool(node.args)
                and isinstance(node.args[0], ast.Name)
                and node.args[0].id == "self")
            if not post_init_self:
                self._flag("MUTATION_ATTEMPT", node,
                           f"{dotted}(...) outside the "
                           "__post_init__(self, ...) idiom — frozen "
                           "artifacts have no mutation surface")
        if self._in_frozen_record and attr in _MUTATOR_METHODS and \
                isinstance(node.func, ast.Attribute) and \
                _rooted_at_self(node.func.value):
            self._flag("MUTATION_ATTEMPT", node,
                           f"self.<field>.{attr}(...) mutates a "
                           "frozen-artifact field in place")

    def _check_nondeterminism(self, node: ast.Call, dotted: str,
                              attr: Optional[str]) -> None:
        parts = dotted.split(".")
        if dotted in ("time.time", "time.monotonic",
                      "time.perf_counter", "time.process_time",
                      "time.sleep", "os.times") or (
                len(parts) >= 2 and parts[-2] == "datetime"
                and parts[-1] in ("now", "utcnow", "today")) or (
                len(parts) >= 2 and parts[-2] == "date"
                and parts[-1] == "today"):
            self._flag("WALL_CLOCK", node,
                       f"{dotted}(...) reads the wall clock — "
                       "experiment code must be a pure function of "
                       "its arguments plus the declared seed")
        if dotted != "random.Random" and \
                (dotted.startswith("random.") or
                 dotted.startswith("numpy.random.") or
                 dotted in ("os.urandom", "secrets.token_bytes",
                            "secrets.token_hex", "secrets.randbelow",
                            "secrets.choice")):
            self._flag("UNSEEDED_RANDOM", node,
                       f"{dotted}(...) draws from a shared/system "
                       "randomness source — use random.Random(seed) "
                       "streams only")
        if dotted in _ENV_DOTTED:
            self._flag("ENV_ACCESS", node,
                       f"{dotted}(...) reads process environment — "
                       "credentials and host state are outside the "
                       "declared input boundary")
        if dotted in _SUBPROCESS_DOTTED:
            self._flag("SUBPROCESS_CALL", node,
                       f"{dotted}(...) spawns a subprocess — an "
                       "undeclared side channel")

    def _check_io(self, node: ast.Call, dotted: str,
                  attr: Optional[str]) -> None:
        if dotted in _FILE_DOTTED or attr in _FILE_ATTRS:
            self._flag("FILE_ACCESS_REVIEW", node,
                       f"{dotted or attr}(...) touches the filesystem "
                       "— review: experiment code may only read "
                       "inside declared roots")
        if dotted in _EXEC_DOTTED:
            self._flag("DYNAMIC_EXEC", node,
                       f"{dotted}(...) is dynamic execution — "
                       "experiment code must be statically auditable")

    def visit_Call(self, node: ast.Call) -> None:
        dotted = _resolve_dotted(node.func, self.aliases)
        attr = node.func.attr if isinstance(node.func, ast.Attribute) \
            else None
        if attr in _FIT_ATTRS and not self.fit_allowed:
            self._flag("FIT_CALL", node,
                       f".{attr}(...) is a fitting surface — model "
                       "fitting belongs to the declared baseline site "
                       "only; regime fitting is impossible downstream")
        self._check_mutation_call(node, dotted, attr)
        self._check_nondeterminism(node, dotted, attr)
        self._check_io(node, dotted, attr)
        self.generic_visit(node)

    def _check_self_write(self, node: ast.AST, targets: Any) -> None:
        if not self._in_frozen_record or \
                self._func in _MUTATION_OK_FUNCS:
            return
        for target in targets:
            if isinstance(target, ast.Attribute) and \
                    _rooted_at_self(target):
                self._flag("MUTATION_ATTEMPT", node,
                           "assignment to self.<field> outside "
                           "__init__/__post_init__ mutates a record")

    def visit_Assign(self, node: ast.Assign) -> None:
        self._check_self_write(node, node.targets)
        self.generic_visit(node)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        self._check_self_write(node, [node.target])
        self.generic_visit(node)

    def visit_AugAssign(self, node: ast.AugAssign) -> None:
        self._check_self_write(node, [node.target])
        self.generic_visit(node)

    def visit_Delete(self, node: ast.Delete) -> None:
        self._check_self_write(node, node.targets)
        self.generic_visit(node)


def _collect_aliases(tree: ast.AST) -> tuple[dict[str, str],
                                             list[Finding]]:
    """First pass: import aliases plus network-import findings."""
    aliases: dict[str, str] = {}
    findings: list[Finding] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                root = a.name.split(".")[0]
                if a.asname:
                    aliases[a.asname] = a.name
                else:
                    aliases[root] = root
                if root in _NETWORK_ROOTS:
                    findings.append(Finding(
                        "NETWORK_IMPORT", "<module>",
                        f"line {node.lineno}: import of "
                        f"{a.name!r} — experiment code performs no "
                        "network access"))
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for a in node.names:
                aliases[a.asname or a.name] = (
                    f"{module}.{a.name}".lstrip("."))
            if module.split(".")[0] in _NETWORK_ROOTS:
                findings.append(Finding(
                    "NETWORK_IMPORT", "<module>",
                    f"line {node.lineno}: from-import of "
                    f"{module!r} — experiment code performs no "
                    "network access"))
    return aliases, findings


def audit_module_source(path: Any) -> list[Finding]:
    """Statically audit one experiment_v0 module file.

    Flags fitting surfaces (``.fit``/``.fit_transform``/
    ``.partial_fit`` — forbidden outside the declared
    ``baselines.py`` supervised-baseline site), mutation attempts on
    frozen records, wall-clock and unseeded-randomness reads,
    network/subprocess/environment surfaces, file access (review
    severity), dynamic execution, non-frozen dataclasses, and
    forbidden claim phrasing in module text (``scan_claims_text``).
    """
    p = Path(path)
    findings: list[Finding] = []
    try:
        source = p.read_text(encoding="utf-8")
    except OSError as exc:
        return [Finding("UNREADABLE_MODULE", str(p),
                        f"cannot read module source: {exc}")]

    for hit in scan_claims_text(source):
        findings.append(Finding(
            "FORBIDDEN_CLAIM_TEXT", str(p),
            f"claim scan hit: {hit} — module text must carry no "
            "authority-shaped phrasing"))

    try:
        tree = ast.parse(source, filename=str(p))
    except SyntaxError as exc:
        findings.append(Finding("SYNTAX_ERROR", str(p),
                                f"module does not parse: {exc}"))
        return findings

    aliases, net_findings = _collect_aliases(tree)
    findings.extend(
        Finding(f.code, str(p), f.detail) for f in net_findings)
    visitor = _SourceVisitor(
        str(p), aliases, fit_allowed=(p.name == _FIT_EXEMPT_MODULE))
    visitor.visit(tree)
    findings.extend(visitor.findings)
    return findings


# ---------------------------------------------------------------------
# Serialized-bundle helpers
# ---------------------------------------------------------------------

def replay_vintage_id(req: VintageRequest) -> str:
    """The deterministic vintage_id a replay binds to a request —
    ``"vintage-" + sha256(request)[:16]``; identical requests share
    one admitted vintage."""
    return "vintage-" + sha256_canonical(req.to_dict())[:16]


def _as_holdout(payload: Any, section: str) -> HoldoutPlanV0:
    record = deserialize_record(payload)
    if type(record) is not HoldoutPlanV0:
        raise ValueError(
            f"{section}.holdout deserialized to "
            f"{type(record).__name__!r}, not HoldoutPlanV0")
    return record


def _recomputed_regime_digest(payload: Mapping[str, Any]) -> str:
    """The regime digest bound downstream: ``sha256_canonical``
    over the serialized payload minus the freeze-local fields —
    the same surface ``freeze_regime_artifact`` digests."""
    return sha256_canonical(
        {k: v for k, v in payload.items()
         if k not in ("freeze_digest", "frozen")})


def _verify_regime_digest_references(
        assoc: Mapping[str, Any], payload: Mapping[str, Any],
        artifact: Any) -> None:
    """REP-C01 — the bundle-level regime digest must recompute from
    the serialized ``artifact_payload`` and equal every reference
    the bundle carries.  A carried digest or artifact_id is
    verified, never trusted: editing the payload, the assignment
    sidecar, the source digests, or a bundle-level reference must
    surface here."""
    recomputed = _recomputed_regime_digest(payload)
    if artifact.regime_digest != recomputed:
        raise ValueError(
            "reconstructed artifact regime_digest does not equal "
            "the digest recomputed from artifact_payload — the "
            "contract artifact and the serialized payload "
            "disagree")
    references: list[tuple[str, Any]] = []
    carried = assoc.get("artifact")
    if carried is not None:
        if not isinstance(carried, Mapping):
            raise ValueError(
                "association.artifact is present but not a "
                "mapping — the serialized contract artifact "
                "cannot be verified")
        references.append(("association.artifact.regime_digest",
                           carried.get("regime_digest")))
        carried_id = carried.get("artifact_id")
        if carried_id is not None and \
                str(carried_id) != artifact.artifact_id:
            raise ValueError(
                f"association.artifact.artifact_id "
                f"{carried_id!r} does not match the declared "
                f"artifact_id {artifact.artifact_id!r} — the "
                "bundle binds two different artifacts")
    for field in ("regime_digest", "artifact_regime_digest"):
        if field in assoc:
            references.append((f"association.{field}",
                               assoc[field]))
    for name, ref in references:
        if ref != recomputed:
            raise ValueError(
                f"{name} {ref!r} does not equal the regime "
                "digest recomputed from the serialized "
                "artifact_payload — a carried reference was "
                "tampered with or mislabeled")


def _reconstruct_association(assoc: Mapping[str, Any]):
    """Rebuild the association lane inputs from a bundle section.

    The regime artifact MUST arrive as the serialized frozen producer
    payload (``artifact_payload``) and is reconstructed through the
    canonical ``regime_assignment_from_artifact`` adapter — the
    adapter's digest recomputation, provenance floor, and freeze
    checks are part of the replay boundary.  A bare contract dict
    bypasses all of that and is rejected."""
    payload = assoc.get("artifact_payload")
    if payload is None:
        raise ValueError(
            "association section carries no artifact_payload — "
            "replay must reconstruct the regime artifact through the "
            "canonical producer adapter, not from a bare contract "
            "dict")
    artifact = regime_assignment_from_artifact(
        payload, artifact_id=str(
            assoc.get("artifact_id") or "replay-artifact"))
    _verify_regime_digest_references(assoc, payload, artifact)
    events = [deserialize_record(e) for e in assoc.get("events") or []]
    for e in events:
        if type(e) is not EventLabelV0:
            raise ValueError(
                f"association event deserialized to "
                f"{type(e).__name__!r}, not EventLabelV0")
    controls = [deserialize_record(c)
                for c in assoc.get("controls") or []]
    for c in controls:
        if type(c) is not ControlWindowV0:
            raise ValueError(
                f"association control deserialized to "
                f"{type(c).__name__!r}, not ControlWindowV0")
    opportunities: dict[str, ObservationOpportunityV0] = {}
    for oid, op in (assoc.get("opportunities") or {}).items():
        rec = deserialize_record(op)
        if type(rec) is not ObservationOpportunityV0:
            raise ValueError(
                f"association opportunity deserialized to "
                f"{type(rec).__name__!r}, not ObservationOpportunityV0")
        if rec.opportunity_id != str(oid):
            raise ValueError(
                f"opportunity registry key {oid!r} does not match "
                f"record id {rec.opportunity_id!r}")
        opportunities[str(oid)] = rec
    holdout = _as_holdout(assoc["holdout"], "association")
    # R10-P06: the serialized unit->basin map is validated, never
    # str()-coerced — a null/blank basin must reject here instead
    # of replaying as the string "None".
    unit_basins: dict[str, str] = {}
    for k, v in (assoc.get("unit_basins") or {}).items():
        if not isinstance(k, str) or not isinstance(v, str) or \
                not k.strip() or not v.strip():
            raise ValueError(
                "association unit_basins must map non-empty "
                "string unit ids to non-empty string basins — "
                f"got {k!r}: {v!r}")
        unit_basins[k] = v
    region_basins = assoc.get("region_basins") or {}
    return (artifact, events, controls, unit_basins, holdout,
            region_basins, opportunities,
            int(assoc.get("n_boot", 200)),
            int(assoc.get("seed", 0)), payload)


def _call_run_association(artifact, events, controls, unit_basins, *,
                          holdout, region_basins, opportunities,
                          n_boot, seed, producer_payload=None):
    """Invoke ``run_association``, forwarding the serialized producer
    payload when its signature binds the ``producer_payload`` keyword
    (PROV-04 — replayed associations get the same verified binding
    the adapter stamped)."""
    kwargs: dict[str, Any] = {
        "holdout": holdout, "region_basins": region_basins,
        "opportunities": opportunities, "n_boot": n_boot,
        "seed": seed}
    if "producer_payload" in inspect.signature(
            run_association).parameters:
        kwargs["producer_payload"] = producer_payload
    return run_association(artifact, events, controls, unit_basins,
                           **kwargs)


#: The canonical producer schema — every field a serialized
#: ``science_v0.run_regimes`` + ``freeze_regime_artifact`` payload
#: must carry.  Missing fields are provenance gaps; a fabricated
#: artifact that omits them cannot support a terminal status.
#: R8-C01: the tuples live in ``research_v0.producer_validation`` —
#: the shared validator freeze and the adapter also run — and are
#: aliased here so the audit's own richer checks and every existing
#: import keep working.
_PRODUCER_REQUIRED_FIELDS = PRODUCER_REQUIRED_FIELDS
_PRODUCER_DIGEST_FIELDS = PRODUCER_DIGEST_FIELDS

#: Mapping from the shared validator's problem-string tag to this
#: auditor's Finding codes — one Finding per problem, additive to
#: the richer audit checks below.
_SHARED_PROBLEM_CODES = {
    "PAYLOAD_MALFORMED": "PRODUCER_PAYLOAD_MALFORMED",
    "PROVENANCE_MISSING": "PRODUCER_PROVENANCE_MISSING",
    "SCHEMA_MALFORMED": "PRODUCER_SCHEMA_MALFORMED",
    "DIGEST_MALFORMED": "PRODUCER_DIGEST_MALFORMED",
    "DIGEST_MISMATCH": "PRODUCER_DIGEST_MISMATCH",
    "STATUS_ERROR": "PRODUCER_STATUS_ERROR"}

_SEED_COVERAGE_STATES = frozenset({"converged", "failed"})


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and bool(_SHA256_RE.match(value))


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and \
        isinstance(value, (int, float))


def _seq_of_numbers(value: Any) -> bool:
    return isinstance(value, (list, tuple)) and bool(value) and \
        all(_is_number(v) for v in value)


def _producer_model_findings(payload: Mapping[str, Any],
                             ) -> list[Finding]:
    """Well-formedness of the ``model`` binding — the serialized
    GMM parameters that let a replay reconstruct the partition
    without re-predicting."""
    findings: list[Finding] = []
    model = payload.get("model")
    if not isinstance(model, Mapping) or \
            set(model) != {"weights", "means", "covariances"}:
        return [Finding(
            "PRODUCER_SCHEMA_MALFORMED", "artifact_payload.model",
            "model must be a mapping with exactly the keys "
            "{weights, means, covariances}")]
    weights, means, covs = (model["weights"], model["means"],
                            model["covariances"])
    if not _seq_of_numbers(weights):
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED", "artifact_payload.model",
            "model.weights must be a non-empty sequence of "
            "numbers"))
    if not isinstance(means, (list, tuple)) or not means or \
            any(not _seq_of_numbers(row) for row in means):
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED", "artifact_payload.model",
            "model.means must be a non-empty sequence of numeric "
            "rows"))
    if not isinstance(covs, (list, tuple)) or not covs or \
            any(not isinstance(m, (list, tuple)) or
                any(not _seq_of_numbers(row) for row in m)
                for m in covs):
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED", "artifact_payload.model",
            "model.covariances must be a non-empty sequence of "
            "numeric matrices"))
    if not findings and not (len(weights) == len(means) ==
                             len(covs)):
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED", "artifact_payload.model",
            "model weights/means/covariances component counts "
            "disagree"))
    k = payload.get("k")
    if not findings and isinstance(k, int) and \
            not isinstance(k, bool) and len(weights) != k:
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED", "artifact_payload.model",
            f"model carries {len(weights)} components but the "
            f"artifact declares k={k}"))
    # PROV-03: numeric sanity — a structurally-typed but pathological
    # model must fail closed.  Weights must be finite, non-negative,
    # and sum to 1; every covariance must be a square, finite,
    # symmetric, positive-semidefinite matrix consistent with the
    # feature dimension.
    import math as _m
    if not findings:
        try:
            _numeric_problems = _model_numeric_findings(
                weights, means, covs, _m)
        except (TypeError, ValueError, OverflowError):
            _numeric_problems = [Finding(
                "PRODUCER_MODEL_INVALID", "artifact_payload.model",
                "model weights/means/covariances contain values "
                "that cannot be represented as finite floats")]
        findings.extend(_numeric_problems)
    return findings


def _model_numeric_findings(weights, means, covs, _m) -> list:
    """Numeric sanity for a structurally-typed model — weights
    finite/non-negative/summing to 1; means finite and
    dimension-consistent; covariances square, finite, symmetric,
    positive-semidefinite.  Every float() coercion may raise
    TypeError/ValueError/OverflowError on pathological inputs — the
    caller maps that to one structured finding."""
    findings: list[Finding] = []
    if any(not _m.isfinite(float(w)) or float(w) < 0.0
           for w in weights):
        findings.append(Finding(
            "PRODUCER_MODEL_INVALID", "artifact_payload.model",
            "model.weights must be finite and non-negative"))
    elif abs(sum(float(w) for w in weights) - 1.0) > 1e-6:
        findings.append(Finding(
            "PRODUCER_MODEL_INVALID", "artifact_payload.model",
            "model.weights do not sum to 1"))
    d = len(means[0]) if means else 0
    if any(len(row) != d for row in means):
        findings.append(Finding(
            "PRODUCER_MODEL_INVALID", "artifact_payload.model",
            "model.means rows have inconsistent feature "
            "dimensions"))
    if any(not _m.isfinite(float(v)) for row in means
           for v in row):
        findings.append(Finding(
            "PRODUCER_MODEL_INVALID", "artifact_payload.model",
            "model.means contain non-finite values"))
    for ci, cov in enumerate(covs):
        if len(cov) != d or any(len(row) != d for row in cov):
            findings.append(Finding(
                "PRODUCER_MODEL_INVALID",
                "artifact_payload.model",
                f"model.covariances[{ci}] is not a {d}x{d} "
                "square matrix"))
            continue
        if any(not _m.isfinite(float(v)) for row in cov
               for v in row):
            findings.append(Finding(
                "PRODUCER_MODEL_INVALID",
                "artifact_payload.model",
                f"model.covariances[{ci}] contains non-finite "
                "values"))
            continue
        if any(abs(float(cov[i][j]) - float(cov[j][i])) > 1e-9
               for i in range(d) for j in range(d)):
            findings.append(Finding(
                "PRODUCER_MODEL_INVALID",
                "artifact_payload.model",
                f"model.covariances[{ci}] is not symmetric"))
            continue
        try:
            import numpy as _np
            w = _np.linalg.eigvalsh(
                _np.asarray(cov, dtype=float))
            if float(w.min()) < -1e-9:
                raise ValueError("not PSD")
        except Exception:
            findings.append(Finding(
                "PRODUCER_MODEL_INVALID",
                "artifact_payload.model",
                f"model.covariances[{ci}] is not "
                "positive-semidefinite"))
    return findings


def _producer_input_schema_findings(
        payload: Mapping[str, Any]) -> list[Finding]:
    """Well-formedness of ``input_schema`` — feature order, row
    count, dtypes, and frame shape bound against the artifact's own
    declared fields."""
    findings: list[Finding] = []
    schema = payload.get("input_schema")
    path = "artifact_payload.input_schema"
    if not isinstance(schema, Mapping):
        return [Finding("PRODUCER_SCHEMA_MALFORMED", path,
                        "input_schema must be a mapping with "
                        "feature_cols, n_rows, dtypes, and shape")]
    missing = sorted({"feature_cols", "n_rows", "dtypes", "shape"}
                     - set(schema))
    if missing:
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED", path,
            f"input_schema lacks {missing}"))
        return findings
    cols = schema["feature_cols"]
    if not isinstance(cols, (list, tuple)) or not cols or \
            any(not isinstance(c, str) or not c.strip()
                for c in cols):
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED", path,
            "input_schema.feature_cols must be a non-empty "
            "sequence of column names"))
    declared = payload.get("feature_cols")
    if isinstance(cols, (list, tuple)) and \
            isinstance(declared, (list, tuple)) and \
            list(cols) != list(declared):
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED", path,
            "input_schema.feature_cols does not equal the "
            "artifact's feature_cols — the feature order binding "
            "is inconsistent"))
    n_rows = schema["n_rows"]
    if isinstance(n_rows, bool) or not isinstance(n_rows, int) or \
            n_rows <= 0:
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED", path,
            "input_schema.n_rows must be a positive integer"))
    shape = schema["shape"]
    if not isinstance(shape, (list, tuple)) or len(shape) != 2 or \
            any(isinstance(d, bool) or not isinstance(d, int)
                or d <= 0 for d in shape):
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED", path,
            "input_schema.shape must be two positive integers"))
    else:
        if isinstance(cols, (list, tuple)) and \
                shape[1] != len(cols):
            findings.append(Finding(
                "PRODUCER_SCHEMA_MALFORMED", path,
                "input_schema.shape[1] != len(feature_cols)"))
        if isinstance(n_rows, int) and not isinstance(n_rows, bool) \
                and n_rows > 0 and shape[0] != n_rows:
            findings.append(Finding(
                "PRODUCER_SCHEMA_MALFORMED", path,
                "input_schema.shape[0] != input_schema.n_rows"))
    frame_rows = payload.get("n_rows")
    if isinstance(n_rows, int) and not isinstance(n_rows, bool) \
            and isinstance(frame_rows, int) and \
            not isinstance(frame_rows, bool) and \
            n_rows != frame_rows:
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED", path,
            "input_schema.n_rows != artifact n_rows — the input "
            "frame binding is inconsistent"))
    dtypes = schema["dtypes"]
    if isinstance(dtypes, Mapping):
        if isinstance(cols, (list, tuple)) and \
                set(map(str, dtypes)) != set(map(str, cols)):
            findings.append(Finding(
                "PRODUCER_SCHEMA_MALFORMED", path,
                "input_schema.dtypes keys do not cover exactly the "
                "declared feature columns"))
    elif not (isinstance(dtypes, (list, tuple)) and
              isinstance(cols, (list, tuple)) and
              len(dtypes) == len(cols) and
              all(isinstance(d, str) for d in dtypes)):
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED", path,
            "input_schema.dtypes must map every feature column to "
            "a dtype or list one dtype per column"))
    return findings


def _producer_seed_findings(payload: Mapping[str, Any]
                            ) -> list[Finding]:
    """Seed-declaration coverage: ``seed_coverage`` must key
    exactly the ``seeds_declared`` set, carry only the declared
    vocabulary, and a failed seed demotes the artifact."""
    findings: list[Finding] = []
    declared = payload.get("seeds_declared")
    declared_ok = isinstance(declared, (list, tuple)) and \
        bool(declared) and all(
            isinstance(s, int) and not isinstance(s, bool)
            for s in declared)
    if "seeds_declared" in payload and not declared_ok:
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED",
            "artifact_payload.seeds_declared",
            "seeds_declared must be a non-empty sequence of "
            "integers"))
    coverage = payload.get("seed_coverage")
    if "seed_coverage" not in payload:
        return findings
    if not isinstance(coverage, Mapping) or not coverage:
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED",
            "artifact_payload.seed_coverage",
            "seed_coverage must be a non-empty seed -> state map"))
        return findings
    bad_states = {str(k): v for k, v in coverage.items()
                  if v not in _SEED_COVERAGE_STATES}
    if bad_states:
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED",
            "artifact_payload.seed_coverage",
            f"seed_coverage states outside "
            f"{sorted(_SEED_COVERAGE_STATES)}: {bad_states}"))
    if declared_ok and \
            {str(k) for k in coverage} != \
            {str(s) for s in declared}:
        findings.append(Finding(
            "PRODUCER_SEED_COVERAGE_MISMATCH",
            "artifact_payload.seed_coverage",
            f"seed_coverage keys {sorted(map(str, coverage))} do "
            f"not equal seeds_declared "
            f"{sorted(str(s) for s in declared)} — every declared "
            "seed must be accounted for"))
    failed = sorted(str(k) for k, v in coverage.items()
                    if v == "failed")
    if failed:
        findings.append(Finding(
            "PRODUCER_SEED_FAILED", "artifact_payload.seed_coverage",
            f"declared seeds {failed} failed to converge — the "
            "artifact's seed evidence is incomplete and the "
            "terminal-status claim is demoted"))
    return findings


#: The declared producer gate universe lives in
#: ``nepal.research_v0.gates.REQUIRED_REGIME_GATE_NAMES`` so the
#: producer, freeze, adapter, replay, and this auditor enforce the
#: same set — every terminal artifact must carry exactly these
#: ``stability.required_gates`` keys.
_REQUIRED_GATE_NAMES = REQUIRED_REGIME_GATE_NAMES


def _producer_gate_findings(payload: Mapping[str, Any]
                            ) -> list[Finding]:
    """The flat ``stability.required_gates`` map must exist; under a
    terminal descriptive status every gate must be closed."""
    findings: list[Finding] = []
    stability = payload.get("stability")
    path = "artifact_payload.stability"
    if "stability" not in payload:
        return findings          # absence is a provenance finding
    if not isinstance(stability, Mapping):
        return [Finding("PRODUCER_SCHEMA_MALFORMED", path,
                        "stability must be a mapping carrying the "
                        "required_gates evidence")]
    gates = stability.get("required_gates")
    if not isinstance(gates, Mapping) or not gates:
        findings.append(Finding(
            "PRODUCER_PROVENANCE_MISSING", path,
            "stability.required_gates is missing or not a "
            "non-empty flat gate map — the artifact carries no "
            "gate evidence to audit"))
        gates = None
    else:
        # REG-14: every declared gate must be present — an omitted
        # gate is not a closed gate.
        missing_gates = sorted(
            _REQUIRED_GATE_NAMES - set(gates))
        if missing_gates:
            findings.append(Finding(
                "PRODUCER_PROVENANCE_MISSING", path,
                f"required_gates omits declared gates "
                f"{missing_gates} — an absent gate can never stand "
                "in for evidence"))
        extra_gates = sorted(set(gates) - _REQUIRED_GATE_NAMES)
        if extra_gates:
            findings.append(Finding(
                "PRODUCER_SCHEMA_MALFORMED", path,
                f"required_gates carries undeclared gates "
                f"{extra_gates} — the gate map is exactly the "
                "declared universe, nothing more"))
    if gates and any(not isinstance(v, bool) for v in gates.values()):
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED", path,
            "required_gates values must be booleans — the flat "
            "gate map admits no tri-state or numeric verdicts"))
    descriptive = payload.get("status") == "DESCRIPTIVE_REGIME_ONLY"
    if gates and descriptive:
        open_gates = sorted(str(g) for g, v in gates.items()
                            if v is not True)
        if open_gates:
            findings.append(Finding(
                "PRODUCER_GATE_BYPASSED", path,
                f"required_gates {open_gates} are open yet the "
                "artifact claims a terminal descriptive status — "
                "a frozen artifact cannot outrun its own gate "
                "evidence"))
    # the bound stability-report digest must recompute over the
    # carried stability block — a carried digest is verified,
    # never trusted
    report_digest = payload.get("stability_report_digest")
    if _is_sha256(report_digest):
        try:
            recomputed = sha256_canonical(stability)
        except (TypeError, ValueError) as exc:
            findings.append(Finding(
                "PRODUCER_PAYLOAD_MALFORMED", path,
                f"stability block is not canonically "
                f"serializable: {exc}"))
        else:
            if recomputed != report_digest:
                findings.append(Finding(
                    "PRODUCER_DIGEST_MISMATCH", path,
                    "stability_report_digest does not recompute "
                    "over the payload's stability block — the "
                    "gate evidence may have been rewritten "
                    "post-bind"))
    return findings


def _producer_null_findings(payload: Mapping[str, Any]
                            ) -> list[Finding]:
    """REG-05b — recompute the carried null-family digests over the
    serialized replicate records.

    Each null family record the producer serializes binds a
    ``family_digest`` = ``sha256_canonical`` over the documented
    dict ``{family, seed_cycle, n_replicates, statistic, p_value,
    observed, alpha, n_succeeded, n_failed, status, reason,
    selection, null_stat_min, null_stat_max, null_k_distribution,
    replicates}`` — recompute it over exactly what is carried (the
    generator is never re-run).  ``seed_cycle`` is the declared
    seed set (``seeds_declared``, falling back to ``seeds``).  When
    the nulls section also carries ``k1_bic`` plus a
    ``null_model_digest``, the envelope digest is recomputed the
    same way.  Family records without a ``family_digest`` carry no
    bound claim and are skipped."""
    findings: list[Finding] = []
    nulls = payload.get("nulls")
    if not isinstance(nulls, Mapping):
        return findings
    declared = payload.get("seeds_declared")
    if not isinstance(declared, (list, tuple)):
        declared = payload.get("seeds")
    seed_cycle = list(declared) \
        if isinstance(declared, (list, tuple)) else declared
    families: dict[str, Any] = {}
    for fam in sorted(nulls, key=str):
        rec = nulls[fam]
        if not isinstance(rec, Mapping) or \
                "family_digest" not in rec:
            continue
        families[str(fam)] = rec
        path = f"artifact_payload.nulls.{fam}"
        try:
            expected = sha256_canonical({
                "family": fam,
                "seed_cycle": seed_cycle,
                "n_replicates": rec["n_replicates"],
                "statistic": rec["statistic"],
                "p_value": rec["p_value"],
                # C06: the digest binds every declared record field
                "observed": rec.get("observed"),
                "alpha": rec.get("alpha"),
                "n_succeeded": rec.get("n_succeeded"),
                "n_failed": rec.get("n_failed"),
                "status": rec.get("status"),
                "reason": rec.get("reason"),
                "selection": rec.get("selection"),
                "null_stat_min": rec.get("null_stat_min"),
                "null_stat_max": rec.get("null_stat_max"),
                "null_k_distribution":
                    rec.get("null_k_distribution", {}),
                "replicates": rec.get("replicates", [])})
        except (KeyError, TypeError, ValueError) as exc:
            findings.append(Finding(
                "PRODUCER_PAYLOAD_MALFORMED", path,
                f"null family record cannot be canonically "
                f"rehashed: {exc} — a bound digest over an "
                "incomplete record is not evidence"))
            continue
        if rec["family_digest"] != expected:
            findings.append(Finding(
                "PRODUCER_DIGEST_MISMATCH", path,
                "family_digest does not recompute over the "
                "carried null-family record — the serialized "
                "replicate evidence was rewritten post-bind"))
    k1_bic = nulls.get("k1_bic")
    declared_nm = payload.get("null_model_digest")
    if isinstance(k1_bic, (list, tuple)) and \
            _is_sha256(declared_nm):
        try:
            expected_nm = sha256_canonical({
                "k1_bic": list(k1_bic),
                "null_families": {fam: rec.get("family_digest")
                                  for fam, rec in
                                  families.items()}})
        except (TypeError, ValueError) as exc:
            findings.append(Finding(
                "PRODUCER_PAYLOAD_MALFORMED",
                "artifact_payload.nulls",
                f"null_model_digest inputs cannot be canonically "
                f"rehashed: {exc}"))
        else:
            if expected_nm != declared_nm:
                findings.append(Finding(
                    "PRODUCER_DIGEST_MISMATCH",
                    "artifact_payload.null_model_digest",
                    "null_model_digest does not recompute over the "
                    "carried k1_bic list and family digests — the "
                    "null-model binding was rewritten post-bind"))
    return findings


def audit_producer_payload(payload: Any) -> list[Finding]:
    """AUD-01 — producer-side audit over a serialized science_v0
    regime artifact payload.  Independent of the adapter: it checks
    the canonical producer schema — every provenance and
    source/evidence binding present, every carried digest well
    formed, the flat required-gates map closed under a terminal
    descriptive status, and the declared seed set fully covered —
    so a fabricated-but-internally-consistent payload still fails
    when protocol evidence is absent."""
    findings: list[Finding] = []
    # R8-C01 / R9-P12: the shared producer provenance floor — the
    # same validator ``freeze_regime_artifact`` and the association
    # adapter run — on EVERY path, including non-mapping payloads.
    # Additive to the richer checks below; each problem maps to one
    # Finding by tag, and nothing downstream may loosen it.
    for _prob in validate_producer_payload(payload):
        _tag, _, _detail = _prob.partition(": ")
        findings.append(Finding(
            _SHARED_PROBLEM_CODES.get(_tag,
                                      "PRODUCER_SCHEMA_MALFORMED"),
            "artifact_payload", _detail or _prob))
    if not isinstance(payload, Mapping):
        return findings
    for field in _PRODUCER_REQUIRED_FIELDS:
        if field not in payload:
            findings.append(Finding(
                "PRODUCER_PROVENANCE_MISSING", "artifact_payload",
                f"producer payload lacks {field!r} — a regime "
                "artifact without complete provenance cannot "
                "support a terminal descriptive status"))
    for field in _PRODUCER_DIGEST_FIELDS:
        if field in payload and not _is_sha256(payload[field]):
            findings.append(Finding(
                "PRODUCER_DIGEST_MALFORMED", "artifact_payload",
                f"{field} is not a 64-hex sha256 — a carried "
                "digest that cannot recompute is not a binding"))
    for field in ("n_train_rows", "n_rows"):
        if field in payload and (
                isinstance(payload[field], bool) or
                not isinstance(payload[field], int) or
                payload[field] <= 0):
            findings.append(Finding(
                "PRODUCER_SCHEMA_MALFORMED", "artifact_payload",
                f"{field} must be a positive integer"))
    if all(isinstance(payload.get(f), int) and
           not isinstance(payload[f], bool) and payload[f] > 0
           for f in ("n_train_rows", "n_rows")) and \
            payload["n_train_rows"] > payload["n_rows"]:
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED", "artifact_payload",
            "n_train_rows exceeds n_rows — the mask accounting "
            "is inconsistent"))
    if payload.get("status") == "RUN_ERROR":
        findings.append(Finding(
            "PRODUCER_STATUS_ERROR", "artifact_payload",
            "RUN_ERROR artifact carried into a replay bundle"))
    if payload.get("status") == "UNSUPERVISED_STRUCTURE_NOT_STABLE":
        findings.append(Finding(
            "PRODUCER_STATUS_UNSTABLE", "artifact_payload",
            "producer declared the structure unstable — its sidecar "
            "must not be replayed into association"))
    if payload.get("frozen") is not True:
        findings.append(Finding(
            "PRODUCER_NOT_FROZEN", "artifact_payload",
            "artifact is not frozen"))
    if payload.get("label_blinding") is not True:
        findings.append(Finding(
            "PRODUCER_UNBLINDED", "artifact_payload",
            "label_blinding is not true"))
    seeds = payload.get("seeds")
    if isinstance(seeds, (list, tuple)) and len(seeds) < 3:
        findings.append(Finding(
            "PRODUCER_SEED_GATE", "artifact_payload",
            "fewer than three seeds — the seed-stability gate is "
            "unmet"))
    occupancy = payload.get("occupancy")
    k = payload.get("k")
    if isinstance(occupancy, (list, tuple)) and \
            isinstance(k, int) and not isinstance(k, bool) and \
            len(occupancy) != k:
        findings.append(Finding(
            "PRODUCER_SCHEMA_MALFORMED", "artifact_payload",
            f"occupancy carries {len(occupancy)} components but "
            f"the artifact declares k={k}"))
    if "model" in payload:
        findings.extend(_producer_model_findings(payload))
    if "input_schema" in payload:
        findings.extend(_producer_input_schema_findings(payload))
    findings.extend(_producer_seed_findings(payload))
    findings.extend(_producer_gate_findings(payload))
    findings.extend(_producer_null_findings(payload))
    # PROV-01: source manifest — fixture or fully-bound real source
    sm = payload.get("source_manifest")
    if sm is not None:
        if not isinstance(sm, Mapping):
            findings.append(Finding(
                "PRODUCER_SCHEMA_MALFORMED", "artifact_payload",
                "source_manifest must be a mapping"))
        elif not sm.get("fixture"):
            for key in ("source_id", "source_digests", "units",
                        "feature_allowlist", "lineage",
                        "evidence_root"):
                if not sm.get(key):
                    findings.append(Finding(
                        "PRODUCER_PROVENANCE_MISSING",
                        "artifact_payload",
                        f"non-fixture source_manifest lacks {key!r}"))
            if isinstance(sm.get("source_digests"), Sequence) and                     not isinstance(sm["source_digests"], str):
                for d in sm["source_digests"]:
                    if not _is_sha256(d):
                        findings.append(Finding(
                            "PRODUCER_DIGEST_MALFORMED",
                            "artifact_payload",
                            "source_manifest.source_digests entry is "
                            "not a 64-hex sha256"))
                        break
            # PROV-03: byte-verify the declared source evidence under
            # evidence_root — declared digests are verified against
            # real file bytes, never trusted.  Fixture manifests
            # carry no files and return no problems.
            if verify_source_evidence is None:
                findings.append(Finding(
                    "PRODUCER_PROVENANCE_MISSING",
                    "artifact_payload",
                    "source-evidence byte verification is "
                    "unavailable — a non-fixture source_manifest "
                    "cannot be byte-verified"))
            else:
                for prob in verify_source_evidence(sm):
                    findings.append(Finding(
                        "PRODUCER_EVIDENCE_UNVERIFIED",
                        "artifact_payload.source_manifest",
                        prob))
    # REG-13/PROV-02: terminal/associable consistency
    status = payload.get("status")
    if payload.get("associable") is True and             status != "DESCRIPTIVE_REGIME_ONLY":
        findings.append(Finding(
            "PRODUCER_GATE_BYPASSED", "artifact_payload",
            "associable=true on a non-descriptive status — "
            "CANDIDATE_ONLY and unstable artifacts may never "
            "associate"))
    if payload.get("terminal") is True and status in (
            "CANDIDATE_ONLY",):
        findings.append(Finding(
            "PRODUCER_GATE_BYPASSED", "artifact_payload",
            "CANDIDATE_ONLY is a demotion, never terminal"))
    axes = payload.get("axis_results") or payload.get("axes")
    if axes is not None and isinstance(axes, Mapping):
        failing = {k: v for k, v in axes.items()
                   if isinstance(v, Mapping) and
                   v.get("status") in ("FAIL", "SKIPPED",
                                       "NONCONVERGED")}
        if failing and payload.get("status") == \
                "DESCRIPTIVE_REGIME_ONLY":
            findings.append(Finding(
                "PRODUCER_GATE_BYPASSED", "artifact_payload",
                f"stability axes {sorted(failing)} failed/skipped "
                "yet the artifact claims a terminal descriptive "
                "status"))
    return findings


def _admit_vintages(requests: Sequence[Any]) -> list[ForecastVintageV0]:
    """Rebuild requests and run the real admission path; identical
    requests dedupe onto one admitted vintage."""
    admitted: dict[str, ForecastVintageV0] = {}
    for reqd in requests:
        req = VintageRequest.from_dict(reqd)
        vintage = build_vintage(req, replay_vintage_id(req))
        admitted.setdefault(sha256_canonical(vintage.to_dict()),
                            vintage)
    return list(admitted.values())


def _split_lookup(holdout: Mapping[str, Any]) -> dict[str, str]:
    out: dict[str, str] = {}
    for split, key in (("train", "train_groups"),
                       ("validation", "validation_groups"),
                       ("test", "test_groups")):
        for g in holdout.get(key) or ():
            out[str(g)] = split
    return out


# ---------------------------------------------------------------------
# Structural pipeline audit
# ---------------------------------------------------------------------

def _holdout_findings(holdout: Any, path: str) -> list[Finding]:
    findings: list[Finding] = []
    if not isinstance(holdout, Mapping):
        return [Finding("HOLDOUT_DEFECT", path,
                        "holdout section is missing or not a mapping")]
    if holdout.get("test_locked") is not True:
        findings.append(Finding(
            "HOLDOUT_UNLOCKED", path,
            "test_locked is not True — evaluation on an unlocked "
            "split is inadmissible"))
    test_groups = {str(g) for g in holdout.get("test_groups") or ()}
    regions = {str(r)
               for r in holdout.get("evaluation_region_names") or ()}
    unmapped = regions - test_groups
    if unmapped:
        findings.append(Finding(
            "TEST_REGION_REUSE", path,
            f"evaluation_region_names not drawn from locked test "
            f"groups: {sorted(unmapped)}"))
    return findings


def _region_findings(assoc: Mapping[str, Any],
                     fc: Mapping[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    # Forecast lane: every case region must be a locked test group.
    f_hold = fc.get("holdout") if isinstance(
        fc.get("holdout"), Mapping) else {}
    test_groups = {str(g) for g in f_hold.get("test_groups") or ()}
    for case in fc.get("cases") or []:
        if not isinstance(case, Mapping):
            continue
        region = case.get("region")
        if str(region) not in test_groups:
            findings.append(Finding(
                "TEST_REGION_REUSE", "forecast.cases",
                f"case {case.get('case_id')!r} region {region!r} is "
                f"outside holdout.test_groups {sorted(test_groups)} "
                "— evaluation cases must sit in locked test groups "
                "only"))
    # Association lane: an event's basin -> evaluation region must be
    # a locked test group.
    a_hold = assoc.get("holdout") if isinstance(
        assoc.get("holdout"), Mapping) else {}
    a_test = {str(g) for g in a_hold.get("test_groups") or ()}
    basin_region: dict[str, str] = {}
    rb = assoc.get("region_basins")
    if isinstance(rb, Mapping):
        for region, basins in rb.items():
            for b in basins or ():
                basin_region[str(b)] = str(region)
    for ev in assoc.get("events") or []:
        if not isinstance(ev, Mapping):
            continue
        basin = str(ev.get("basin_id") or "")
        region = basin_region.get(basin)
        if region is not None and region not in a_test:
            findings.append(Finding(
                "TEST_REGION_REUSE", "association.events",
                f"event {ev.get('event_id')!r} sits in basin "
                f"{basin!r} -> region {region!r}, which is not a "
                "locked test group"))
    return findings


def _cascade_findings(assoc: Mapping[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    a_hold = assoc.get("holdout") if isinstance(
        assoc.get("holdout"), Mapping) else {}
    split_of_group = _split_lookup(a_hold)
    assignments = a_hold.get("event_assignments") or {}
    by_cascade: dict[str, dict[str, Any]] = {}
    for ev in assoc.get("events") or []:
        if not isinstance(ev, Mapping):
            continue
        cascade = str(ev.get("cascade_group_id") or "")
        if not cascade:
            continue
        eid = ev.get("event_id")
        group = assignments.get(eid)
        split = split_of_group.get(str(group))
        entry = by_cascade.setdefault(
            cascade, {"splits": set(), "members": []})
        entry["splits"].add(split)
        entry["members"].append(eid)
    for cascade, entry in sorted(by_cascade.items()):
        known = {s for s in entry["splits"] if s is not None}
        if len(known) > 1:
            findings.append(Finding(
                "CASCADE_SPLIT_COLLISION", "association.events",
                f"cascade group {cascade!r} members "
                f"{sorted(entry['members'])} map to different splits "
                f"{sorted(known)} — cascades are atomic and may "
                "never span splits"))
    return findings


def _provenance_findings(assoc: Mapping[str, Any],
                         fc: Mapping[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    artifact = assoc.get("artifact") if isinstance(
        assoc.get("artifact"), Mapping) else {}
    if not str(artifact.get("artifact_id") or "").strip():
        findings.append(Finding(
            "MISSING_PROVENANCE", "association.artifact",
            "artifact_id is empty — an unnamed artifact has no "
            "provenance"))
    digest = str(artifact.get("regime_digest") or "")
    if not _SHA256_RE.match(digest):
        findings.append(Finding(
            "MISSING_PROVENANCE", "association.artifact",
            "regime_digest is missing or not a 64-hex sha256 — the "
            "frozen partition is not byte-bound"))
    else:
        # REP-C01: the carried reference must equal the digest
        # recomputed from the serialized artifact_payload — a
        # well-formed but wrong digest is tampering, not provenance.
        payload = assoc.get("artifact_payload")
        if isinstance(payload, Mapping):
            try:
                recomputed = _recomputed_regime_digest(payload)
            except (TypeError, ValueError) as exc:
                findings.append(Finding(
                    "PRODUCER_PAYLOAD_MALFORMED",
                    "association.artifact_payload",
                    f"artifact_payload is not canonically "
                    f"serializable: {exc}"))
            else:
                if digest != recomputed:
                    findings.append(Finding(
                        "REGIME_DIGEST_MISMATCH",
                        "association.artifact",
                        "regime_digest does not equal the digest "
                        "recomputed from the serialized "
                        "artifact_payload — a carried reference "
                        "is verified, never trusted"))
    admitted: dict[str, Any] = {}
    for i, reqd in enumerate(fc.get("vintage_requests") or []):
        req_path = f"forecast.vintage_requests[{i}]"
        if not isinstance(reqd, Mapping):
            findings.append(Finding(
                "MISSING_PROVENANCE", req_path,
                "vintage request is not a mapping"))
            continue
        for field_name in ("archive_payload_sha256",
                           "retrieval_record_sha256"):
            value = str(reqd.get(field_name) or "")
            if not _SHA256_RE.match(value):
                findings.append(Finding(
                    "MISSING_PROVENANCE", req_path,
                    f"{field_name} is missing or not a 64-hex sha256 "
                    "— vintage bytes and retrieval records must be "
                    "digest-bound"))
        for field_name in ("provider", "model_version", "license_id",
                           "archive_mechanism", "archive_payload_path",
                           "retrieval_record_path"):
            if not str(reqd.get(field_name) or "").strip():
                findings.append(Finding(
                    "MISSING_PROVENANCE", req_path,
                    f"{field_name} is empty — provider-declared "
                    "metadata is required for admission"))
        try:
            req = VintageRequest.from_dict(reqd)
            vintage = build_vintage(req, replay_vintage_id(req))
            admitted[sha256_canonical(vintage.to_dict())] = vintage
        except (ValueError, TypeError) as exc:
            findings.append(Finding(
                "VINTAGE_NOT_ADMISSIBLE", req_path,
                f"request fails admission: {exc}"))
    for case in fc.get("cases") or []:
        if not isinstance(case, Mapping):
            continue
        value = str(case.get("vintage_digest") or "")
        if not _SHA256_RE.match(value):
            findings.append(Finding(
                "MISSING_PROVENANCE", "forecast.cases",
                f"case {case.get('case_id')!r} vintage_digest is "
                "missing or not a 64-hex sha256"))
        elif value not in admitted:
            findings.append(Finding(
                "UNADMITTED_VINTAGE", "forecast.cases",
                f"case {case.get('case_id')!r} binds vintage_digest "
                f"{value[:16]}… which no admitted request produces"))
    return findings


def _baseline_findings(fc: Mapping[str, Any]) -> list[Finding]:
    findings: list[Finding] = []
    probs = fc.get("baseline_probs")
    if not isinstance(probs, Mapping):
        return [Finding("MISSING_BASELINE", "forecast.baseline_probs",
                        "baseline_probs is missing or not a mapping")]
    n_cases = len(fc.get("cases") or [])
    missing = sorted(REQUIRED_BASELINE_NAMES - set(map(str, probs)))
    if missing:
        findings.append(Finding(
            "MISSING_BASELINE", "forecast.baseline_probs",
            f"mandatory baselines absent: {missing} (required: "
            f"{sorted(REQUIRED_BASELINE_NAMES)})"))
    for name, vec in probs.items():
        try:
            length = len(list(vec))
        except TypeError:
            length = -1
        if length != n_cases:
            findings.append(Finding(
                "BASELINE_MISALIGNED", "forecast.baseline_probs",
                f"baseline {name!r} has {length} probabilities for "
                f"{n_cases} cases — vectors must be aligned"))
    return findings


#: Records the pipeline produces or consumes; outputs must be frozen.
_PIPELINE_CLASSES = (
    RegimeAssignmentArtifact, AssociationReport, EvaluationReport,
    ForecastCase, VintageRequest, EventLabelV0, ControlWindowV0,
    HoldoutPlanV0, ForecastVintageV0)

#: Report/artifact classes whose declared fields are scanned for
#: mutable container types.
_OUTPUT_CLASSES = (RegimeAssignmentArtifact, AssociationReport,
                   EvaluationReport)

_MUTABLE_ANNOTATION_RE = re.compile(
    r"\b(dict|list|set|Dict|List|Set|MutableMapping|MutableSequence|"
    r"MutableSet)\b")


def audit_output_class(cls: Any, path: Optional[str] = None
                       ) -> list[Finding]:
    """Immutability audit for one record/report class.

    A non-dataclass or non-frozen class is a ``MUTABLE_OUTPUT``
    finding; a frozen dataclass declaring ``dict``/``list``/``set``-
    typed fields gets a ``MUTABLE_FIELD`` review finding (frozen is
    shallow — the container can still be mutated in place).
    """
    where = path or f"{getattr(cls, '__module__', '?')}." \
                     f"{getattr(cls, '__qualname__', repr(cls))}"
    if not (isinstance(cls, type) and dataclasses.is_dataclass(cls)):
        return [Finding("MUTABLE_OUTPUT", where,
                        "pipeline output is not a dataclass — "
                        "records and reports must be frozen "
                        "dataclasses")]
    if not cls.__dataclass_params__.frozen:
        return [Finding("MUTABLE_OUTPUT", where,
                        f"{cls.__name__} is not frozen=True — "
                        "pipeline outputs must be immutable")]
    findings: list[Finding] = []
    mutable_fields = [f.name for f in dataclasses.fields(cls)
                      if _MUTABLE_ANNOTATION_RE.search(str(f.type))]
    if mutable_fields:
        findings.append(Finding(
            "MUTABLE_FIELD", where,
            f"review: {cls.__name__} is frozen but declares mutable "
            f"container fields {mutable_fields} — field contents can "
            "still be mutated in place"))
    return findings


def _immutability_findings() -> list[Finding]:
    findings: list[Finding] = []
    for cls in _PIPELINE_CLASSES:
        findings.extend(f for f in audit_output_class(cls)
                        if f.code == "MUTABLE_OUTPUT")
    for cls in _OUTPUT_CLASSES:
        findings.extend(f for f in audit_output_class(cls)
                        if f.code == "MUTABLE_FIELD")
    return findings


def _order_probe_findings(assoc: Mapping[str, Any]) -> list[Finding]:
    """Label-ordering leakage probe: the frozen artifact digest must
    not move under assignment-row permutation, and the association
    report digest must not move under event-order permutation."""
    findings: list[Finding] = []
    try:
        (artifact, events, controls, unit_basins, holdout,
         region_basins, opportunities, n_boot, seed,
         producer_payload) = _reconstruct_association(assoc)
    except Exception as exc:
        return [Finding("RECONSTRUCTION_DEFECT",
                        "association",
                        f"association section cannot be rebuilt: "
                        f"{exc}")]
    permuted_artifact = dataclasses.replace(
        artifact, assignments=tuple(reversed(artifact.assignments)))
    if sha256_canonical(permuted_artifact.to_dict()) != \
            sha256_canonical(artifact.to_dict()):
        findings.append(Finding(
            "ARTIFACT_ORDER_SENSITIVE", "association.artifact",
            "artifact digest changes under assignment-row "
            "permutation — the frozen partition must be "
            "order-canonical"))
    try:
        forward = _call_run_association(
            artifact, events, controls, unit_basins, holdout=holdout,
            region_basins=region_basins, opportunities=opportunities,
            n_boot=n_boot, seed=seed,
            producer_payload=producer_payload)
        reversed_run = _call_run_association(
            artifact, list(reversed(events)), controls, unit_basins,
            holdout=holdout, region_basins=region_basins,
            opportunities=opportunities, n_boot=n_boot, seed=seed,
            producer_payload=producer_payload)
    except Exception as exc:
        findings.append(Finding(
            "ASSOCIATION_REJECTED", "association",
            f"run_association raised during the ordering probe: "
            f"{exc}"))
        return findings
    if sha256_canonical(forward.to_dict()) != \
            sha256_canonical(reversed_run.to_dict()):
        findings.append(Finding(
            "LABEL_ORDER_SENSITIVE", "association",
            "association digest changes when the event-label order "
            "is permuted — downstream statistics must not depend on "
            "input ordering"))
    return findings


def audit_pipeline(bundle: Any) -> list[Finding]:
    """Structural audit of a serialized replay bundle.

    Checks the schema tag; holdout lock and region mapping on both
    lanes; case-region membership in locked test groups; cascade-group
    split atomicity; provenance digests on the artifact, vintage
    requests, and case bindings; the mandatory aligned baseline set;
    pipeline-class immutability; and label-ordering leakage via a
    live permutation probe.
    """
    at = "bundle"
    if not isinstance(bundle, Mapping):
        return [Finding("BUNDLE_TYPE", at,
                        "replay bundle must be a mapping")]
    findings: list[Finding] = []
    schema = bundle.get("schema")
    if schema != REPLAY_SCHEMA:
        findings.append(Finding(
            "SCHEMA_MISMATCH", at,
            f"schema {schema!r} is not {REPLAY_SCHEMA!r}"))
    assoc = bundle.get("association")
    assoc = assoc if isinstance(assoc, Mapping) else {}
    fc = bundle.get("forecast")
    fc = fc if isinstance(fc, Mapping) else {}
    findings.extend(_holdout_findings(
        assoc.get("holdout"), "association.holdout"))
    findings.extend(_holdout_findings(
        fc.get("holdout"), "forecast.holdout"))
    findings.extend(_region_findings(assoc, fc))
    findings.extend(_cascade_findings(assoc))
    findings.extend(_provenance_findings(assoc, fc))
    findings.extend(audit_producer_payload(
        assoc.get("artifact_payload")))
    findings.extend(_baseline_findings(fc))
    findings.extend(_immutability_findings())
    findings.extend(_order_probe_findings(assoc))
    return findings


# ---------------------------------------------------------------------
# Deterministic replay
# ---------------------------------------------------------------------

def replay_bundle(bundle: Mapping[str, Any]) -> dict[str, Any]:
    """Replay a serialized bundle through the real pipeline.

    Rebuilds records via ``deserialize_record`` /
    ``RegimeAssignmentArtifact.from_dict`` /
    ``VintageRequest.from_dict`` + ``build_vintage`` /
    ``ForecastCase.from_dict``, then calls ``run_association`` and
    ``evaluate``.  Returns a dict with per-lane ``status`` and
    canonical ``digest`` plus ``problems`` — every failure is
    collected, never raised, so a defective bundle yields findings.
    """
    out: dict[str, Any] = {
        "association_status": "ABSENT",
        "association_digest": "",
        "evaluation_status": "ABSENT",
        "evaluation_digest": "",
        "problems": [],
    }
    if not isinstance(bundle, Mapping):
        out["problems"].append("bundle is not a mapping")
        return out
    schema = bundle.get("schema")
    if schema != REPLAY_SCHEMA:
        out["problems"].append(
            f"schema {schema!r} is not {REPLAY_SCHEMA!r}")

    assoc = bundle.get("association")
    if isinstance(assoc, Mapping):
        for required in ("events", "controls", "unit_basins",
                         "opportunities", "holdout",
                         "artifact_payload"):
            if required not in assoc:
                out["problems"].append(
                    f"association section is missing {required!r} — "
                    "an absent section is not an empty one")
        producer_findings = audit_producer_payload(
            assoc.get("artifact_payload"))
        for f in producer_findings:
            out["problems"].append(f"{f.code}: {f.detail}")
        if producer_findings:
            # PROV-02b — a payload that fails the producer audit must
            # not replay into a supported-looking status: findings
            # block the association lane outright (the findings are
            # still recorded in problems above).
            out["association_status"] = "REPLAY_FAILED"
        else:
            try:
                (artifact, events, controls, unit_basins, holdout,
                 region_basins, opportunities, n_boot, seed,
                 producer_payload) = _reconstruct_association(assoc)
                report = _call_run_association(
                    artifact, events, controls, unit_basins,
                    holdout=holdout, region_basins=region_basins,
                    opportunities=opportunities, n_boot=n_boot,
                    seed=seed, producer_payload=producer_payload)
                out["association_status"] = report.status
                out["association_digest"] = sha256_canonical(
                    report.to_dict())
            except Exception as exc:
                out["association_status"] = "REPLAY_FAILED"
                out["problems"].append(f"association: {exc}")

    fc = bundle.get("forecast")
    if isinstance(fc, Mapping):
        try:
            vintages = _admit_vintages(
                fc.get("vintage_requests") or [])
            admitted = {sha256_canonical(v.to_dict()): v
                        for v in vintages}
            out["problems"].extend(
                f"vintage ledger: {p}"
                for p in ledger_problems(vintages))
            cases = [ForecastCase.from_dict(c)
                     for c in fc.get("cases") or []]
            holdout = _as_holdout(fc.get("holdout"), "forecast")
            # the forecast evaluation scope is registry-derived —
            # opportunities, unit->basin, and region->basin maps ride
            # in the bundle, never a caller-chosen integer
            f_opps = {}
            for oid, op in (fc.get("opportunities") or {}).items():
                rec = deserialize_record(op)
                if type(rec) is not ObservationOpportunityV0:
                    raise ValueError(
                        f"forecast opportunity deserialized to "
                        f"{type(rec).__name__!r}, not "
                        "ObservationOpportunityV0")
                if rec.opportunity_id != str(oid):
                    raise ValueError(
                        f"forecast opportunity registry key {oid!r} "
                        f"does not match record id "
                        f"{rec.opportunity_id!r}")
                f_opps[str(oid)] = rec
            # R10-P06: same non-coercion rule as the association
            # lane — blank/null basin values reject, they never
            # replay as coerced strings.
            f_unit_basins: dict[str, str] = {}
            for k, v in (fc.get("unit_basins") or {}).items():
                if not isinstance(k, str) or \
                        not isinstance(v, str) or \
                        not k.strip() or not v.strip():
                    raise ValueError(
                        "forecast unit_basins must map non-empty "
                        "string unit ids to non-empty string "
                        f"basins — got {k!r}: {v!r}")
                f_unit_basins[k] = v
            f_region_basins = fc.get("region_basins") or {}
            report = evaluate(
                cases, holdout=holdout,
                baseline_probs=fc.get("baseline_probs") or {},
                admitted_vintages=admitted,
                opportunities=f_opps,
                unit_basins=f_unit_basins,
                region_basins=f_region_basins,
                n_boot=int(fc.get("n_boot", 200)),
                seed=int(fc.get("seed", 0)))
            out["evaluation_status"] = report.status
            out["evaluation_digest"] = sha256_canonical(
                report.to_dict())
        except Exception as exc:
            out["evaluation_status"] = "REPLAY_FAILED"
            out["problems"].append(f"forecast: {exc}")
    return out


def replay_problems(bundle_a: Any, bundle_b: Any) -> list[str]:
    """Replay two serializations of the same bundle and report drift.

    Byte-for-byte replay requires identical result dicts; any
    difference in statuses, digests, or problem lists is returned as a
    drift description.
    """
    ra = replay_bundle(bundle_a)
    rb = replay_bundle(bundle_b)
    diffs: list[str] = []
    for key in ("association_status", "association_digest",
                "evaluation_status", "evaluation_digest"):
        if ra.get(key) != rb.get(key):
            diffs.append(
                f"{key} drifted: {ra.get(key)!r} != {rb.get(key)!r}")
    if ra.get("problems") != rb.get("problems"):
        diffs.append(
            f"problems drifted: {ra.get('problems')!r} != "
            f"{rb.get('problems')!r}")
    return diffs


__all__ = [
    "Finding",
    "REPLAY_SCHEMA",
    "audit_module_source",
    "audit_output_class",
    "audit_producer_payload",
    "audit_pipeline",
    "replay_bundle",
    "replay_problems",
    "replay_vintage_id",
]
