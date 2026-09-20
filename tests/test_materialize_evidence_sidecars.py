from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from materialize_evidence_sidecars import materialize


def test_materialize_creates_missing_sidecar_without_replacing_payload(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    payload = root / "nested" / "payload.json"
    payload.parent.mkdir()
    payload.write_bytes(b"payload\n")
    before = payload.read_bytes()
    report = materialize(root)
    assert report["created_sidecars"] == 1
    assert payload.read_bytes() == before
    assert payload.with_name(payload.name + ".sha256").read_text().split()[0]


def test_materialize_refuses_stale_existing_sidecar(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    payload = root / "payload.bin"
    payload.write_bytes(b"payload")
    payload.with_name(payload.name + ".sha256").write_text("0" * 64 +
                                                        "  payload.bin\n")
    with pytest.raises(ValueError, match="existing sidecars"):
        materialize(root)
