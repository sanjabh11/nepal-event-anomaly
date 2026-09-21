#!/usr/bin/env python3
"""P5 release-closure V4 post-publication validator (audit item A3-07).

The release-closure document (schema ``P5_RELEASE_CLOSURE_V4``) is the
detached record that binds the evidence index, the suite receipt, both
replay reports, the incident surface, the owner disposition, and the
repository/environment state into one auditable artifact.  It is
published INTO a slot that the V2 evidence index declared as a
``planned`` exclusion BEFORE publication (while the index still records
``final_verification.status == "CLOSURE_PENDING"``).

The audit that produced this validator found a closure that recorded
the path ``p5_evidence_index_v2.json`` while hashing index v3 bytes
(A3-01 path/SHA mismatch), accepted free-form suite counts (A3-04), and
recorded wrong warning counts.  This validator is therefore the STRICT
post-publication gate: it re-resolves every recorded ``relpath`` against
the real evidence roots and re-hashes every recorded ``sha256`` against
live bytes.  Anything that does not resolve to exactly the recorded
bytes is a failure — the closure never gets the benefit of the doubt.

Layout contract: the closure lives at ``<daily_root>/retrieval/``, so
the daily evidence root is ``closure_path.parent.parent``.

Usage::

    validate_release_closure.py <closure.json>
        [--current-tree [--repo-root PATH]]

Two scopes of verdict (A4-04/05/14/15/18 — a frozen bundle can be
internally consistent while the repository has since moved on):

- Default (frozen) mode validates the bundle against itself and the
  evidence roots only.  Success reports
  ``FROZEN_SNAPSHOT_CLOSURE_OK``.
- ``--current-tree`` additionally binds the closure to the LIVE
  repository under ``--repo-root`` (live ``git rev-parse HEAD``, the
  live manifest file bytes, and the receipt's repository head).
  Success reports ``CURRENT_TREE_CLOSURE_OK``; drift reports
  ``CURRENT_TREE_CLOSURE_FAIL`` with the stale binding named.

Emits ``{status, problems, checks, ...}`` as JSON to stdout.  Exit 0 on
an ``*_OK`` status, 1 otherwise.  READ-ONLY: the validator never writes.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from validate_evidence_index import (  # noqa: E402
    _HEX40, _HEX64, _check_generated_utc, _check_relpath, _resolve,
    _sha256_bytes, _sha256_path)

REPO = Path(__file__).resolve().parents[1]
SCHEMA = "P5_RELEASE_CLOSURE_V4"
INDEX_SCHEMA = "P5_EVIDENCE_INDEX_V2"
FROZEN_OK = "FROZEN_SNAPSHOT_CLOSURE_OK"
FROZEN_FAIL = "FROZEN_SNAPSHOT_CLOSURE_FAIL"
CURRENT_OK = "CURRENT_TREE_CLOSURE_OK"
CURRENT_FAIL = "CURRENT_TREE_CLOSURE_FAIL"
DEFAULT_MANIFEST_RELPATH = "docs/science/ARTIFACT_MANIFEST_V0.json"
#: The validator accepts both receipt generations: V1 receipts were
#: published before the V2 manifest-binding fields existed, and a frozen
#: v4 closure may honestly bind a V1 receipt file.
RECEIPT_SCHEMAS = ("P5_SUITE_RECEIPT_V1", "P5_SUITE_RECEIPT_V2")
OWNER_SCHEMA = "P5_D_OWNER_DISPOSITION_V2"
CLAIM_SCOPE = "research_only_no_operational_authorization"
REPLAY_STATUS = "REPLAY_OK"
REPLAY_SCOPE = "artifact_integrity_replay"
INDEX_OK = "INDEX_OK"
SURFACE_OK = "INCIDENT_SURFACE_OK"
FINAL_STATUSES = frozenset({"CLOSURE_PENDING", "CLOSED"})
AUTHORITY_FLAGS = ("promotion_eligible", "production_authorized",
                   "warning_path_authorized", "operational_claim")
COUNT_KEYS = ("collected", "passed", "skipped", "failed", "errors",
              "warnings")
_APPROVED_VALUE = "APPROVED"
ALLOWED_APPROVAL_VALUES = ("PENDING", "PENDING_OWNER_APPROVAL",
                           "NOT_REQUESTED", "NOT_APPROVED",
                           _APPROVED_VALUE)
REQUIRED_TOP_FIELDS = ("schema", "claim_scope", "root_of_trust",
                       "repository", "suite", "replays", "authority",
                       "incident_surface", "owner_disposition",
                       "environment", "activity_id", "started_utc",
                       "completed_utc")
_APPROVAL_KEYS = (("approval_status", "approved_by", "approval_utc"),
                  ("option3_approval_status", "option3_approved_by",
                   "option3_approval_utc"))


def _nonempty(value) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _resolve_relpath(root: Path, relpath, problems, ctx):
    """Resolve ``relpath`` under ``root``; fail closed on bad form or
    escape.  Returns the unresolved candidate path or None."""
    err = _check_relpath(relpath)
    if err:
        problems.append(f"{ctx}: {err}")
        return None
    base = _resolve(Path(root))
    candidate = Path(root) / relpath
    resolved = _resolve(candidate)
    try:
        resolved.relative_to(base)
    except ValueError:
        problems.append(
            f"{ctx}: relpath {relpath!r} resolves outside its root")
        return None
    return candidate


def _check_binding(path, recorded_sha, problems, ctx) -> bool:
    """A3-01 core: the recorded relpath must resolve to bytes whose
    sha256 equals the recorded sha256.  Returns True only when the live
    file exists and digests match."""
    if not (isinstance(recorded_sha, str) and _HEX64(recorded_sha)):
        problems.append(f"{ctx}: recorded sha256 must be 64 hex chars "
                        f"(got {recorded_sha!r})")
        return False
    if path is None or not path.is_file():
        problems.append(f"{ctx}: file missing on disk: {path}")
        return False
    actual = _sha256_path(path)
    if actual != recorded_sha:
        problems.append(
            f"{ctx}: path/SHA mismatch — recorded relpath resolves to "
            f"sha256 {actual[:16]}… but the closure recorded "
            f"{recorded_sha[:16]}…")
        return False
    return True


def _load_json(path, problems, ctx):
    try:
        doc = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        problems.append(f"{ctx}: not readable JSON: {exc}")
        return None
    if not isinstance(doc, dict):
        problems.append(f"{ctx}: root must be a JSON object")
        return None
    return doc


def _approval_consistency(mapping, problems, ctx, *, require_status=False):
    """G-05 approval surface: ``approved_by``/``approval_utc`` must be
    both null or both set, and APPROVED requires both.  Accepts the plain
    owner-disposition keys or the ``option3_`` mirror keys."""
    triple = None
    for status_key, by_key, utc_key in _APPROVAL_KEYS:
        if any(k in mapping for k in (status_key, by_key, utc_key)):
            triple = (mapping.get(status_key), mapping.get(by_key),
                      mapping.get(utc_key))
            break
    if triple is None:
        if require_status:
            problems.append(f"{ctx}: approval fields are required")
        return
    status, approved_by, approval_utc = triple
    if status is not None and status not in ALLOWED_APPROVAL_VALUES:
        problems.append(f"{ctx}: approval_status {status!r} is not in "
                        f"vocabulary {list(ALLOWED_APPROVAL_VALUES)}")
    if require_status and status is None:
        problems.append(f"{ctx}: approval_status is required")
    if (approved_by is None) != (approval_utc is None):
        problems.append(f"{ctx}: approved_by and approval_utc must be "
                        "both null or both set")
    if status == _APPROVED_VALUE and (approved_by is None
                                 or approval_utc is None):
        problems.append(f"{ctx}: APPROVED requires an owner identity "
                        "and a timestamp")
    if approved_by is not None and status != _APPROVED_VALUE:
        problems.append(f"{ctx}: approved_by is set but approval_status "
                        f"is {status!r}, not APPROVED")


def _parse_utc(value):
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return None


def _live_head(repo_root) -> str:
    """Resolve ``git rev-parse HEAD`` under ``repo_root``.

    Raises ValueError when the live HEAD cannot be resolved — the
    caller treats that as fail-closed, never as a skip.
    """
    proc = subprocess.run(["git", "rev-parse", "HEAD"],
                          cwd=str(repo_root), capture_output=True,
                          text=True, check=False)
    head = proc.stdout.strip()
    if proc.returncode != 0 or not _HEX40(head):
        raise ValueError(
            f"git rev-parse HEAD failed under {repo_root}: "
            f"{proc.stderr.strip() or head or 'no output'}")
    return head


def validate_closure(closure_path, *, current_tree=False,
                     repo_root=None) -> dict:
    """Validate a published P5_RELEASE_CLOSURE_V4 document.

    Default mode validates the frozen bundle: success is
    ``FROZEN_SNAPSHOT_CLOSURE_OK``.  With ``current_tree=True`` the
    closure is additionally bound to the live repository under
    ``repo_root`` (default: this worktree): success is
    ``CURRENT_TREE_CLOSURE_OK`` and any drift is
    ``CURRENT_TREE_CLOSURE_FAIL``.

    Returns ``{status, closure, schema, problems, checks}``.
    Read-only: nothing is written.
    """
    problems: list[str] = []
    checks: dict[str, str] = {}
    closure_path = Path(closure_path)
    resolved = _resolve(closure_path)
    fail_status = CURRENT_FAIL if current_tree else FROZEN_FAIL
    ok_status = CURRENT_OK if current_tree else FROZEN_OK

    def report(status):
        return {"status": status, "closure": str(resolved),
                "schema": doc.get("schema") if isinstance(doc, dict)
                else None,
                "problems": problems, "checks": checks}

    def mark(name, before):
        checks[name] = "FAIL" if len(problems) > before else "PASS"

    # ---- 1. closure file exists, is JSON, has schema + sidecar ------
    before = len(problems)
    if not closure_path.is_file():
        problems.append(f"closure file missing: {closure_path}")
        checks["closure_file"] = "FAIL"
        return {"status": fail_status, "closure": str(resolved),
                "schema": None, "problems": problems, "checks": checks}
    try:
        raw = closure_path.read_bytes()
    except OSError as exc:
        problems.append(f"closure unreadable: {exc}")
        checks["closure_file"] = "FAIL"
        return {"status": fail_status, "closure": str(resolved),
                "schema": None, "problems": problems, "checks": checks}
    try:
        doc = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        problems.append(f"closure is not valid JSON: {exc}")
        checks["closure_file"] = "FAIL"
        return {"status": fail_status, "closure": str(resolved),
                "schema": None, "problems": problems, "checks": checks}
    if not isinstance(doc, dict):
        problems.append("closure root must be a JSON object")
        checks["closure_file"] = "FAIL"
        return {"status": fail_status, "closure": str(resolved),
                "schema": None, "problems": problems, "checks": checks}
    mark("closure_file", before)

    before = len(problems)
    if doc.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA!r} "
                        f"(got {doc.get('schema')!r})")
    for field in REQUIRED_TOP_FIELDS:
        if field not in doc:
            problems.append(f"missing required top-level field {field!r}")
    mark("schema", before)

    before = len(problems)
    sidecar = Path(str(resolved) + ".sha256")
    if not sidecar.is_file():
        problems.append(f"self sidecar missing: {sidecar.name}")
    else:
        tokens = sidecar.read_bytes().split()
        first = tokens[0].decode("utf-8", "replace") if tokens else ""
        if first != _sha256_bytes(raw):
            problems.append("self sidecar first token does not equal "
                            "the closure file sha256")
    mark("sidecar", before)

    # The closure lives at <daily_root>/retrieval/<file>; the daily
    # evidence root is the parent of the directory containing it.
    daily_root = resolved.parent.parent

    # ---- 2. root_of_trust: recorded path must resolve to recorded --
    #         bytes (A3-01), validator_status INDEX_OK, root_id set.
    before = len(problems)
    rot = doc.get("root_of_trust")
    index_path = None
    index_doc = None
    if not isinstance(rot, dict):
        problems.append("root_of_trust must be a structured object")
    else:
        if not _nonempty(rot.get("root_id")):
            problems.append("root_of_trust.root_id must be a non-empty "
                            "string")
        if rot.get("index_schema") != INDEX_SCHEMA:
            problems.append(f"root_of_trust.index_schema must be "
                            f"{INDEX_SCHEMA!r} "
                            f"(got {rot.get('index_schema')!r})")
        if rot.get("validator_status") != INDEX_OK:
            problems.append("root_of_trust.validator_status must be "
                            f"{INDEX_OK!r} "
                            f"(got {rot.get('validator_status')!r})")
        candidate = _resolve_relpath(daily_root, rot.get("relpath"),
                                     problems, "root_of_trust")
        if candidate is not None and candidate.is_file():
            index_path = candidate
        _check_binding(candidate, rot.get("sha256"), problems,
                       "root_of_trust")
    mark("root_of_trust", before)

    # ---- 3. index cross-check: planned slot, final status, manifest -
    before = len(problems)
    if index_path is None:
        problems.append("index cross-check impossible: root_of_trust "
                        "did not resolve to an existing file")
    else:
        index_doc = _load_json(index_path, problems, "index")
    if isinstance(index_doc, dict):
        if index_doc.get("schema") != INDEX_SCHEMA:
            problems.append(f"resolved index schema must be "
                            f"{INDEX_SCHEMA!r} "
                            f"(got {index_doc.get('schema')!r})")
        rid = rot.get("root_id") if isinstance(rot, dict) else None
        roots = index_doc.get("roots")
        if not isinstance(roots, dict):
            problems.append("index roots must be a structured object")
            roots = {}
        spec = roots.get(rid)
        if rid is not None:
            if not isinstance(spec, dict) or not _nonempty(
                    spec.get("path")):
                problems.append(f"index roots has no usable path for "
                                f"root_id {rid!r}")
            elif _resolve(Path(spec["path"]).expanduser()) != daily_root:
                problems.append(
                    f"index root {rid!r} resolves to {spec['path']!r}, "
                    f"not the daily root {daily_root}")
        try:
            closure_rel = resolved.relative_to(daily_root).as_posix()
        except ValueError:
            closure_rel = None
            problems.append("closure file is not inside the daily root")
        planned = {(e.get("root_id"), e.get("relpath"))
                   for e in index_doc.get("exclusions", [])
                   if isinstance(e, dict) and e.get("state") == "planned"}
        if closure_rel is not None and (rid, closure_rel) not in planned:
            problems.append(
                f"closure slot ({rid!r}, {closure_rel!r}) is not a "
                "'planned' exclusion of the index — the slot must be "
                "declared before publication")
        final = index_doc.get("final_verification")
        final_status = (final.get("status") if isinstance(final, dict)
                        else None)
        if final_status not in FINAL_STATUSES:
            problems.append("index final_verification.status must be "
                            f"one of {sorted(FINAL_STATUSES)} "
                            f"(got {final_status!r})")
        repo = doc.get("repository")
        if isinstance(repo, dict) and (
                index_doc.get("manifest_sha256")
                != repo.get("manifest_sha256")):
            problems.append("index manifest_sha256 does not match "
                            "closure.repository.manifest_sha256")
    mark("index_crosscheck", before)

    # ---- repository field hygiene ------------------------------------
    before = len(problems)
    repo = doc.get("repository")
    if not isinstance(repo, dict):
        problems.append("repository must be a structured object")
        repo = {}
    else:
        for field in ("head", "content_head", "manifest_commit"):
            value = repo.get(field)
            if not (isinstance(value, str) and _HEX40(value)):
                problems.append(f"repository.{field} must be a 40-hex "
                                f"commit id (got {value!r})")
        value = repo.get("manifest_sha256")
        if not (isinstance(value, str) and _HEX64(value)):
            problems.append("repository.manifest_sha256 must be 64 hex "
                            "chars")
    mark("repository", before)

    # ---- 4. suite binding: receipt path+sha, receipt is green, ------
    #         every recorded count equals the receipt's (A3-04), and
    #         the receipt's repository_head is a recorded commit.
    before = len(problems)
    receipt = None
    suite = doc.get("suite")
    if not isinstance(suite, dict):
        problems.append("suite must be a structured object")
    else:
        if not _nonempty(suite.get("generator_activity_id")):
            problems.append("suite.generator_activity_id must be a "
                            "non-empty string")
        closure_counts = suite.get("counts")
        if not isinstance(closure_counts, dict):
            problems.append("suite.counts must be a structured object")
            closure_counts = None
        else:
            for key in COUNT_KEYS:
                value = closure_counts.get(key)
                if (not isinstance(value, int)
                        or isinstance(value, bool) or value < 0):
                    problems.append(f"suite.counts.{key} must be a "
                                    f"non-negative integer "
                                    f"(got {value!r})")
        receipt_path = _resolve_relpath(daily_root,
                                        suite.get("receipt_relpath"),
                                        problems, "suite")
        if _check_binding(receipt_path, suite.get("receipt_sha256"),
                          problems, "suite.receipt"):
            receipt = _load_json(receipt_path, problems,
                                 "suite.receipt")
        if isinstance(receipt, dict):
            if receipt.get("schema") not in RECEIPT_SCHEMAS:
                problems.append(f"suite receipt schema must be one of "
                                f"{list(RECEIPT_SCHEMAS)} "
                                f"(got {receipt.get('schema')!r})")
            if receipt.get("exit_code") != 0:
                problems.append("suite receipt exit_code must be 0 "
                                f"(got {receipt.get('exit_code')!r})")
            receipt_counts = receipt.get("counts")
            if not isinstance(receipt_counts, dict):
                problems.append("suite receipt counts must be a "
                                "structured object")
            else:
                if (receipt_counts.get("failed") != 0
                        or receipt_counts.get("errors") != 0):
                    problems.append("suite receipt records failures or "
                                    f"errors: {receipt_counts!r}")
                if isinstance(closure_counts, dict):
                    for key in COUNT_KEYS:
                        if (receipt_counts.get(key)
                                != closure_counts.get(key)):
                            problems.append(
                                f"suite.counts.{key} diverges from the "
                                f"receipt: closure="
                                f"{closure_counts.get(key)!r} receipt="
                                f"{receipt_counts.get(key)!r}")
            heads = {repo.get(k) for k in
                     ("head", "content_head", "manifest_commit")}
            heads.discard(None)
            if receipt.get("repository_head") not in heads:
                problems.append(
                    "suite receipt repository_head "
                    f"{receipt.get('repository_head')!r} is not one of "
                    "the recorded repository commits")
    mark("suite_binding", before)

    # ---- 5. replays: each report resolves under its named index -----
    #         root, hashes to the recorded sha, and the live payload
    #         confirms REPLAY_OK / artifact_integrity_replay.
    replays = doc.get("replays")
    roots = (index_doc.get("roots") if isinstance(index_doc, dict)
             else {})
    if not isinstance(roots, dict):
        roots = {}
    for name in ("daily", "seasonal"):
        before = len(problems)
        entry = replays.get(name) if isinstance(replays, dict) else None
        if not isinstance(entry, dict):
            problems.append(f"replays.{name} must be a structured object")
        else:
            rid = entry.get("root_id")
            spec = roots.get(rid)
            if not isinstance(spec, dict) or not _nonempty(
                    spec.get("path")):
                problems.append(f"replays.{name}.root_id {rid!r} is "
                                "not a declared index root")
                replay_root = None
            else:
                replay_root = Path(spec["path"]).expanduser()
            if entry.get("status") != REPLAY_STATUS:
                problems.append(f"replays.{name}.status must be "
                                f"{REPLAY_STATUS!r} "
                                f"(got {entry.get('status')!r})")
            if entry.get("scope") != REPLAY_SCOPE:
                problems.append(f"replays.{name}.scope must be "
                                f"{REPLAY_SCOPE!r} "
                                f"(got {entry.get('scope')!r})")
            if replay_root is not None:
                report_path = _resolve_relpath(
                    replay_root, entry.get("relpath"), problems,
                    f"replays.{name}")
                if _check_binding(report_path, entry.get("sha256"),
                                  problems, f"replays.{name}"):
                    payload = _load_json(report_path, problems,
                                         f"replays.{name}")
                    if isinstance(payload, dict):
                        if payload.get("status") != REPLAY_STATUS:
                            problems.append(
                                f"replays.{name}: live report status "
                                f"is {payload.get('status')!r}, not "
                                f"{REPLAY_STATUS!r}")
                        if payload.get("replay_scope") != REPLAY_SCOPE:
                            problems.append(
                                f"replays.{name}: live report scope "
                                f"is {payload.get('replay_scope')!r}, "
                                f"not {REPLAY_SCOPE!r}")
        mark(f"replay_{name}", before)

    # ---- 6. authority + claim scope ----------------------------------
    before = len(problems)
    authority = doc.get("authority")
    if not isinstance(authority, dict):
        problems.append("authority must be a structured object")
    else:
        for flag in AUTHORITY_FLAGS:
            if authority.get(flag) is not False:
                problems.append(f"authority.{flag} must be exactly "
                                f"false (got {authority.get(flag)!r})")
    if doc.get("claim_scope") != CLAIM_SCOPE:
        problems.append(f"claim_scope must be {CLAIM_SCOPE!r} "
                        f"(got {doc.get('claim_scope')!r})")
    mark("authority", before)

    # ---- 7. owner disposition: path+sha binding and approval-field --
    #         consistency on both the mirror and the live document.
    before = len(problems)
    owner_sec = doc.get("owner_disposition")
    if not isinstance(owner_sec, dict):
        problems.append("owner_disposition must be a structured object")
    else:
        if any(key in owner_sec for keys in _APPROVAL_KEYS
               for key in keys):
            _approval_consistency(owner_sec, problems,
                                  "owner_disposition")
        owner_rel = (owner_sec.get("relpath")
                     or owner_sec.get("logical_path")
                     or owner_sec.get("path"))
        owner_path = None
        rel_err = _check_relpath(owner_rel)
        if rel_err:
            problems.append(f"owner_disposition: {rel_err}")
        else:
            for base in (daily_root, daily_root / "retrieval",
                         daily_root.parent):
                candidate = base / owner_rel
                if candidate.is_file():
                    owner_path = candidate
                    break
            if owner_path is None:
                problems.append(
                    f"owner_disposition: file missing on disk: "
                    f"{owner_rel!r}")
        owner_doc = None
        if _check_binding(owner_path, owner_sec.get("sha256"),
                          problems, "owner_disposition"):
            owner_doc = _load_json(owner_path, problems,
                                   "owner_disposition")
        if isinstance(owner_doc, dict):
            if owner_doc.get("schema") != OWNER_SCHEMA:
                problems.append(f"owner disposition schema must be "
                                f"{OWNER_SCHEMA!r} "
                                f"(got {owner_doc.get('schema')!r})")
            _approval_consistency(owner_doc, problems,
                                  "owner_disposition file",
                                  require_status=True)
    mark("owner_disposition", before)

    # ---- 8. environment / activity / timing --------------------------
    before = len(problems)
    env = doc.get("environment")
    digest = None
    packages = None
    if not isinstance(env, dict):
        problems.append("environment must be a structured object")
    else:
        digest = env.get("environment_digest")
        if digest is None:
            digest = doc.get("environment_digest")
        packages = env.get("packages")
    if not (isinstance(digest, str) and _HEX64(digest)):
        problems.append("environment_digest must be 64 hex chars")
    # A4-14: the recorded digest must equal the recomputed sha256 of the
    # canonical packages serialization — a missing packages map is a
    # problem, never a skip.
    if not isinstance(packages, dict):
        problems.append("environment.packages must be a structured "
                        "object mapping package name to version")
    elif isinstance(digest, str) and _HEX64(digest):
        recomputed = _sha256_bytes(json.dumps(
            packages, sort_keys=True,
            separators=(",", ":")).encode("utf-8"))
        if recomputed != digest:
            problems.append(
                "environment_digest does not equal the recomputed "
                "sha256 of the canonical environment.packages "
                "serialization — the packages map was altered after "
                "the digest was recorded")
    if not _nonempty(doc.get("activity_id")):
        problems.append("activity_id must be a non-empty string")
    started, completed = doc.get("started_utc"), doc.get("completed_utc")
    err = _check_generated_utc(started)
    if err:
        problems.append(f"started_utc: {err}")
    err = _check_generated_utc(completed)
    if err:
        problems.append(f"completed_utc: {err}")
    start_dt, end_dt = _parse_utc(started), _parse_utc(completed)
    if (start_dt is not None and end_dt is not None
            and start_dt > end_dt):
        problems.append(f"started_utc {started!r} is after "
                        f"completed_utc {completed!r}")
    mark("environment", before)

    # ---- 9. incident surface: resolves and hashes to live bytes ------
    before = len(problems)
    surface = doc.get("incident_surface")
    if not isinstance(surface, dict):
        problems.append("incident_surface must be a structured object")
    else:
        rel = (surface.get("relpath") or surface.get("logical_path")
               or surface.get("path"))
        err = _check_relpath(rel)
        target = None
        if err:
            problems.append(f"incident_surface: {err}")
        else:
            for base in (daily_root.parent, daily_root):
                candidate = base / rel
                if candidate.is_file():
                    target = candidate
                    break
            if target is None:
                problems.append("incident_surface target not found on "
                                f"disk: {rel!r}")
            else:
                tres = _resolve(target)
                try:
                    tres.relative_to(daily_root.parent)
                except ValueError:
                    problems.append("incident_surface target resolves "
                                    "outside the evidence root")
                    target = None
        if target is not None:
            recorded = surface.get("sha256")
            if not (isinstance(recorded, str) and _HEX64(recorded)):
                problems.append("incident_surface.sha256 must be 64 "
                                "hex chars")
            elif _sha256_path(target) != recorded:
                problems.append("incident_surface.sha256 does not "
                                "match the live file")
        if ("validator_status" in surface
                and surface["validator_status"] != SURFACE_OK):
            problems.append("incident_surface.validator_status must "
                            f"be {SURFACE_OK!r} "
                            f"(got {surface['validator_status']!r})")
        if ("main_release_excluded" in surface
                and surface["main_release_excluded"] is not True):
            problems.append("incident_surface.main_release_excluded "
                            "must be true")
    mark("incident_surface", before)

    # ---- 10. current-tree freshness (A4-04/05/15/18) -----------------
    # Frozen-mode success proves the bundle is internally consistent;
    # only --current-tree proves the bundle still describes the LIVE
    # repository.  Every binding below fails closed and names the
    # drifted field.
    if current_tree:
        before = len(problems)
        root_path = _resolve(Path(repo_root)) if repo_root else REPO

        live_head = None
        try:
            live_head = _live_head(root_path)
        except (OSError, ValueError) as exc:
            problems.append(f"live HEAD unresolvable under "
                            f"{root_path}: {exc}")
        if (live_head is not None
                and repo.get("head") != live_head):
            problems.append(
                f"repository.head {str(repo.get('head'))[:12]}… != "
                f"live HEAD {live_head[:12]}… — the closure binds a "
                "stale repository state")

        manifest_rel = (repo.get("manifest_relpath")
                        or DEFAULT_MANIFEST_RELPATH)
        manifest_path = _resolve_relpath(
            root_path, manifest_rel, problems, "live manifest")
        live_manifest_sha = None
        live_content_head = None
        if manifest_path is not None:
            if not manifest_path.is_file():
                problems.append(f"live manifest missing on disk: "
                                f"{manifest_path}")
            else:
                live_manifest_sha = _sha256_path(manifest_path)
                manifest_doc = _load_json(manifest_path, problems,
                                          "live manifest")
                if isinstance(manifest_doc, dict):
                    live_content_head = manifest_doc.get("content_head")
        if live_manifest_sha is None:
            problems.append("live manifest sha256 unavailable — the "
                            "manifest binding cannot be verified")
        else:
            if repo.get("manifest_sha256") != live_manifest_sha:
                problems.append(
                    f"repository.manifest_sha256 "
                    f"{str(repo.get('manifest_sha256'))[:16]}… != live "
                    f"manifest sha256 {live_manifest_sha[:16]}…")
            index_manifest_sha = None
            if isinstance(index_doc, dict):
                manifest_entry = index_doc.get("manifest")
                if isinstance(manifest_entry, dict):
                    index_manifest_sha = manifest_entry.get("sha256")
                if index_manifest_sha is None:
                    index_manifest_sha = index_doc.get(
                        "manifest_sha256")
            if index_manifest_sha is None:
                problems.append("resolved index records no "
                                "manifest.sha256 — the three-way "
                                "manifest agreement is unverifiable")
            else:
                if index_manifest_sha != live_manifest_sha:
                    problems.append(
                        f"index manifest.sha256 "
                        f"{str(index_manifest_sha)[:16]}… != live "
                        f"manifest sha256 {live_manifest_sha[:16]}…")
                if index_manifest_sha != repo.get("manifest_sha256"):
                    problems.append(
                        f"index manifest.sha256 "
                        f"{str(index_manifest_sha)[:16]}… != closure "
                        f"repository.manifest_sha256 "
                        f"{str(repo.get('manifest_sha256'))[:16]}…")
        if live_content_head is not None and (
                repo.get("content_head") != live_content_head):
            problems.append(
                f"repository.content_head "
                f"{str(repo.get('content_head'))[:12]}… != live "
                f"manifest content_head {live_content_head[:12]}…")

        if isinstance(receipt, dict):
            valid_heads = {h for h in (live_head, live_content_head)
                           if isinstance(h, str)}
            if not valid_heads:
                problems.append("suite receipt repository_head cannot "
                                "be verified: live HEAD and manifest "
                                "content_head are both unresolvable")
            elif receipt.get("repository_head") not in valid_heads:
                problems.append(
                    f"suite receipt repository_head "
                    f"{str(receipt.get('repository_head'))[:12]}… is "
                    "neither the live HEAD nor the live manifest "
                    "content_head")
        else:
            problems.append("suite receipt unresolved — the "
                            "current-tree repository_head binding "
                            "cannot be verified")
        mark("current_tree", before)

    return report(fail_status if problems else ok_status)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Strict post-publication validator for a "
                    "P5_RELEASE_CLOSURE_V4 document (read-only; JSON "
                    "report to stdout).  Default mode validates the "
                    "frozen bundle (FROZEN_SNAPSHOT_CLOSURE_OK); "
                    "--current-tree additionally binds the live "
                    "repository (CURRENT_TREE_CLOSURE_OK).")
    parser.add_argument("closure",
                        help="path to the release-closure JSON file")
    parser.add_argument("--current-tree", action="store_true",
                        help="verify the closure still binds the LIVE "
                             "repository state under --repo-root")
    parser.add_argument("--repo-root", default=None,
                        help="repository root for --current-tree "
                             "(default: this worktree)")
    args = parser.parse_args(argv)
    try:
        report = validate_closure(args.closure,
                                  current_tree=args.current_tree,
                                  repo_root=args.repo_root)
    except (OSError, ValueError) as exc:
        report = {"status": (CURRENT_FAIL if args.current_tree
                             else FROZEN_FAIL),
                  "closure": str(args.closure), "schema": None,
                  "problems": [str(exc)], "checks": {}}
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] in (FROZEN_OK, CURRENT_OK) else 1


if __name__ == "__main__":
    sys.exit(main())
