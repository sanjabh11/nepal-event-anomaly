"""Research-only CLI namespace: ``python -m nepal.research_v0.cli``.

Intentionally narrow.  This CLI re-verifies research envelopes, scans
for forbidden claims, and prints policy decisions; it contains no
intake, download, freeze, clustering, or run commands — those do not
exist until the P3 design-approval gate passes, and they will never be
added to this module's argparse surface without an approved design
hash binding.
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path
from typing import Optional, Sequence

from . import gates, policy
from ._hashing import sha256_canonical


def _nonneg_finite(text: str) -> float:
    try:
        value = float(text)
    except ValueError:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a number") from None
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError(
            f"{text!r} must be finite and non-negative")
    return value


def _cmd_validate_envelope(args: argparse.Namespace) -> int:
    try:
        with open(args.file, "r", encoding="utf-8") as handle:
            envelope = json.load(handle, parse_constant=_reject_constant)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"BLOCKED: cannot load envelope: {exc}", file=sys.stderr)
        return 1
    if not isinstance(envelope, dict):
        print("BLOCKED: envelope is not a JSON object", file=sys.stderr)
        return 1
    problems: list[str] = []
    for field, expected in (
            ("research_diagnostic_only", True),
            ("claim_scope", "research_only_no_operational_authorization"),
            ("promotion_eligible", False),
            ("production_authorized", False),
            ("warning_path_authorized", False)):
        if envelope.get(field) != expected:
            problems.append(
                f"envelope field {field!r} must be {expected!r}, found "
                f"{envelope.get(field)!r}")
    problems.extend(gates.envelope_status_problems(
        str(envelope.get("status", ""))))
    # Recompute the self-hash over the envelope minus the digest field;
    # a fabricated or tampered envelope fails.
    declared = envelope.get("envelope_sha256")
    if not isinstance(declared, str) or \
            not gates.SHA256_RE.match(declared):
        problems.append("envelope_sha256 missing or not 64-hex")
    else:
        body = {k: v for k, v in envelope.items()
                if k != "envelope_sha256"}
        try:
            actual = sha256_canonical(body)
        except (TypeError, ValueError) as exc:
            problems.append(f"envelope not canonically serializable: "
                            f"{exc}")
        else:
            if actual != declared:
                problems.append("envelope_sha256 mismatch — fabricated "
                                "or tampered envelope")
    for name in ("matrix_sha256", "policy_sha256"):
        value = envelope.get(name)
        if not isinstance(value, str) or not gates.SHA256_RE.match(value):
            problems.append(f"{name} missing or not 64-hex")
    if envelope.get("human_approved") is not True:
        problems.append("human_approved must be true with named "
                        "approver")
    for name in ("approved_by", "approved_at", "approver_attestation",
                 "approval_scope"):
        if not str(envelope.get(name) or "").strip():
            problems.append(f"{name} must be present and non-empty")
    if str(envelope.get("approval_scope", "")) != "design_review_only":
        problems.append("approval_scope must be 'design_review_only'")
    digests = envelope.get("record_digests")
    if not isinstance(digests, dict) or not all(
            isinstance(v, str) and gates.SHA256_RE.match(v)
            for v in digests.values()):
        problems.append("record_digests must map names to 64-hex "
                        "digests")

    # E02/E15 — the envelope records the canonical artifact root and
    # D1/D2 paths it was built against; CLI verification resolves to
    # exactly those files — no caller-supplied alternative is trusted.
    from pathlib import Path as _P
    artifact_root_raw = envelope.get("artifact_root")
    if not artifact_root_raw:
        problems.append("artifact_root missing — envelope is not "
                        "root-bound")
        artifact_root = None
    else:
        artifact_root = _P(str(artifact_root_raw))
        if not artifact_root.is_dir():
            problems.append(
                f"artifact_root {artifact_root} is not a directory")
            artifact_root = None
    for path_field, digest_field in (
            ("matrix_path", "matrix_sha256"),
            ("policy_path", "policy_sha256")):
        recorded = envelope.get(path_field)
        if not recorded:
            problems.append(f"{path_field} missing from envelope")
            continue
        path = _P(str(recorded))
        if artifact_root is not None:
            try:
                path.resolve().relative_to(artifact_root.resolve())
            except ValueError:
                problems.append(
                    f"{path_field} {path} escapes artifact_root")
                continue
        if not path.is_file() or path.is_symlink():
            problems.append(
                f"{path_field} {path} is not a regular file")
            continue
        try:
            actual = gates.sha256_file(path)
        except ValueError as exc:
            problems.append(f"{path_field} {path}: {exc}")
            continue
        if actual != envelope.get(digest_field):
            problems.append(
                f"{path_field} digest {actual[:16]}… does not match "
                f"envelope {digest_field}")

    # E01 — typed record reconstruction: every bound digest must map
    # to a record file that deserializes to the declared type, passes
    # record-level validation, and re-hashes identically.
    status = str(envelope.get("status", ""))
    bound = envelope.get("record_digests") or {}
    record_types = envelope.get("record_types") or {}
    if bound and not args.records_dir:
        problems.append("envelope binds record digests but "
                        "--records-dir was not supplied")
    if bound and args.records_dir:
        records_dir = _P(args.records_dir)
        if not records_dir.is_dir():
            problems.append(f"--records-dir {records_dir} is not a "
                            "directory")
        else:
            from .records import (deserialize_record,
                                  STATUS_REQUIRED_ARTIFACT_TYPES,
                                  STATUS_REQUIRED_RECORDS,
                                  EXECUTION_STATUSES)
            from .records import EvidenceArtifactV0, SourceRecordV0
            records = {}
            for name, digest in bound.items():
                rec_file = records_dir / f"{name}.json"
                if not rec_file.is_file() or rec_file.is_symlink():
                    problems.append(
                        f"record {name!r}: expected regular file "
                        f"{rec_file} missing")
                    continue
                try:
                    payload = json.loads(
                        rec_file.read_text(encoding="utf-8"),
                        parse_constant=_reject_constant)
                except (OSError, json.JSONDecodeError, ValueError,
                        TypeError) as exc:
                    problems.append(
                        f"record {name!r}: cannot load: {exc}")
                    continue
                if sha256_canonical(payload) != digest:
                    problems.append(
                        f"record {name!r}: payload digest does not "
                        "match bound digest")
                    continue
                try:
                    record = deserialize_record(payload)
                except ValueError as exc:
                    problems.append(f"record {name!r}: {exc}")
                    continue
                declared_type = record_types.get(name)
                if declared_type and \
                        type(record).__name__ != declared_type:
                    problems.append(
                        f"record {name!r}: declared type "
                        f"{declared_type} != payload type "
                        f"{type(record).__name__}")
                    continue
                problems.extend(
                    f"record {name!r}: {p}"
                    for p in gates._validate_record(name, record))
                records[name] = record
            if not problems:
                # Replay the same graph validation the builder runs.
                problems.extend(
                    gates._status_record_problems(status, records))
                problems.extend(
                    gates._cross_record_problems(records, status))
                # Byte-bound evidence replay: sidecars, artifacts,
                # vintage payloads under the recorded evidence_root.
                ev_root = envelope.get("evidence_root")
                needs_ev = (
                    status in EXECUTION_STATUSES
                    or any(type(r) is EvidenceArtifactV0
                           for r in records.values())
                    or any(getattr(r, "posture", "") ==
                           "EVIDENCE_VERIFIED"
                           for r in records.values()))
                if needs_ev and not ev_root:
                    problems.append(
                        "execution/byte-bound envelope lacks "
                        "evidence_root")
                elif needs_ev and ev_root:
                    ev = _P(str(ev_root))
                    if not ev.is_dir():
                        problems.append(
                            f"evidence_root {ev} is not a directory")
                    elif artifact_root is not None and \
                            ev.resolve() != artifact_root.resolve():
                        try:
                            ev.resolve().relative_to(
                                artifact_root.resolve())
                        except ValueError:
                            problems.append(
                                "evidence_root escapes artifact_root")
                    if not problems:
                        from .records import ForecastVintageV0
                        from ._hashing import hash_artifact
                        for name, record in records.items():
                            problems.extend(
                                f"record {name!r}: {p}" for p in
                                gates.source_evidence_problems(
                                    record, evidence_root=ev_root))
                            if type(record) is EvidenceArtifactV0:
                                problems.extend(
                                    f"record {name!r}: {p}" for p in
                                    gates._evidence_artifact_problems(
                                        record, ev_root))
                            elif type(record) is ForecastVintageV0:
                                for pf, df in (
                                        ("archive_payload_path",
                                         "archive_payload_sha256"),
                                        ("retrieval_record_path",
                                         "retrieval_record_sha256")):
                                    try:
                                        meta = hash_artifact(
                                            gates._resolve_against(
                                                getattr(record, pf),
                                                ev), ev)
                                    except ValueError as exc:
                                        problems.append(
                                            f"record {name!r}: {exc}")
                                        continue
                                    if meta["sha256"] != getattr(
                                            record, df):
                                        problems.append(
                                            f"record {name!r}: {df} "
                                            "does not match file "
                                            "bytes")
    if problems:
        for problem in problems:
            print(f"BLOCKED: {problem}", file=sys.stderr)
        return 1
    print(f"envelope {args.file} satisfies research-only claim checks "
          "and bundle verification")
    return 0


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r} rejected")


def _cmd_verify_manifest(args: argparse.Namespace) -> int:
    """P0-02/O01 — stdlib manifest verifier: every listed file must
    exist with matching size and sha256, and the manifest must bind a
    content_head.  Stdlib-only so it runs in any environment."""
    import hashlib
    path = Path(args.file)
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"),
                              parse_constant=_reject_constant)
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"manifest error: {exc}")
        return 1
    problems: list[str] = []
    if not isinstance(manifest.get("content_head"), str) or \
            len(manifest["content_head"]) != 40:
        problems.append("content_head missing or not a 40-char SHA")
    mc = manifest.get("manifest_commit")
    if not isinstance(mc, str) or len(mc) != 40:
        problems.append(
            "manifest_commit missing or not a 40-char SHA")
    elif mc != manifest.get("content_head"):
        problems.append(
            "manifest_commit does not equal content_head — it must "
            "name the final content commit, never a stale or "
            "manifest-only rebind commit")
    if not isinstance(manifest.get("baseline_head"), str) or \
            len(manifest["baseline_head"]) != 40:
        problems.append("baseline_head missing or not a 40-char SHA")
    # P5-A2 audit fix — head SHAs must resolve to real commit objects,
    # not merely be well-formed strings.  A manifest whose content_head
    # names a non-existent object binds nothing.  The check applies only
    # when the manifest root is inside a git worktree; synthetic fixtures
    # without a git context cannot be resolved and are skipped.  The
    # subprocess call lives in nepal.gitutil — research_v0's isolation
    # contract forbids subprocess imports in this package.
    from nepal.gitutil import is_git_worktree, resolves_to_commit
    _mroot = path.resolve().parents[2]
    if is_git_worktree(_mroot):
        for field in ("content_head", "baseline_head"):
            sha = manifest.get(field)
            if not isinstance(sha, str) or len(sha) != 40:
                continue
            if resolves_to_commit(_mroot, sha) is False:
                problems.append(
                    f"{field} {sha} does not resolve to a commit "
                    "object — the manifest binds a dangling identity")
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        problems.append("files list missing or empty")
    else:
        # Manifest paths are repo-root relative; derive the root from
        # the manifest location (docs/science -> repo root).
        root = path.resolve().parents[2]
        seen: set[str] = set()
        for entry in files:
            rel = entry.get("relpath", "")
            f = (root / rel)
            try:
                f.resolve().relative_to(root)
            except ValueError:
                problems.append(f"{rel}: escapes manifest root")
                continue
            if rel in seen:
                problems.append(f"{rel}: duplicate entry")
                continue
            seen.add(rel)
            if not f.is_file() or f.is_symlink():
                problems.append(f"{rel}: missing or not a regular file")
                continue
            if f.stat().st_size != entry.get("size_bytes"):
                problems.append(f"{rel}: size mismatch")
                continue
            if hashlib.sha256(f.read_bytes()).hexdigest() != \
                    entry.get("sha256"):
                problems.append(f"{rel}: sha256 mismatch")
    tr = manifest.get("test_results")
    if not isinstance(tr, dict) or not tr.get("research_v0"):
        problems.append("test_results missing")
    if problems:
        for p in problems:
            print(f"MANIFEST FINDING: {p}")
        return 1
    print(f"{path}: manifest verified ({len(seen)} files)")
    return 0


def _iter_claim_scan_paths(target: str) -> list[Path]:
    """Files to scan: the file itself, or every regular file under a
    directory (sorted, deterministic).  Directories must not reach
    ``open()`` — a directory arg used to fail the whole CI lint."""
    p = Path(target)
    if p.is_dir():
        return sorted(f for f in p.rglob("*") if f.is_file())
    return [p]


def _cmd_claim_scan(args: argparse.Namespace) -> int:
    any_findings = False
    scanned = 0
    for path in _iter_claim_scan_paths(args.file):
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            print(f"BLOCKED: cannot read {path}: {exc}",
                  file=sys.stderr)
            any_findings = True
            continue
        findings = gates.scan_claims_text(text)
        scanned += 1
        if findings:
            any_findings = True
            for finding in findings:
                print(f"FINDING: {path}: {finding}", file=sys.stderr)
    if any_findings:
        return 1
    print(f"{args.file}: no forbidden claim content"
          + (f" ({scanned} files)" if scanned > 1 else ""))
    return 0


def _cmd_horizons(args: argparse.Namespace) -> int:
    event_class = policy.EventTimeClass(args.event_class)
    eligible = policy.eligible_horizons(
        event_class,
        event_uncertainty_seconds=args.uncertainty_seconds,
        observation_latency_seconds=args.observation_latency_seconds,
        processing_latency_seconds=args.processing_latency_seconds)
    print(json.dumps({
        "event_class": event_class.value,
        "eligible_horizons": eligible,
        "research_diagnostic_only": True,
    }, sort_keys=True))
    return 0


def _cmd_embargo(args: argparse.Namespace) -> int:
    embargo = policy.embargo_seconds(
        max_horizon_seconds=args.horizon_seconds,
        max_label_interval_seconds=args.label_interval_seconds,
        max_observation_latency_seconds=args.observation_latency_seconds,
        max_cascade_seconds=args.cascade_seconds)
    if embargo is None:
        print(json.dumps({
            "embargo_seconds": None,
            "blocked": True,
            "reason": "at least one embargo component is unknown or "
                      "invalid",
        }, sort_keys=True))
        return 1
    print(json.dumps({"embargo_seconds": embargo, "blocked": False},
                     sort_keys=True))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m nepal.research_v0.cli",
        description="Research-only validation surface for the v0 "
                    "science-design contracts; data actions are not "
                    "implemented here.")
    sub = parser.add_subparsers(dest="command", required=True)

    env = sub.add_parser(
        "validate-envelope",
        help="re-verify an envelope's flags, status, digests, and "
             "self-hash; re-hashes the canonical matrix/policy files "
             "recorded in the envelope and replays full record "
             "validation")
    env.add_argument("file")
    env.add_argument("--records-dir", default=None,
                     help="directory of <name>.json record payloads to "
                          "deserialize and re-validate")
    env.set_defaults(func=_cmd_validate_envelope)

    man = sub.add_parser(
        "verify-manifest",
        help="stdlib verifier: every manifest-listed file must exist "
             "with matching size and sha256; content/baseline heads "
             "must be bound")
    man.add_argument("file")
    man.set_defaults(func=_cmd_verify_manifest)

    scan = sub.add_parser(
        "claim-scan",
        help="scan a file for forbidden statuses, truthy authority "
             "flags, and operational phrases")
    scan.add_argument("file")
    scan.set_defaults(func=_cmd_claim_scan)

    hor = sub.add_parser(
        "horizons",
        help="list horizons admissible for an event-time precision class")
    hor.add_argument("--event-class", required=True,
                     choices=[c.value for c in policy.EventTimeClass])
    hor.add_argument("--uncertainty-seconds", type=_nonneg_finite,
                     required=True)
    hor.add_argument("--observation-latency-seconds",
                     type=_nonneg_finite, default=None)
    hor.add_argument("--processing-latency-seconds",
                     type=_nonneg_finite, default=None)
    hor.set_defaults(func=_cmd_horizons)

    emb = sub.add_parser(
        "embargo",
        help="compute the temporal embargo; blocked if any component is "
             "unknown or invalid")
    emb.add_argument("--horizon-seconds", type=_nonneg_finite,
                     default=None)
    emb.add_argument("--label-interval-seconds", type=_nonneg_finite,
                     default=None)
    emb.add_argument("--observation-latency-seconds",
                     type=_nonneg_finite, default=None)
    emb.add_argument("--cascade-seconds", type=_nonneg_finite,
                     default=None)
    emb.set_defaults(func=_cmd_embargo)

    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
