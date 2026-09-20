#!/usr/bin/env python3
"""Rebind ARTIFACT_MANIFEST_V0.json to a content commit.

Usage: rebind_manifest.py <content_commit_sha> [collection_count]

Recomputes every governed file entry from live bytes, adds newly
governed files, refreshes collection_guard, and sets
content_head/manifest_commit to the final content commit.  The
manifest itself is self-excluded (self_excluded: true); the result
is committed in a separate manifest-only commit.
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MANIFEST = ROOT / "docs/science/ARTIFACT_MANIFEST_V0.json"

NEW_GOVERNED = [
    "nepal/gitutil.py",
    "nepal/science_v0/seasonal_frame.py",
    "nepal/seismic_sidecar/one_station_contract.py",
    "scripts/run_seasonal_p5.py",
    "scripts/replay_seasonal_p5.py",
    "scripts/rebind_manifest.py",
    "tests/test_seasonal_lane.py",
    "tests/test_seasonal_frame_adversarial.py",
    "tests/test_one_station_contract.py",
    "docs/science/P5_EXTENSION_STATUS_V0.md",
]


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def main() -> int:
    head = sys.argv[1]
    count = sys.argv[2] if len(sys.argv) > 2 else None
    m = json.loads(MANIFEST.read_text())
    entries = {e["relpath"]: e for e in m["files"]}
    for rel in NEW_GOVERNED:
        if rel not in entries:
            entries[rel] = {"relpath": rel}
    files = []
    for rel in sorted(entries):
        p = ROOT / rel
        files.append({"relpath": rel, "sha256": sha(p),
                      "size_bytes": p.stat().st_size})
    m["files"] = files
    m["content_head"] = head
    m["manifest_commit"] = head
    tr = m["test_results"]
    if count:
        tr["collection_guard"] = re.sub(
            r"\(\d+ nodes?\)", f"({count} nodes)",
            tr["collection_guard"])
    MANIFEST.write_text(json.dumps(m, indent=1, sort_keys=True)
                        + "\n")
    print(f"rebound: {len(files)} files, head {head[:10]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
