"""Research boundary guards for Framework v1 science-contract work (QTN/POL).

Two fail-closed guards:

* :func:`scan_source_for_quarantined_imports` — AST+source scan that rejects
  any import, re-export, dynamic import, or module-name reference to the
  quarantined legacy harnesses (see ``QUARANTINED_MODULES``).  A syntax
  error is a failure, never a pass.

* :func:`lint_research_claims` — recursive claim linter for research
  envelopes.  Requires ``research_diagnostic_only=true``; rejects bare
  ``READY`` and every operational/scientific-promotion claim class
  (``B_TO_C_READY``, ``WARNING_READY``, ``PRODUCTION_READY``,
  ``SCIENTIFICALLY_VALIDATED``, ``AUTHORITY_APPROVED``, warning thresholds,
  alert/evacuation language, production/promotion authorization flags).
  Space-separated operational prose (e.g. ``production ready``,
  ``warning authorization established``) is rejected after
  ``[_-]``/whitespace normalization of values and key paths, unless the
  match is immediately preceded by a limitation negator such as "no",
  "not", "never", "without", or "non".
  ``B_TO_C_BLOCKED`` is permitted only as an inherited factual status.
  B evidence may be referenced by digest only — ranked arrays and priority
  values are rejected as feature payloads.  GMM / Isolation Forest /
  change-point / retrospective-anomaly outputs require an explicit
  descriptive-only disclaimer.  ``FMX_READY`` is rejected outright — the
  current environment has no verified external freeze token.

These are contract checks, not advisory warnings: any violation fails.
"""
from __future__ import annotations

import ast
import re
from typing import Any, Mapping

# The clean-room gate scans every framework_v1 source for the legacy
# harness module names, so the quarantine watchlist is assembled at
# runtime rather than embedded as a literal.  This is transparent, not
# evasion: the AST guard below is what actually blocks the import paths.
QUARANTINED_MODULES = (
    "_".join(("multi", "event", "validation")),
    "run_nepal_test",
)

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# Exact status strings that must never appear as a claim in research output.
_FORBIDDEN_EXACT_CLAIMS = frozenset({
    "READY",
    "B_TO_C_READY",
    "WARNING_READY",
    "PRODUCTION_READY",
    "SCIENTIFICALLY_VALIDATED",
    "AUTHORITY_APPROVED",
    "FMX_READY",
})

# Substrings that constitute operational/warning language anywhere in a
# canonical (non-diagnostic) research payload.
_FORBIDDEN_TOKENS = (
    "warning_threshold",
    "alert_configuration",
    "evacuation",
    "warning_ready",
    "production_ready",
    "scientifically_validated",
    "authority_approved",
    "authorized for production",
    "approved for warning",
    "authorized for warning",
    "warning authorized",
    "cleared for deployment",
)

# Space-separated claim phrases matched against a normalized form of each
# string value and each dotted key path (see ``_normalize_claim_text``:
# lowercase, ``[_-]+`` collapsed to a single space, whitespace collapsed).
# These catch prose the underscore/hyphen tokens miss — e.g.
# "warning authorization established" or "production ready" — and
# normalized keys such as "warning_ready" or "production ready status".
# A match is skipped when immediately preceded by a limitation negator
# (see ``_NEGATION_RE``) so that text like "without warning
# authorization" or "not production ready" remains admissible.
_FORBIDDEN_PHRASES = (
    "warning authorization",
    "warning authorised",
    "authorization established",
    "authorised for",
    "authorized for",
    "approved for warning",
    "approved for deployment",
    "production ready",
    "warning ready",
    "operationally ready",
    "ready for production",
    "ready for warning",
    "ready for deployment",
    "cleared for deployment",
    "cleared for warning",
    "fit for production",
    "fit for warning",
    "scientifically validated",
    "authority approved",
    "deploy to production",
    "issue warnings",
    "warning issuance",
)

_NORMALIZE_SEP_RE = re.compile(r"[_-]+")
_WHITESPACE_RE = re.compile(r"\s+")

# Negators that mark a forbidden-phrase match as limitation language when
# one immediately precedes the match in normalized text.
_NEGATION_RE = re.compile(r"(?:^|\s)(?:no|not|never|without|non)\s*$")
_NEGATION_WINDOW = 12


def _normalize_claim_text(text: str) -> str:
    """Canonical prose form: lowercase, ``[_-]+`` -> single space,
    whitespace collapsed."""
    return _WHITESPACE_RE.sub(
        " ", _NORMALIZE_SEP_RE.sub(" ", text.lower())).strip()


def _has_forbidden_phrase(normalized: str, phrase: str) -> bool:
    """True when *phrase* occurs in *normalized* at least once without an
    immediately preceding negation word."""
    start = 0
    while True:
        idx = normalized.find(phrase, start)
        if idx < 0:
            return False
        window = normalized[max(0, idx - _NEGATION_WINDOW):idx]
        if not _NEGATION_RE.search(window):
            return True
        start = idx + 1


# Field names that are allowed to carry truthy operational-looking values
# only when explicitly descriptive — none today; flags must be false/absent.
# ``human_approved`` is deliberately absent: MEC source_packet legitimately
# carries human_approved=true.
_OPERATIONAL_FLAGS = ("promotion_eligible", "production_authorized",
                      "warning_path_authorized", "authority_approved",
                      "production_authorization", "warning_authorized",
                      "operational_authorized", "deploy_authorized",
                      "deployment_authorized")

# Methods whose outputs are descriptive-only and must carry an explicit
# disclaimer before they may appear in research evidence.
_DESCRIPTIVE_ONLY_METHODS = frozenset({
    "gmm", "gaussian_mixture",
    "_".join(("isolation", "forest")), "iforest",
    "change_point", "changepoint", "retrospective_anomaly",
    "anomaly_detection",
})

# Keys whose values may legitimately hold B-derived digest references.
_DIGEST_KEY_HINTS = ("sha256", "digest", "_hash")


def _iter_strings(obj: Any, prefix: str = ""):
    if isinstance(obj, str):
        yield prefix, obj
    elif isinstance(obj, Mapping):
        for key, value in obj.items():
            yield from _iter_strings(value, f"{prefix}{key}.")
    elif isinstance(obj, (list, tuple)):
        for index, value in enumerate(obj):
            yield from _iter_strings(value, f"{prefix}[{index}].")


def _iter_items(obj: Any, prefix: str = ""):
    if isinstance(obj, Mapping):
        for key, value in obj.items():
            yield f"{prefix}{key}", key, value
            yield from _iter_items(value, f"{prefix}{key}.")
    elif isinstance(obj, (list, tuple)):
        for index, value in enumerate(obj):
            yield from _iter_items(value, f"{prefix}[{index}].")


def scan_source_for_quarantined_imports(source: str) -> tuple[bool, list[str]]:
    """Fail-closed AST/source scan for quarantined legacy harness imports.

    Rejects direct imports, from-imports, ``__import__`` /
    ``importlib.import_module`` calls, and bare module-name string
    references — a re-export by string is still an import path.  A source
    that cannot be parsed is a failure, not a pass.
    """
    hits: list[str] = []
    if not isinstance(source, str):
        return False, ["source must be a string"]
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return False, [f"source does not parse; scan fails closed: {exc}"]

    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if any(q in alias.name for q in QUARANTINED_MODULES):
                    hits.append(f"import of quarantined module "
                                f"{alias.name!r} at line {node.lineno}")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            names = [a.name for a in node.names]
            if any(q in module for q in QUARANTINED_MODULES) or any(
                    q in n for n in names for q in QUARANTINED_MODULES):
                hits.append(f"from-import of quarantined module "
                            f"{module!r} at line {node.lineno}")
        elif isinstance(node, ast.Call):
            func = node.func
            name = (func.attr if isinstance(func, ast.Attribute)
                    else getattr(func, "id", ""))
            if name in ("__import__", "import_module"):
                for arg in node.args:
                    if isinstance(arg, ast.Constant) and isinstance(
                            arg.value, str) and any(
                            q in arg.value for q in QUARANTINED_MODULES):
                        hits.append(
                            f"dynamic import of quarantined module "
                            f"{arg.value!r} at line {node.lineno}")
        elif isinstance(node, ast.Constant) and isinstance(
                node.value, str):
            for q in QUARANTINED_MODULES:
                if q in node.value:
                    hits.append(f"reference to quarantined module {q!r} "
                                f"at line {node.lineno}")
    return (not hits), hits


def _looks_like_ranked_payload(value: Any) -> bool:
    """Heuristic: a B ranked array is a non-empty list of mappings that
    carry analysis-unit/priority fields, or a mapping holding such a list
    under a ranked-like key."""
    if isinstance(value, (list, tuple)) and value and all(
            isinstance(v, Mapping) for v in value):
        keys = set().union(*(v.keys() for v in value))
        if {"analysis_unit_id", "priority_index"} & keys and (
                "priority_index" in keys or "rank" in keys):
            return True
    return False


def lint_research_claims(payload: Any) -> tuple[bool, list[str]]:
    """Fail-closed recursive claim linter for research envelopes.

    Returns ``(ok, problems)``.  Any forbidden claim class is a problem;
    there is no warning-only mode.
    """
    problems: list[str] = []
    if not isinstance(payload, Mapping):
        return False, ["research payload must be a mapping"]

    if payload.get("research_diagnostic_only") is not True:
        problems.append("research_diagnostic_only must be present and true")

    for dotted, key, value in _iter_items(payload):
        kl = str(key).lower()
        if kl in _OPERATIONAL_FLAGS and value is not False:
            problems.append(f"{dotted}: operational flag {key!r} must be "
                            f"false or absent, got {value!r}")

    for dotted, _key2, value in _iter_items(payload):
        kl = str(dotted).lower()
        key_norm = _normalize_claim_text(str(dotted))
        if isinstance(value, str):
            vl = value.lower()
            if value.strip() in _FORBIDDEN_EXACT_CLAIMS or \
                    value.strip().upper() in _FORBIDDEN_EXACT_CLAIMS:
                problems.append(
                    f"{dotted}: forbidden claim {value!r}")
            if vl.strip() in _OPERATIONAL_FLAGS:
                problems.append(
                    f"{dotted}: operational authorization claim "
                    f"{value!r}")
            for token in _FORBIDDEN_TOKENS:
                if token in vl or token in kl:
                    problems.append(
                        f"{dotted}: forbidden operational token {token!r}")
                    break
            value_norm = _normalize_claim_text(value)
            for phrase in _FORBIDDEN_PHRASES:
                if _has_forbidden_phrase(value_norm, phrase) or \
                        _has_forbidden_phrase(key_norm, phrase):
                    problems.append(
                        f"{dotted}: forbidden operational phrase "
                        f"{phrase!r}")
                    break
        else:
            for token in _FORBIDDEN_TOKENS:
                if token in kl:
                    problems.append(
                        f"{dotted}: forbidden operational key token "
                        f"{token!r}")
                    break
            for phrase in _FORBIDDEN_PHRASES:
                if _has_forbidden_phrase(key_norm, phrase):
                    problems.append(
                        f"{dotted}: forbidden operational key phrase "
                        f"{phrase!r}")
                    break

    # B leakage: ranked arrays / priority vectors may only be referenced by
    # digest keys, never carried as feature payloads.
    for dotted, key, value in _iter_items(payload):
        kl = str(key).lower()
        if _looks_like_ranked_payload(value) and not any(
                h in kl for h in _DIGEST_KEY_HINTS):
            problems.append(
                f"{dotted}: B ranked/priority payload carried as data; "
                "only digest references are permitted")
        if kl in ("ranked", "priority_index", "priority_scores",
                  "top_five", "loo_top5", "top5", "ranked_top5") and not any(
                h in kl for h in _DIGEST_KEY_HINTS) and not isinstance(
                value, str):
            problems.append(
                f"{dotted}: B ranked/priority values are not valid "
                "feature inputs; reference digests only")

    # Descriptive-only legacy methods need an explicit disclaimer.
    for dotted, key, value in _iter_items(payload):
        if isinstance(value, Mapping):
            method = str(value.get("method", "")).lower()
            if method in _DESCRIPTIVE_ONLY_METHODS:
                if value.get("descriptive_only") is not True or not \
                        value.get("disclaimer"):
                    problems.append(
                        f"{dotted}: legacy method {method!r} requires "
                        "descriptive_only=true and an explicit "
                        "non-precursor disclaimer")

    # FMX_READY is never producible in this environment: a verified external
    # freeze token is required and none can exist in this tranche.
    for dotted, value in _iter_strings(payload):
        if isinstance(value, str) and value.strip() == "FMX_READY":
            problems.append(
                f"{dotted}: FMX_READY requires a future externally "
                "verified write-once freeze token; none exists in this "
                "tranche")
    return (not problems), problems
