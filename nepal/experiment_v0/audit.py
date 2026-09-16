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
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

from nepal.research_v0._hashing import sha256_canonical
from nepal.research_v0.gates import scan_claims_text
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
    unit_basins = {str(k): str(v)
                   for k, v in (assoc.get("unit_basins") or {}).items()}
    region_basins = assoc.get("region_basins") or {}
    return (artifact, events, controls, unit_basins, holdout,
            region_basins, opportunities,
            int(assoc.get("n_boot", 200)),
            int(assoc.get("seed", 0)))


def audit_producer_payload(payload: Any) -> list[Finding]:
    """AUD-01 — producer-side audit over a serialized science_v0
    regime artifact payload.  Independent of the adapter: it checks
    that every regime-protocol gate is *present and closed* on the
    artifact itself, so a fabricated-but-internally-consistent
    payload still fails when protocol evidence is absent."""
    findings: list[Finding] = []
    if not isinstance(payload, Mapping):
        return [Finding("PRODUCER_PAYLOAD_MALFORMED",
                        "artifact_payload",
                        "producer payload is not a mapping")]
    required = ("assignments", "assignment_digest",
                "regime_artifact_digest", "freeze_digest", "frozen",
                "label_blinding", "fitted_on", "mode", "status",
                "data_class", "seeds", "feature_cols",
                "feature_matrix_digest", "input_bytes_digest",
                "config_digest", "fit_groups",
                "heldout_groups_declared", "train_mask_digest")
    for field in required:
        if field not in payload:
            findings.append(Finding(
                "PRODUCER_PROVENANCE_MISSING", "artifact_payload",
                f"producer payload lacks {field!r} — a regime "
                "artifact without complete provenance cannot "
                "support a terminal descriptive status"))
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
    axes = payload.get("axis_results") or payload.get("axes")
    if axes is not None and isinstance(axes, Mapping):
        failing = {k: v for k, v in axes.items()
                   if isinstance(v, Mapping) and
                   v.get("status") in ("FAIL", "SKIPPED",
                                       "NONCONVERGED")}
        if failing and payload.get("status") ==                 "DESCRIPTIVE_REGIME_ONLY":
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
         region_basins, opportunities, n_boot, seed) = \
            _reconstruct_association(assoc)
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
        forward = run_association(
            artifact, events, controls, unit_basins, holdout=holdout,
            region_basins=region_basins, opportunities=opportunities,
            n_boot=n_boot, seed=seed)
        reversed_run = run_association(
            artifact, list(reversed(events)), controls, unit_basins,
            holdout=holdout, region_basins=region_basins,
            opportunities=opportunities, n_boot=n_boot, seed=seed)
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
        for f in audit_producer_payload(assoc.get("artifact_payload")):
            out["problems"].append(f"{f.code}: {f.detail}")
        try:
            (artifact, events, controls, unit_basins, holdout,
             region_basins, opportunities, n_boot, seed) = \
                _reconstruct_association(assoc)
            report = run_association(
                artifact, events, controls, unit_basins,
                holdout=holdout, region_basins=region_basins,
                opportunities=opportunities, n_boot=n_boot, seed=seed)
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
            report = evaluate(
                cases, holdout=holdout,
                baseline_probs=fc.get("baseline_probs") or {},
                admitted_vintages=admitted,
                n_opportunities=fc.get("n_opportunities", -1),
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
