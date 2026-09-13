"""Ranked-payload digest domain contract (F1).

The delta/I1 metadata rebinds stamped ``ranked_payload_sha256`` with a
composite digest over ``{ranked, loo_top5, top_five, gate}``.  The field name
suggested the digest covered the ``ranked`` array alone, whose canonical
SHA-256 is different — a Tranche-1 pilot detected the discrepancy and could
not recover the preimage domain.

This module establishes an explicit two-field contract so the domains can
never again be conflated:

* ``ranked_array_canonical_sha256`` — always
  ``sha256_canonical(payload["ranked"])``, stamped on every B result and on
  every rebind;
* ``ranked_payload_sha256`` — preserved verbatim (never overwritten), with
  the mandatory sibling ``ranked_payload_sha256_domain_status``:

  - ``MATCHES_RANKED_ARRAY_CANONICAL`` — the digest equals the ranked
    array's canonical hash;
  - ``HISTORICAL_ORPHAN_UNKNOWN_DOMAIN`` — the digest exists but is provably
    not the ranked-array canonical hash (e.g. a composite payload digest
    whose exact preimage domain is unrecoverable);
  - ``MISSING`` — no ``ranked_payload_sha256`` is declared.

The verifier is fail-closed: a declared ``MATCHES`` status that does not
recompute equal is rejected, as is an ``ORPHAN`` claim on a digest that
actually matches, and any digest without a domain status.
"""
from __future__ import annotations

from typing import Any, Mapping

from .provenance import sha256_canonical

DOMAIN_MATCHES = "MATCHES_RANKED_ARRAY_CANONICAL"
DOMAIN_ORPHAN = "HISTORICAL_ORPHAN_UNKNOWN_DOMAIN"
DOMAIN_MISSING = "MISSING"
DOMAIN_STATUSES = (DOMAIN_MATCHES, DOMAIN_ORPHAN, DOMAIN_MISSING)

RANKED_CANONICAL_FIELD = "ranked_array_canonical_sha256"
RANKED_PAYLOAD_FIELD = "ranked_payload_sha256"
DOMAIN_STATUS_FIELD = "ranked_payload_sha256_domain_status"


def ranked_array_canonical_sha256(ranked: Any) -> str:
    """Canonical SHA-256 of the ranked array alone — the only digest a
    consumer should treat as binding the visible ranked bytes."""
    return sha256_canonical(ranked)


def stamp_ranked_digests(payload: dict[str, Any]) -> dict[str, Any]:
    """Stamp the ranked-digest contract fields on a B-shaped payload.

    Sets ``ranked_array_canonical_sha256`` from the payload's ``ranked``
    bytes and derives ``ranked_payload_sha256_domain_status`` from any
    declared ``ranked_payload_sha256``.  The declared digest is NEVER
    modified — only its domain classification is recorded.
    """
    ranked = payload.get("ranked")
    if ranked is None:
        raise ValueError("cannot stamp ranked digests: 'ranked' is absent")
    canonical = ranked_array_canonical_sha256(ranked)
    payload[RANKED_CANONICAL_FIELD] = canonical
    declared = payload.get(RANKED_PAYLOAD_FIELD)
    if declared is None:
        payload[DOMAIN_STATUS_FIELD] = DOMAIN_MISSING
    elif declared == canonical:
        payload[DOMAIN_STATUS_FIELD] = DOMAIN_MATCHES
    else:
        payload[DOMAIN_STATUS_FIELD] = DOMAIN_ORPHAN
    return payload


def verify_ranked_digests(payload: Any) -> tuple[bool, list[str]]:
    """Fail-closed verification of the ranked-digest contract.

    When ``ranked`` is present, a recorded ``ranked_array_canonical_sha256``
    must recompute equal.  A declared ``ranked_payload_sha256`` requires a
    domain status; ``MATCHES`` must recompute equal to the ranked canonical
    hash, and ``ORPHAN`` must recompute unequal — both directions are lies
    if violated.  ``MISSING`` is valid only when no digest is declared.
    """
    problems: list[str] = []
    if not isinstance(payload, Mapping):
        return False, ["payload must be a mapping"]

    ranked = payload.get("ranked")
    canonical: str | None = None
    if ranked is not None:
        canonical = ranked_array_canonical_sha256(ranked)
        recorded = payload.get(RANKED_CANONICAL_FIELD)
        if recorded is not None and recorded != canonical:
            problems.append(
                f"{RANKED_CANONICAL_FIELD} {recorded!r} does not recompute "
                f"to the ranked array canonical hash {canonical!r}")

    declared = payload.get(RANKED_PAYLOAD_FIELD)
    status = payload.get(DOMAIN_STATUS_FIELD)

    if declared is not None and status is None:
        problems.append(
            f"{RANKED_PAYLOAD_FIELD} declared without "
            f"{DOMAIN_STATUS_FIELD}; digest domain is ambiguous")
    if status is not None and status not in DOMAIN_STATUSES:
        problems.append(f"{DOMAIN_STATUS_FIELD} {status!r} is not a known "
                        f"domain status")
    if declared is None and status in (DOMAIN_MATCHES, DOMAIN_ORPHAN):
        problems.append(
            f"{DOMAIN_STATUS_FIELD} {status!r} requires a declared "
            f"{RANKED_PAYLOAD_FIELD}")

    if canonical is not None and isinstance(declared, str):
        if status == DOMAIN_MATCHES and declared != canonical:
            problems.append(
                f"{DOMAIN_STATUS_FIELD} claims {DOMAIN_MATCHES} but "
                f"{RANKED_PAYLOAD_FIELD} {declared!r} does not equal the "
                f"ranked array canonical hash {canonical!r}")
        if status == DOMAIN_ORPHAN and declared == canonical:
            problems.append(
                f"{DOMAIN_STATUS_FIELD} claims {DOMAIN_ORPHAN} but "
                f"{RANKED_PAYLOAD_FIELD} equals the ranked array canonical "
                "hash — the digest is not orphaned")
    return (not problems), problems
