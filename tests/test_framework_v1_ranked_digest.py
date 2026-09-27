"""F1 ranked-payload digest domain tests.

The delta/I1 rebinds declared ``ranked_payload_sha256 = d056…``, a composite
digest over ``{ranked, loo_top5, top_five, gate}`` — NOT the canonical hash of
the ``ranked`` array itself (``a4e6…``).  The field name misled consumers.
F1 introduces an explicit two-field contract:

* ``ranked_array_canonical_sha256`` — always ``sha256_canonical(ranked)``;
* ``ranked_payload_sha256`` — preserved verbatim — plus sibling
  ``ranked_payload_sha256_domain_status`` in
  {MATCHES_RANKED_ARRAY_CANONICAL, HISTORICAL_ORPHAN_UNKNOWN_DOMAIN, MISSING}.

The verifier fails closed on a false MATCH claim and on an orphan claim for a
digest that actually matches.  Existing digests are never overwritten.
"""
from __future__ import annotations

import copy

import pytest

from nepal.framework_v1 import ranked_digest as rd
from nepal.framework_v1.provenance import sha256_canonical


def _payload(n=3):
    return {"ranked": [{"analysis_unit_id": f"AU-E{i:04d}-N000{i}",
                        "priority_index": 0.9 - i * 0.01}
                       for i in range(n)]}


class TestCanonicalStamping:
    def test_ranked_array_canonical_sha256_computed(self):
        p = _payload()
        assert rd.ranked_array_canonical_sha256(p["ranked"]) == \
            sha256_canonical(p["ranked"])

    def test_stamp_adds_canonical_field(self):
        p = rd.stamp_ranked_digests(_payload())
        assert p["ranked_array_canonical_sha256"] == \
            sha256_canonical(p["ranked"])

    def test_stamp_missing_declared_marks_missing(self):
        p = rd.stamp_ranked_digests(_payload())
        assert p["ranked_payload_sha256_domain_status"] == \
            rd.DOMAIN_MISSING

    def test_stamp_matching_declared_marks_matches(self):
        p = _payload()
        p["ranked_payload_sha256"] = sha256_canonical(p["ranked"])
        p = rd.stamp_ranked_digests(p)
        assert p["ranked_payload_sha256_domain_status"] == \
            rd.DOMAIN_MATCHES

    def test_stamp_orphan_declared_preserved_verbatim(self):
        """A non-matching declared digest is preserved, never overwritten."""
        p = _payload()
        p["ranked_payload_sha256"] = "d0" * 32
        p = rd.stamp_ranked_digests(p)
        assert p["ranked_payload_sha256"] == "d0" * 32
        assert p["ranked_payload_sha256_domain_status"] == \
            rd.DOMAIN_ORPHAN

    def test_stamp_never_mutates_ranked_bytes(self):
        p = _payload()
        before = copy.deepcopy(p["ranked"])
        rd.stamp_ranked_digests(p)
        assert p["ranked"] == before


class TestVerifier:
    def test_verify_clean_payload(self):
        p = rd.stamp_ranked_digests(_payload())
        ok, problems = rd.verify_ranked_digests(p)
        assert ok, problems

    def test_false_match_claim_fails_closed(self):
        p = _payload()
        p["ranked_payload_sha256"] = "ff" * 32
        p["ranked_payload_sha256_domain_status"] = rd.DOMAIN_MATCHES
        p["ranked_array_canonical_sha256"] = sha256_canonical(p["ranked"])
        ok, problems = rd.verify_ranked_digests(p)
        assert not ok
        assert any("MATCHES" in p or "match" in p for p in problems)

    def test_orphan_claim_on_matching_digest_rejected(self):
        """Claiming ORPHAN for a digest that equals the canonical is a lie."""
        p = _payload()
        p["ranked_payload_sha256"] = sha256_canonical(p["ranked"])
        p["ranked_array_canonical_sha256"] = sha256_canonical(p["ranked"])
        p["ranked_payload_sha256_domain_status"] = rd.DOMAIN_ORPHAN
        ok, problems = rd.verify_ranked_digests(p)
        assert not ok

    def test_digest_without_domain_status_rejected(self):
        p = _payload()
        p["ranked_payload_sha256"] = "d0" * 32
        p["ranked_array_canonical_sha256"] = sha256_canonical(p["ranked"])
        ok, problems = rd.verify_ranked_digests(p)
        assert not ok
        assert any("domain_status" in p for p in problems)

    def test_status_without_digest_only_valid_as_missing(self):
        p = _payload()
        p["ranked_array_canonical_sha256"] = sha256_canonical(p["ranked"])
        p["ranked_payload_sha256_domain_status"] = rd.DOMAIN_ORPHAN
        ok, problems = rd.verify_ranked_digests(p)
        assert not ok

    def test_stale_canonical_field_detected(self):
        p = rd.stamp_ranked_digests(_payload())
        p["ranked"][0]["priority_index"] = 0.01  # tamper
        ok, problems = rd.verify_ranked_digests(p)
        assert not ok
        assert any("canonical" in p for p in problems)

    def test_unknown_status_rejected(self):
        p = _payload()
        p["ranked_payload_sha256"] = "d0" * 32
        p["ranked_payload_sha256_domain_status"] = "SOMEWHERE_OVER_THERE"
        ok, problems = rd.verify_ranked_digests(p)
        assert not ok

    def test_orphan_status_accepted_for_unknown_domain(self):
        """The real F1 case: d056 is a composite-domain digest, not the
        ranked array — HISTORICAL_ORPHAN_UNKNOWN_DOMAIN is honest."""
        p = _payload()
        p["ranked_payload_sha256"] = sha256_canonical(
            {"ranked": p["ranked"], "extra": "composite"})
        p = rd.stamp_ranked_digests(p)
        assert p["ranked_payload_sha256_domain_status"] == rd.DOMAIN_ORPHAN
        ok, problems = rd.verify_ranked_digests(p)
        assert ok, problems
