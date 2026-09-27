#!/usr/bin/env python3
"""Coordinator watch loop for run-20260912-2a8757b.

Read-only integrity monitor. Appends one JSON line per cycle to watch_log.jsonl.
Never writes outside RUN_ROOT. Interval: 300s.
"""
import hashlib, json, os, subprocess, time

REPO = "/Users/sanjayb/nepal-event-anomaly"
RUN = os.path.join(REPO, ".phase-loop/parallel_runs/run-20260912-2a8757b")
LOG = os.path.join(RUN, "watch_log.jsonl")
INTERVAL = 300

FROZEN = {
    "preregistration.md": "0e7ce3c2e347a955f7495d719bb9232465ac9c25a5266656865800dbc963da7c",
    "data/gmm_false_positive_results.json": "fb711617194d41ed4f9b109f72c07558c22d247b6266b4dbb59f211a76cfd2bc",
    "data/locked_jja_events.json": "536125c44033cd6852e82c4718cca2a9138ca0f369e2cecf84de4c215af427eb",
    "data/features_nepal_jja_2001_2026.csv": "444f2ac0904757d67e68a0c8cbec8544a355a0a16c42ed7ffa86c2556a338041",
}
EXPECTED_HEAD = "8eeeccc60231a3aa2e55c14ce790fda3fea32bcd"
EXPECTED_DIFF = "5798d7fe99dc5feaf524fdac92e63c4c8f9c80eb9edb9a4ef4fa8e099a750586"
EXPECTED_STATUS_COUNT = 37
MANIFEST = "data/framework_inputs_v1_reconciled/manifest.json"
MANIFEST_SHA = "a20762d5cb9fce1ac2cafb9b8dcf3d24dea1c0c6dbffe4fa01ee509d802cad71"

def sh(cmd):
    try:
        return subprocess.run(cmd, shell=True, cwd=REPO, capture_output=True, text=True, timeout=60).stdout.strip()
    except Exception as e:
        return f"ERR:{e}"

def sha256(path):
    try:
        h = hashlib.sha256()
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
        return h.hexdigest()
    except Exception as e:
        return f"ERR:{e}"

def packet_activity():
    out = {}
    for lane in ("D1", "C1", "Q1"):
        d = os.path.join(RUN, "packets", lane)
        files = []
        for root, _, names in os.walk(d):
            for n in names:
                p = os.path.join(root, n)
                files.append((p, os.path.getmtime(p)))
        out[lane] = {"files": len(files), "newest_mtime": max((m for _, m in files), default=None)}
    return out

def main():
    while True:
        head = sh("git rev-parse HEAD")
        status_count = sh("git status --short | wc -l")
        diff_sha = sh("git diff | shasum -a 256 | cut -d' ' -f1")
        df = sh("df -k . | tail -1 | awk '{print $4}'")
        frozen_ok = {p: (sha256(os.path.join(REPO, p)) == exp) for p, exp in FROZEN.items()}
        manifest_sha = sha256(os.path.join(REPO, MANIFEST))
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "head": head,
            "head_ok": head == EXPECTED_HEAD,
            "status_count": int(status_count or 0),
            "status_count_ok": str(status_count) == str(EXPECTED_STATUS_COUNT),
            "diff_sha": diff_sha,
            "diff_ok": diff_sha == EXPECTED_DIFF,
            "disk_avail_kib": int(df) if df.isdigit() else None,
            "disk_guard_ok": bool(df.isdigit() and int(df) >= 5 * 1024 * 1024),
            "frozen_ok": frozen_ok,
            "manifest_sha_ok": manifest_sha == MANIFEST_SHA,
            "packets": packet_activity(),
        }
        with open(LOG, "a") as f:
            f.write(json.dumps(rec) + "\n")
        time.sleep(INTERVAL)

if __name__ == "__main__":
    main()
