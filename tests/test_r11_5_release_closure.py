"""R11.5 release-closure regressions — the three release-proof gates:

1. **stale manifest_commit** — ``manifest_commit`` must name the FINAL
   content commit: ``manifest_commit == content_head`` (P0_BASELINE_LEDGER:
   "content commit(s), then one manifest-only rebind; ``content_head`` and
   ``manifest_commit`` name the final content commit"). It must never
   retain an older round's value, and it must never be the manifest-only
   rebind commit itself.
2. **missing lane test from manifest** — every R11.5 real-path lane file
   must be a governed manifest member.
3. **collection count mismatch** — the live repo-wide collection must
   equal the manifest's ``collection_guard`` node count, adjusted only by
   this closure file's own tests until the manifest-only rebind governs
   the file (after rebind the adjustment must be zero — the guard asserts
   3061 today, 3064 once this file is bound, and any drift trips it).
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
_MANIFEST = _REPO_ROOT / "docs/science/ARTIFACT_MANIFEST_V0.json"
_LANE_FILES = (
    "tests/test_p5_glof_intake.py",
    "tests/test_real_fmx_audit.py",
    "tests/test_glof_poc_real_path.py",
    "tests/test_regime_real_path.py")
_CLOSURE_RELPATH = "tests/test_r11_5_release_closure.py"
_COLLECTION_RE = re.compile(r"(\d+) tests collected")
#: The number of test functions in THIS module.  The manifest's
#: collection_guard predates this file, so the live expected repo count
#: = manifest count + this module's own tests while the file is ungoverned.
#: The manifest-only rebind adds the file and re-records collection_guard,
#: after which the adjustment is zero and any drift trips the gate.
_OWN_TEST_COUNT = 4


def _manifest():
    return json.loads(_MANIFEST.read_text(encoding="utf-8"))


class TestR115ReleaseClosure:
    def test_manifest_commit_names_the_final_content_commit(self):
        m = _manifest()
        assert m["manifest_commit"] == m["content_head"], (
            f"manifest_commit {m['manifest_commit']} != content_head "
            f"{m['content_head']} — the manifest no longer names the "
            "content commit it binds (stale manifest-only rebind)")

    def test_r115_lane_tests_are_governed_manifest_members(self):
        m = _manifest()
        relpaths = {e["relpath"] for e in m["files"]}
        missing = [p for p in _LANE_FILES if p not in relpaths]
        assert missing == [], (
            "R11.5 lane tests missing from the manifest: "
            + ", ".join(missing))

    def test_cli_rejects_stale_manifest_commit(self, tmp_path):
        """R11.5-1 — the verify-manifest CLI independently enforces
        manifest_commit == content_head; a stale or manifest-only
        rebind value must fail closed."""
        import argparse
        import hashlib
        from nepal.research_v0.cli import _cmd_verify_manifest
        root = tmp_path / "a" / "b"
        root.mkdir(parents=True)
        (tmp_path / "f.txt").write_bytes(b"x")
        good = {"content_head": "a" * 40,
                "baseline_head": "b" * 40,
                "manifest_commit": "a" * 40,
                "test_results": {"research_v0": "ok"},
                "files": [{"relpath": "f.txt",
                           "sha256": hashlib.sha256(b"x").hexdigest(),
                           "size_bytes": 1}]}
        mp = root / "m.json"
        mp.write_text(json.dumps(good), encoding="utf-8")
        assert _cmd_verify_manifest(
            argparse.Namespace(file=str(mp))) == 0
        stale = dict(good, manifest_commit="c" * 40)
        mp.write_text(json.dumps(stale), encoding="utf-8")
        assert _cmd_verify_manifest(
            argparse.Namespace(file=str(mp))) == 1

    def test_collection_count_matches_manifest(self):
        m = _manifest()
        guard = m["test_results"]["collection_guard"]
        match = re.search(r"\((\d+) nodes?\)", guard)
        assert match, f"collection_guard format unrecognized: {guard!r}"
        manifest_count = int(match.group(1))
        governed = _CLOSURE_RELPATH in {
            e["relpath"] for e in m["files"]}
        expected = manifest_count + (0 if governed else _OWN_TEST_COUNT)
        proc = subprocess.run(
            [sys.executable, "-B", "-m", "pytest", "tests/",
             "--collect-only", "-q"],
            cwd=_REPO_ROOT, capture_output=True, text=True, check=False)
        assert proc.returncode == 0, proc.stderr[-2000:]
        live = int(_COLLECTION_RE.search(proc.stdout).group(1))
        assert live == expected, (
            f"repo-wide collection {live} != expected {expected} "
            f"(manifest collection_guard: {guard!r})")