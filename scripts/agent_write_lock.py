#!/usr/bin/env python3
"""File write-lease coordinator for multi-agent worktrees.

Implements the MULTI_AGENT_WRITE_COORDINATION_PROTOCOL_V2 contract:

- ``acquire`` takes an exclusive lease on a repository-relative file
  for an agent id with a TTL (seconds); the lease is a small JSON
  record under ``.write-leases/`` created atomically
  (``O_CREAT | O_EXCL``) — two racing acquirers cannot both win.
- ``release`` drops the lease (only the holder may release; the
  ``--force`` flag exists for coordinator cleanup of dead leases).
- ``status`` reports the current lease state.
- ``verify-freeze`` asserts a frozen file is byte-identical to a
  recorded freeze digest — a post-freeze write is detected, never
  silently overwritten.

Leases are advisory: writers must check them.  A held lease does not
prevent writes — it prevents SURPRISE writes, which is what the V2
protocol requires.  Expired leases self-evict on the next access.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

LEASE_DIR = ".write-leases"
FREEZE_DIR = ".write-freezes"


def _repo_root() -> Path:
    p = Path(__file__).resolve().parent.parent
    return p


def _lease_path(root: Path, relpath: str) -> Path:
    key = hashlib.sha256(relpath.encode()).hexdigest()[:24]
    return root / LEASE_DIR / f"{key}.json"


def _freeze_path(root: Path, relpath: str) -> Path:
    key = hashlib.sha256(relpath.encode()).hexdigest()[:24]
    return root / FREEZE_DIR / f"{key}.json"


def _read_lease(root: Path, relpath: str) -> dict | None:
    lp = _lease_path(root, relpath)
    try:
        data = json.loads(lp.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("expires", 0) < time.time():
        try:
            lp.unlink()
        except OSError:
            pass
        return None
    return data


def _norm(root: Path, relpath: str) -> str:
    p = Path(relpath)
    if p.is_absolute():
        try:
            return str(p.resolve().relative_to(root.resolve()))
        except ValueError:
            return str(p)
    return str(p)


def cmd_acquire(args) -> int:
    root = _repo_root()
    rel = _norm(root, args.file)
    existing = _read_lease(root, rel)
    if existing is not None:
        if existing["agent"] == args.agent:
            print(f"lease already held by {args.agent} on {rel}")
            return 0
        print(f"LEASE DENIED: {rel} is held by {existing['agent']} "
              f"until {existing['expires']:.0f}", file=sys.stderr)
        return 2
    (root / LEASE_DIR).mkdir(exist_ok=True)
    lease = {
        "agent": args.agent, "file": rel,
        "acquired": time.time(),
        "expires": time.time() + args.ttl,
        "ttl_seconds": args.ttl}
    lp = _lease_path(root, rel)
    try:
        fd = os.open(str(lp), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        with os.fdopen(fd, "w") as fh:
            json.dump(lease, fh)
    except FileExistsError:
        print(f"LEASE DENIED: {rel} was acquired concurrently",
              file=sys.stderr)
        return 2
    print(f"lease acquired: {args.agent} -> {rel} "
          f"(ttl {args.ttl}s)")
    return 0


def cmd_release(args) -> int:
    root = _repo_root()
    rel = _norm(root, args.file)
    existing = _read_lease(root, rel)
    lp = _lease_path(root, rel)
    if existing is None:
        print(f"no lease held on {rel}")
        return 0
    if existing["agent"] != args.agent and not args.force:
        print(f"RELEASE DENIED: {rel} is held by "
              f"{existing['agent']}, not {args.agent}",
              file=sys.stderr)
        return 2
    try:
        lp.unlink()
    except OSError as exc:
        print(f"release failed: {exc}", file=sys.stderr)
        return 1
    print(f"lease released: {rel}")
    return 0


def cmd_status(args) -> int:
    root = _repo_root()
    rel = _norm(root, args.file) if args.file else None
    lease_root = root / LEASE_DIR
    found = False
    if rel:
        existing = _read_lease(root, rel)
        if existing:
            print(json.dumps(existing, indent=2))
            found = True
    elif lease_root.is_dir():
        for lp in sorted(lease_root.glob("*.json")):
            data = _read_lease(
                root, json.loads(lp.read_text())["file"])
            if data:
                print(json.dumps(data))
                found = True
    if not found:
        print("no active leases")
    return 0


def cmd_freeze(args) -> int:
    root = _repo_root()
    rel = _norm(root, args.frozen_file)
    target = root / rel
    if not target.is_file():
        print(f"freeze target {rel} does not exist", file=sys.stderr)
        return 1
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    (root / FREEZE_DIR).mkdir(exist_ok=True)
    rec = {"file": rel, "sha256": digest, "frozen_at": time.time(),
           "agent": args.agent}
    _freeze_path(root, rel).write_text(json.dumps(rec))
    print(f"freeze recorded: {rel} @ {digest[:16]}...")
    return 0


def cmd_verify_freeze(args) -> int:
    root = _repo_root()
    rel = _norm(root, args.frozen_file)
    fp = _freeze_path(root, rel)
    try:
        rec = json.loads(fp.read_text())
    except (OSError, json.JSONDecodeError):
        print(f"FREEZE MISSING: no freeze record for {rel}",
              file=sys.stderr)
        return 2
    target = root / rel
    if not target.is_file():
        print(f"FREEZE VIOLATED: {rel} was deleted after freeze",
              file=sys.stderr)
        return 2
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if digest != rec["sha256"]:
        print(f"FREEZE VIOLATED: {rel} changed after freeze — "
              f"expected {rec['sha256'][:16]}..., got "
              f"{digest[:16]}...", file=sys.stderr)
        return 2
    print(f"freeze verified: {rel}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("acquire")
    p.add_argument("--agent", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--ttl", type=int, default=600)
    p.set_defaults(fn=cmd_acquire)

    p = sub.add_parser("release")
    p.add_argument("--agent", required=True)
    p.add_argument("--file", required=True)
    p.add_argument("--force", action="store_true")
    p.set_defaults(fn=cmd_release)

    p = sub.add_parser("status")
    p.add_argument("--file", default=None)
    p.set_defaults(fn=cmd_status)

    p = sub.add_parser("freeze")
    p.add_argument("--frozen-file", required=True)
    p.add_argument("--agent", default="coordinator")
    p.set_defaults(fn=cmd_freeze)

    p = sub.add_parser("verify-freeze")
    p.add_argument("--frozen-file", required=True)
    p.set_defaults(fn=cmd_verify_freeze)

    args = ap.parse_args()
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
