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
    for name in ("approved_by", "approved_at"):
        if not str(envelope.get(name) or "").strip():
            problems.append(f"{name} must be present and non-empty")
    digests = envelope.get("record_digests")
    if not isinstance(digests, dict) or not all(
            isinstance(v, str) and gates.SHA256_RE.match(v)
            for v in digests.values()):
        problems.append("record_digests must map names to 64-hex "
                        "digests")
    if problems:
        for problem in problems:
            print(f"BLOCKED: {problem}", file=sys.stderr)
        return 1
    print(f"envelope {args.file} satisfies research-only claim checks "
          "and self-hash verification")
    return 0


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r} rejected")


def _cmd_claim_scan(args: argparse.Namespace) -> int:
    try:
        with open(args.file, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        print(f"BLOCKED: cannot read {args.file}: {exc}",
              file=sys.stderr)
        return 1
    findings = gates.scan_claims_text(text)
    if findings:
        for finding in findings:
            print(f"FINDING: {finding}", file=sys.stderr)
        return 1
    print(f"{args.file}: no forbidden claim content")
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
             "self-hash")
    env.add_argument("file")
    env.set_defaults(func=_cmd_validate_envelope)

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
