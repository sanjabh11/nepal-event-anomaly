#!/usr/bin/env python3
"""Validate an INCIDENT_SURFACE_V1 descriptor (G-10/G-26).

Incident-surface descriptors record coordinator/audit artifacts that are
deliberately EXCLUDED from the scientific release surface: quarantine
files, incident patches, and mutated-state preservation copies.  A valid
descriptor:

- declares ``schema`` == ``INCIDENT_SURFACE_V1``;
- declares ``main_release_excluded`` == true with a reason;
- lists every artifact with matching on-disk SHA-256 and byte size;
- carries a self sidecar ``<descriptor>.sha256`` whose digest matches the
  descriptor bytes.

Usage::

    validate_incident_surface.py --descriptor PATH [--json]

Exit codes: 0 = INCIDENT_SURFACE_OK, 1 = INCIDENT_SURFACE_FAIL,
2 = usage error.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

SCHEMA = "INCIDENT_SURFACE_V1"


class SurfaceError(ValueError):
    """A fail-closed incident-surface validation error."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validate_surface(descriptor_path: Path) -> dict:
    path = Path(descriptor_path).resolve()
    raw = path.read_bytes()
    doc = json.loads(raw.decode("utf-8"))
    problems: list[str] = []

    sidecar = Path(str(path) + ".sha256")
    if not sidecar.is_file():
        problems.append(f"self sidecar missing: {sidecar.name}")
    else:
        expected = sidecar.read_text(encoding="utf-8").split()[0]
        if expected != sha256_bytes(raw):
            problems.append("self sidecar digest mismatch")

    if doc.get("schema") != SCHEMA:
        problems.append(f"schema must be {SCHEMA}: {doc.get('schema')!r}")
    if doc.get("main_release_excluded") is not True:
        problems.append("main_release_excluded must be exactly true")
    if not doc.get("exclusion_reason"):
        problems.append("exclusion_reason is required")
    if not doc.get("generated_utc"):
        problems.append("generated_utc is required")

    entries = doc.get("entries")
    if not isinstance(entries, list) or not entries:
        problems.append("entries must be a non-empty list")
    else:
        seen = set()
        for i, entry in enumerate(entries):
            name = entry.get("name")
            if not name or name in seen:
                problems.append(f"entries[{i}].name missing/duplicate")
                continue
            seen.add(name)
            artifact = path.parent / name
            if not artifact.is_file():
                problems.append(f"artifact missing: {name}")
                continue
            data = artifact.read_bytes()
            if entry.get("sha256") != sha256_bytes(data):
                problems.append(f"artifact digest mismatch: {name}")
            if entry.get("bytes") != len(data):
                problems.append(f"artifact size mismatch: {name}")
            if not entry.get("purpose"):
                problems.append(f"artifact purpose missing: {name}")
        if len(entries) != len(seen):
            problems.append("duplicate entry names")

    status = "INCIDENT_SURFACE_FAIL" if problems else "INCIDENT_SURFACE_OK"
    return {"status": status, "descriptor": str(path),
            "problems": problems, "artifact_count":
            len(doc.get("entries", [])) if isinstance(
                doc.get("entries"), list) else 0}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Validate an INCIDENT_SURFACE_V1 descriptor.")
    parser.add_argument("--descriptor", required=True)
    args = parser.parse_args(argv)
    try:
        result = validate_surface(Path(args.descriptor))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(json.dumps({"status": "INCIDENT_SURFACE_FAIL",
                          "problems": [str(exc)]}, indent=2, sort_keys=True))
        return 1
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "INCIDENT_SURFACE_OK" else 1


if __name__ == "__main__":
    sys.exit(main())
