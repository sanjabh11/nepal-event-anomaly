"""Fail-closed publication helpers for P5 evidence writers.

The governed evidence roots are append-only release surfaces.  These helpers
make the safe default explicit: a writer may create a new path atomically, but
it may never replace an existing evidence byte path.
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any


class ExistingEvidenceError(FileExistsError):
    """Raised when a publication would replace an existing evidence file."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _exclusive_temp(parent: Path, name: str) -> tuple[int, Path]:
    parent.mkdir(parents=True, exist_ok=True)
    fd, raw = tempfile.mkstemp(prefix=f".{name}.", suffix=".tmp",
                               dir=str(parent))
    return fd, Path(raw)


def write_once_bytes(path: Path, data: bytes) -> str:
    """Atomically create *path* and its bytes, refusing replacement.

    A hard-link publication makes the destination creation atomic and fails if
    another writer created the destination after the initial existence check.
    """
    path = Path(path)
    if path.exists():
        raise ExistingEvidenceError(f"refusing to replace existing evidence: {path}")
    fd, tmp = _exclusive_temp(path.parent, path.name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        try:
            os.link(tmp, path)
        except FileExistsError as exc:
            raise ExistingEvidenceError(
                f"concurrent writer created evidence path: {path}") from exc
        finally:
            try:
                tmp.unlink()
            except FileNotFoundError:
                pass
    except BaseException:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        raise
    return sha256_bytes(data)


def write_once_text(path: Path, text: str) -> str:
    return write_once_bytes(Path(path), text.encode("utf-8"))


def write_once_json(path: Path, value: Any, *, indent: int = 2,
                    sort_keys: bool = True) -> str:
    text = json.dumps(value, indent=indent, sort_keys=sort_keys) + "\n"
    return write_once_text(Path(path), text)


def write_once_sidecar(path: Path) -> str:
    path = Path(path)
    digest = sha256_bytes(path.read_bytes())
    sidecar = Path(str(path) + ".sha256")
    write_once_text(sidecar, f"{digest}  {path.name}\n")
    return digest
