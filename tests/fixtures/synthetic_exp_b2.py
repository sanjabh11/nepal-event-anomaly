"""Deterministic synthetic fixtures for the B2 vintage-adapter tests.

Every value here is fabricated: no timestamp corresponds to a real
issue or retrieval event, no digest corresponds to real bytes, and no
provider string asserts anything about a real archive.  These payloads
exercise the metadata-admission contract only — they are never a
substitute for governed intake evidence.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any

_INIT = datetime(2020, 6, 1, 0, 0, 0, tzinfo=timezone.utc)
_ISSUE = _INIT + timedelta(hours=6)
_VALID_START = _INIT + timedelta(days=1)
_VALID_END = _VALID_START + timedelta(hours=6)
# issue + 48 h satisfies the TIGGE/S2S-default floor exactly.
_ARCHIVE = _ISSUE + timedelta(hours=48)
_RETRIEVAL = _ARCHIVE + timedelta(hours=18)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def synthetic_vintage_request(
        provider: str = "tigge", **overrides: Any) -> dict[str, Any]:
    """A contract-valid VintageRequest-shaped kwargs dict.

    Defaults satisfy the strictest registry floor (TIGGE 48 h): issue
    at init+6 h, archive at issue+48 h, retrieval after archive.
    ``overrides`` are applied last so tests can inject exactly one
    defect per case.
    """
    request: dict[str, Any] = {
        "provider": provider,
        "centre": "",
        "data_class": "ARCHIVED_OPERATIONAL",
        "model_version": "synthetic-model-v0",
        "cycle": "00",
        "initialization_time": _iso(_INIT),
        "issue_time": _iso(_ISSUE),
        "valid_start": _iso(_VALID_START),
        "valid_end": _iso(_VALID_END),
        "archive_availability": _iso(_ARCHIVE),
        "local_retrieval_time": _iso(_RETRIEVAL),
        "license_id": "synthetic-license-v0",
        "archive_mechanism": "synthetic-archive-api",
        "archive_payload_sha256": _sha(f"{provider}:payload-bytes"),
        "retrieval_record_sha256": _sha(f"{provider}:retrieval-record"),
        "archive_payload_path": f"evidence/{provider}/payload.bin",
        "retrieval_record_path": f"evidence/{provider}/retrieval.json",
        "evidence_root": "",
        "declared_delay_seconds": -1.0,
    }
    request.update(overrides)
    return request


__all__ = ["synthetic_vintage_request"]
