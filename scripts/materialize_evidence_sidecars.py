#!/usr/bin/env python3
"""Create missing sha256 sidecars for existing evidence payloads.

This is a one-way, fail-closed migration helper for the P5 audit-3 release
surface.  It never replaces a payload or an existing sidecar.  Existing
sidecars are checked against their payload bytes before any missing sidecar
is created.
"""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from p5_safe_io import write_once_sidecar


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def materialize(root: Path) -> dict:
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"evidence root is not a directory: {root}")
    payloads = sorted(
        path for path in root.rglob("*")
        if path.is_file() and not path.name.endswith(".sha256"))
    invalid = []
    missing = []
    for payload in payloads:
        sidecar = Path(str(payload) + ".sha256")
        digest = _sha256(payload)
        if sidecar.exists():
            tokens = sidecar.read_bytes().split()
            if not tokens or tokens[0].decode("utf-8", "replace") != digest:
                invalid.append(str(sidecar.relative_to(root)))
        else:
            missing.append((payload, digest))
    if invalid:
        raise ValueError("existing sidecars do not bind live payloads: "
                         + ", ".join(sorted(invalid)))
    created = []
    for payload, digest in missing:
        # Recompute inside the publication helper's read path and keep the
        # exclusive-create guarantee for concurrent coordinators.
        write_once_sidecar(payload)
        created.append(str(payload.relative_to(root)))
    return {"root": str(root), "payload_files": len(payloads),
            "existing_sidecars": len(payloads) - len(missing),
            "created_sidecars": len(created), "created": created}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Create missing P5 evidence sidecars without replacing bytes")
    parser.add_argument("--root", action="append", required=True,
                        help="evidence root; repeat for each governed root")
    args = parser.parse_args(argv)
    try:
        reports = [materialize(Path(root)) for root in args.root]
    except (OSError, ValueError) as exc:
        print(f"SIDECAR_MATERIALIZATION_FAIL: {exc}")
        return 1
    import json
    print(json.dumps({"status": "SIDECARS_OK", "roots": reports},
                     indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
