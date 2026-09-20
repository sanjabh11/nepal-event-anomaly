from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from p5_safe_io import ExistingEvidenceError, write_once_bytes, write_once_json


def test_write_once_is_atomic_and_refuses_replacement(tmp_path):
    target = tmp_path / "retrieval" / "artifact.json"
    digest = write_once_bytes(target, b"first\n")
    assert target.read_bytes() == b"first\n"
    assert len(digest) == 64
    with pytest.raises(ExistingEvidenceError):
        write_once_bytes(target, b"second\n")
    assert target.read_bytes() == b"first\n"
    assert not list(target.parent.glob(".*.tmp"))


def test_write_once_json_creates_parent_and_canonical_new_file(tmp_path):
    target = tmp_path / "run" / "receipt.json"
    write_once_json(target, {"b": 2, "a": 1})
    assert target.read_text() == '{\n  "a": 1,\n  "b": 2\n}\n'
