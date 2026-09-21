"""P5-IDX: validator for the P5_EVIDENCE_INDEX_V1 cross-root index.

The audited v0 index carried absolute host paths in ``files[]``,
stale digests, no provenance, and no automated check.  These tests
build fully synthetic roots in ``tmp_path`` — real evidence roots are
never touched — and pin both halves of the contract: a well-formed
index passes end-to-end (digests, sizes, sidecars, supersedes chain)
and every defect class fails closed.
"""
import hashlib
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]
                     / "scripts"))

import generate_evidence_index_v2 as gei
import validate_evidence_index as vei


# ------------------------------------------------------------------
# synthetic fixture builders
# ------------------------------------------------------------------

def _sha(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _write_payload(root: Path, relpath: str, data: bytes,
                   sidecar: bool = True) -> dict:
    """Write a payload file (+ optional sha256sum-format sidecar) under
    a synthetic root and return its index metadata."""
    f = root / relpath
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(data)
    meta = {"sha256": _sha(data), "size_bytes": len(data),
            "sidecar_sha256": None}
    if sidecar:
        sc = root / (relpath + ".sha256")
        sc.write_text(f"{meta['sha256']}  {f.name}\n",
                      encoding="utf-8")
        meta["sidecar_sha256"] = _sha(sc.read_bytes())
    return meta


def _entry(root_id, relpath, meta, state="current",
           activity="unit-test generator", entity_role="evidence"):
    return {"root_id": root_id, "relpath": relpath,
            "sha256": meta["sha256"], "size_bytes": meta["size_bytes"],
            "sidecar_sha256": meta["sidecar_sha256"],
            "state": state, "activity": activity,
            "entity_role": entity_role}


def _index_doc(files, roots, **overrides):
    doc = {
        "schema": "P5_EVIDENCE_INDEX_V1",
        "title": "synthetic test index",
        "generated_utc": "2026-09-21T00:00:00Z",
        "generator": {"agent": "pytest", "repo_commit": "a" * 40},
        "manifest_sha256": "b" * 64,
        "roots": roots,
        "files": files,
        "claim_scope": "research_only_no_operational_authorization",
    }
    doc.update(overrides)
    return doc


@pytest.fixture
def env(tmp_path):
    """One root, one verified file, one index — all under tmp_path."""
    root = tmp_path / "evidence_root"
    root.mkdir()
    meta = _write_payload(root, "retrieval/a.json", b'{"a": 1}')
    files = [_entry("r1", "retrieval/a.json", meta)]
    roots = {"r1": {"path": str(root), "role": "primary"}}
    doc = _index_doc(files, roots)
    index = tmp_path / "p5_evidence_index_v1.json"
    index.write_text(json.dumps(doc), encoding="utf-8")
    return {"root": root, "roots": roots, "files": files,
            "meta": meta, "doc": doc, "index": index}


def _write_index(tmp_path, doc) -> Path:
    p = tmp_path / "p5_evidence_index_v1.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    return p


def _problems(report):
    return "\n".join(report["problems"])


# ------------------------------------------------------------------
# happy path + CLI surface
# ------------------------------------------------------------------

def test_valid_index_passes(env):
    report = vei.validate_index(env["index"])
    assert report["status"] == "INDEX_OK"
    assert report["problems"] == []
    assert report["files_checked"] == 1
    assert report["files_ok"] == 1


def test_cli_exit_zero_and_json_stdout(env, capsys):
    rc = vei.main([str(env["index"])])
    out = json.loads(capsys.readouterr().out)
    assert rc == 0
    assert out["status"] == "INDEX_OK"
    assert {"status", "problems", "files_checked",
            "files_ok"} <= set(out)


def test_cli_exit_one_on_failure(env, capsys):
    env["doc"]["schema"] = "P5_EVIDENCE_INDEX_V0"
    env["index"].write_text(json.dumps(env["doc"]), encoding="utf-8")
    rc = vei.main([str(env["index"])])
    assert rc == 1
    out = json.loads(capsys.readouterr().out)
    assert out["status"] == "INDEX_FAIL"


def test_report_out_written(env, tmp_path, capsys):
    out_path = tmp_path / "report.json"
    rc = vei.main([str(env["index"]), "--report-out", str(out_path)])
    assert rc == 0
    saved = json.loads(out_path.read_text(encoding="utf-8"))
    assert saved["status"] == "INDEX_OK"


def test_root_map_override(env, tmp_path):
    """--root-map supplies real locations when roots.*.path is a stale
    host path from another machine."""
    env["doc"]["roots"]["r1"]["path"] = "/nonexistent/host/path"
    _write_index(tmp_path, env["doc"])
    env["index"].write_text(json.dumps(env["doc"]), encoding="utf-8")
    # Without override: the unresolvable root fails.
    report = vei.validate_index(env["index"])
    assert report["status"] == "INDEX_FAIL"
    # With override pointing at the real root: passes.
    report = vei.validate_index(env["index"],
                                root_map={"r1": str(env["root"])})
    assert report["status"] == "INDEX_OK", report["problems"]


# ------------------------------------------------------------------
# schema / top-level failures
# ------------------------------------------------------------------

def test_wrong_schema_rejected(env, tmp_path):
    env["doc"]["schema"] = "P5_EVIDENCE_INDEX_V0"
    report = vei.validate_index(_write_index(tmp_path, env["doc"]))
    assert report["status"] == "INDEX_FAIL"
    assert "schema" in _problems(report)


def test_missing_required_top_level_field(env, tmp_path):
    for field in ("generated_utc", "generator", "manifest_sha256",
                  "roots", "files"):
        doc = _index_doc(env["files"], env["roots"])
        del doc[field]
        report = vei.validate_index(_write_index(tmp_path, doc))
        assert report["status"] == "INDEX_FAIL", field
        assert field in _problems(report)


def test_wrong_claim_scope_rejected(env, tmp_path):
    env["doc"]["claim_scope"] = "operational"
    report = vei.validate_index(_write_index(tmp_path, env["doc"]))
    assert report["status"] == "INDEX_FAIL"
    assert "claim_scope" in _problems(report)


def test_index_not_json_fails_closed(tmp_path):
    p = tmp_path / "bad.json"
    p.write_text("not json {{{", encoding="utf-8")
    report = vei.validate_index(p)
    assert report["status"] == "INDEX_FAIL"


# ------------------------------------------------------------------
# provenance failures
# ------------------------------------------------------------------

def test_repo_commit_must_be_40_hex(env, tmp_path):
    for bad in ("abc123", "g" * 40, "a" * 64, 12345):
        doc = _index_doc(env["files"], env["roots"])
        doc["generator"]["repo_commit"] = bad
        report = vei.validate_index(_write_index(tmp_path, doc))
        assert report["status"] == "INDEX_FAIL", bad
        assert "repo_commit" in _problems(report)


def test_generated_utc_requires_strict_z(env, tmp_path):
    for bad in ("2026-09-21",                      # date only
                "2026-09-21T00:00:00+00:00",      # offset, not Z
                "2026-09-21 00:00:00Z",           # space separator
                "2026-13-40T99:99:99Z",           # not a real datetime
                1726700000):
        doc = _index_doc(env["files"], env["roots"],
                         generated_utc=bad)
        report = vei.validate_index(_write_index(tmp_path, doc))
        assert report["status"] == "INDEX_FAIL", bad
        assert "generated_utc" in _problems(report)


def test_manifest_sha256_malformed(env, tmp_path):
    env["doc"]["manifest_sha256"] = "z" * 64
    report = vei.validate_index(_write_index(tmp_path, env["doc"]))
    assert report["status"] == "INDEX_FAIL"
    assert "manifest_sha256" in _problems(report)


# ------------------------------------------------------------------
# file-entry vocabulary / shape failures
# ------------------------------------------------------------------

def test_undeclared_root_id_rejected(env, tmp_path):
    env["files"][0]["root_id"] = "ghost"
    report = vei.validate_index(_write_index(tmp_path, env["doc"]))
    assert report["status"] == "INDEX_FAIL"
    assert "not declared in roots" in _problems(report)


def test_absolute_relpath_rejected(env, tmp_path):
    env["files"][0]["relpath"] = str(env["root"] / "retrieval/a.json")
    report = vei.validate_index(_write_index(tmp_path, env["doc"]))
    assert report["status"] == "INDEX_FAIL"
    assert "absolute" in _problems(report)


def test_absolute_path_anywhere_in_files_rejected(env, tmp_path):
    """Absolute paths are allowed only under roots.<id>.path — a stray
    one in any other files[] field fails."""
    env["files"][0]["note"] = "/Users/host/elsewhere/x.json"
    report = vei.validate_index(_write_index(tmp_path, env["doc"]))
    assert report["status"] == "INDEX_FAIL"
    assert "absolute" in _problems(report)


def test_dotdot_escape_rejected(env, tmp_path):
    for bad_rel in ("../escape.txt", "a/../../escape.txt",
                    "..\\win_escape.txt"):
        doc = _index_doc(
            [dict(env["files"][0], relpath=bad_rel)], env["roots"])
        report = vei.validate_index(_write_index(tmp_path, doc))
        assert report["status"] == "INDEX_FAIL", bad_rel
        assert ".." in _problems(report)


def test_malformed_sha256_rejected(env, tmp_path):
    for bad in ("abc", "z" * 64, "a" * 63, "a" * 65, 123):
        doc = _index_doc(
            [dict(env["files"][0], sha256=bad)], env["roots"])
        report = vei.validate_index(_write_index(tmp_path, doc))
        assert report["status"] == "INDEX_FAIL", bad
        assert "sha256" in _problems(report)


def test_bad_state_vocab_rejected(env, tmp_path):
    env["files"][0]["state"] = "deprecated"
    report = vei.validate_index(_write_index(tmp_path, env["doc"]))
    assert report["status"] == "INDEX_FAIL"
    assert "state" in _problems(report)


def test_empty_activity_rejected(env, tmp_path):
    for bad in ("", "   ", None, 7):
        doc = _index_doc(
            [dict(env["files"][0], activity=bad)], env["roots"])
        report = vei.validate_index(_write_index(tmp_path, doc))
        assert report["status"] == "INDEX_FAIL", repr(bad)
        assert "activity" in _problems(report)


def test_size_bytes_must_be_nonnegative_int(env, tmp_path):
    for bad in (-1, True, 3.5, "12"):
        doc = _index_doc(
            [dict(env["files"][0], size_bytes=bad)], env["roots"])
        report = vei.validate_index(_write_index(tmp_path, doc))
        assert report["status"] == "INDEX_FAIL", repr(bad)
        assert "size_bytes" in _problems(report)


def test_duplicate_logical_path_rejected(env, tmp_path):
    env["doc"]["files"] = [env["files"][0], dict(env["files"][0])]
    report = vei.validate_index(_write_index(tmp_path, env["doc"]))
    assert report["status"] == "INDEX_FAIL"
    assert "duplicate" in _problems(report)


def test_self_reference_rejected(env, tmp_path):
    """An index must not list itself."""
    root = env["root"]
    index_in_root = root / "p5_evidence_index_v1.json"
    doc = _index_doc(
        env["files"] + [_entry("r1", "p5_evidence_index_v1.json",
                               {"sha256": "c" * 64, "size_bytes": 0,
                                "sidecar_sha256": None})],
        env["roots"])
    index_in_root.write_text(json.dumps(doc), encoding="utf-8")
    report = vei.validate_index(index_in_root)
    assert report["status"] == "INDEX_FAIL"
    assert "self-reference" in _problems(report)


# ------------------------------------------------------------------
# live-verification failures (digests, sizes, sidecars, supersedes)
# ------------------------------------------------------------------

def test_missing_file_detected(env, tmp_path):
    env["files"][0]["relpath"] = "retrieval/ghost.json"
    report = vei.validate_index(_write_index(tmp_path, env["doc"]))
    assert report["status"] == "INDEX_FAIL"
    assert "missing on disk" in _problems(report)
    assert report["files_ok"] == 0


def test_stale_sha256_detected(env, tmp_path):
    (env["root"] / "retrieval/a.json").write_bytes(b'{"a": 2}')
    report = vei.validate_index(_write_index(tmp_path, env["doc"]))
    assert report["status"] == "INDEX_FAIL"
    assert "sha256 stale" in _problems(report)


def test_stale_size_detected(env, tmp_path):
    env["files"][0]["size_bytes"] += 10
    report = vei.validate_index(_write_index(tmp_path, env["doc"]))
    assert report["status"] == "INDEX_FAIL"
    assert "size_bytes stale" in _problems(report)


def test_missing_sidecar_detected(env, tmp_path):
    (env["root"] / "retrieval/a.json.sha256").unlink()
    report = vei.validate_index(_write_index(tmp_path, env["doc"]))
    assert report["status"] == "INDEX_FAIL"
    assert "sidecar missing" in _problems(report)


def test_stale_sidecar_digest_detected(env, tmp_path):
    sc = env["root"] / "retrieval/a.json.sha256"
    sc.write_text("tampered", encoding="utf-8")
    report = vei.validate_index(_write_index(tmp_path, env["doc"]))
    assert report["status"] == "INDEX_FAIL"
    assert "sidecar_sha256 stale" in _problems(report)


def test_sidecar_payload_token_mismatch(env, tmp_path):
    """Sidecar whose OWN digest matches but whose payload's first token
    is not the file's sha256 must still fail."""
    sc = env["root"] / "retrieval/a.json.sha256"
    sc.write_text(f"{'0' * 64}  a.json\n", encoding="utf-8")
    env["files"][0]["sidecar_sha256"] = _sha(sc.read_bytes())
    report = vei.validate_index(_write_index(tmp_path, env["doc"]))
    assert report["status"] == "INDEX_FAIL"
    assert "first token" in _problems(report)


def test_supersedes_verified_and_stale_detected(env, tmp_path):
    root = env["root"]
    old = root / "retrieval/p5_evidence_index_v0.json"
    old.write_bytes(b'{"schema": "P5_EVIDENCE_INDEX_V0"}')
    # Correct digest -> still OK.
    doc = _index_doc(env["files"], env["roots"], supersedes={
        "relpath": "retrieval/p5_evidence_index_v0.json",
        "sha256": _sha(old.read_bytes())})
    report = vei.validate_index(_write_index(tmp_path, doc))
    assert report["status"] == "INDEX_OK", report["problems"]
    # Stale digest -> FAIL.
    doc["supersedes"]["sha256"] = "d" * 64
    report = vei.validate_index(_write_index(tmp_path, doc))
    assert report["status"] == "INDEX_FAIL"
    assert "supersedes" in _problems(report)
    # Missing target -> FAIL.
    doc["supersedes"]["relpath"] = "retrieval/nonexistent_v0.json"
    report = vei.validate_index(_write_index(tmp_path, doc))
    assert report["status"] == "INDEX_FAIL"
    assert "supersedes target not found" in _problems(report)


def test_multi_root_index_passes(tmp_path):
    """Cross-root is the whole point — two roots, one file each."""
    r1, r2 = tmp_path / "root_a", tmp_path / "root_b"
    r1.mkdir(); r2.mkdir()
    m1 = _write_payload(r1, "run/x.json", b"x")
    m2 = _write_payload(r2, "run/y.json", b"yy")
    doc = _index_doc(
        [_entry("a", "run/x.json", m1),
         _entry("b", "run/y.json", m2, state="immutable")],
        {"a": {"path": str(r1), "role": "alpha"},
         "b": {"path": str(r2), "role": "beta"}})
    report = vei.validate_index(_write_index(tmp_path, doc))
    assert report["status"] == "INDEX_OK", report["problems"]
    assert report["files_checked"] == 2
    assert report["files_ok"] == 2


# ------------------------------------------------------------------
# V2 exhaustive inventory / partition tests
# ------------------------------------------------------------------

def _v2_mapping_file(tmp_path, root):
    mapping = {
        "daily": {
            "path": str(root),
            "role": "synthetic daily evidence",
            "kind": "physical",
        },
        "seismic": {
            "path": str(root / "seismic"),
            "role": "synthetic seismic logical partition",
            "kind": "logical",
            "physical_root_id": "daily",
            "path_prefix": "seismic",
        },
    }
    p = tmp_path / "root-map.json"
    p.write_text(json.dumps(mapping), encoding="utf-8")
    return p


def _generate_v2(tmp_path, *, index_inside_root=False):
    root = tmp_path / "v2-evidence"
    root.mkdir()
    _write_payload(root, "ordinary/payload.json", b"ordinary")
    _write_payload(root, ".hidden-payload", b"hidden")
    _write_payload(root, "seismic/trace.bin", b"seismic")
    mapping = _v2_mapping_file(tmp_path, root)
    index = (root / "index_v2.json" if index_inside_root
             else tmp_path / "index_v2.json")
    report = tmp_path / "generation-report.json"
    rc = gei.main([
        "--root-map", str(mapping),
        "--index-out", str(index),
        "--report-out", str(report),
    ])
    assert rc == 0
    return root, index, report


def test_v2_generation_is_exhaustive_and_partitions_seismic_once(tmp_path):
    root, index, report_path = _generate_v2(tmp_path)
    doc = json.loads(index.read_text(encoding="utf-8"))
    report = vei.validate_index(index)

    assert report["status"] == "INDEX_OK", report["problems"]
    assert doc["schema"] == vei.SCHEMA_V2
    assert isinstance(doc["coverage_scope"], dict)
    assert isinstance(doc["exclusions"], list)
    assert isinstance(doc["topology"], dict)
    listed = {(e["root_id"], e["relpath"]) for e in doc["files"]}
    assert ("daily", ".hidden-payload") in listed
    assert ("seismic", "trace.bin") in listed
    assert ("daily", "seismic/trace.bin") not in listed
    assert json.loads(report_path.read_text(encoding="utf-8"))["status"] == \
        "INDEX_OK"


def test_v2_hidden_payload_fails_full_inventory_coverage(tmp_path):
    root, index, _ = _generate_v2(tmp_path)
    _write_payload(root, ".late-hidden", b"late")
    report = vei.validate_index(index)
    assert report["status"] == "INDEX_FAIL"
    assert "inventory coverage" in _problems(report)
    assert ".late-hidden" in _problems(report)


def test_v2_missing_sidecar_fails(tmp_path):
    root, index, _ = _generate_v2(tmp_path)
    (root / "ordinary/payload.json.sha256").unlink()
    report = vei.validate_index(index)
    assert report["status"] == "INDEX_FAIL"
    assert "sidecar missing" in _problems(report)


def test_v2_sidecar_exception_must_be_explicit_and_validated(tmp_path):
    root, index, _ = _generate_v2(tmp_path)
    (root / "ordinary/payload.json.sha256").unlink()
    doc = json.loads(index.read_text(encoding="utf-8"))
    entry = next(e for e in doc["files"]
                 if e["relpath"] == "ordinary/payload.json")
    entry["sidecar_sha256"] = None
    entry["sidecar_exception"] = {
        "code": "approved_missing_sidecar",
        "reason": "synthetic exception under the declared V2 schema",
        "authority": "pytest",
    }
    p = tmp_path / "sidecar-exception.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    report = vei.validate_index(p)
    assert report["status"] == "INDEX_OK", report["problems"]


def test_v2_unlisted_payload_requires_explicit_exclusion(tmp_path):
    root, index, _ = _generate_v2(tmp_path)
    doc = json.loads(index.read_text(encoding="utf-8"))
    removed = next(e for e in doc["files"]
                   if e["relpath"] == ".hidden-payload")
    doc["files"].remove(removed)
    report_path = tmp_path / "unlisted.json"
    report_path.write_text(json.dumps(doc), encoding="utf-8")
    report = vei.validate_index(report_path)
    assert report["status"] == "INDEX_FAIL"
    assert "inventory coverage" in _problems(report)

    doc["exclusions"].append({
        "root_id": "daily",
        "relpath": ".hidden-payload",
        "reason": "synthetic explicit exclusion",
    })
    doc["final_verification"]["closure"]["exclusions"] = "PASS"
    doc["final_verification"]["counts"]["included_files"] -= 1
    doc["final_verification"]["counts"]["excluded_files"] += 1
    report_path.write_text(json.dumps(doc), encoding="utf-8")
    report = vei.validate_index(report_path)
    assert report["status"] == "INDEX_OK", report["problems"]


def test_v2_duplicate_logical_assignment_fails(tmp_path):
    root, index, _ = _generate_v2(tmp_path)
    doc = json.loads(index.read_text(encoding="utf-8"))
    seismic = next(e for e in doc["files"] if e["root_id"] == "seismic")
    doc["files"].append(dict(seismic, root_id="daily",
                              relpath="seismic/trace.bin"))
    doc["final_verification"]["counts"]["included_files"] += 1
    p = tmp_path / "duplicate.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    report = vei.validate_index(p)
    assert report["status"] == "INDEX_FAIL"
    assert "physical" in _problems(report)


def test_v2_absolute_supersedes_is_rejected(tmp_path):
    root, index, _ = _generate_v2(tmp_path)
    old = root / "old-index.json"
    old.write_text('{"schema":"P5_EVIDENCE_INDEX_V1"}', encoding="utf-8")
    doc = json.loads(index.read_text(encoding="utf-8"))
    doc["supersedes"] = {
        "root_id": "daily",
        "relpath": str(old),
        "sha256": _sha(old.read_bytes()),
    }
    p = tmp_path / "absolute-supersedes.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    report = vei.validate_index(p)
    assert report["status"] == "INDEX_FAIL"
    assert "supersedes.relpath" in _problems(report)
    assert "absolute" in _problems(report)


def test_v2_final_closure_mismatch_fails(tmp_path):
    _, index, _ = _generate_v2(tmp_path)
    doc = json.loads(index.read_text(encoding="utf-8"))
    doc["final_verification"]["closure"]["inventory_coverage"] = "FAIL"
    p = tmp_path / "closure-mismatch.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    report = vei.validate_index(p)
    assert report["status"] == "INDEX_FAIL"
    assert "final_verification" in _problems(report)


def test_v2_existing_output_refuses_overwrite(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    _write_payload(root, "payload.bin", b"payload")
    mapping = _v2_mapping_file(tmp_path, root)
    index = tmp_path / "existing-index.json"
    index.write_bytes(b"do-not-overwrite")
    before = index.read_bytes()
    rc = gei.main([
        "--root-map", str(mapping),
        "--index-out", str(index),
    ])
    assert rc != 0
    assert index.read_bytes() == before


def test_v2_index_self_reference_fails(tmp_path):
    root, index, _ = _generate_v2(tmp_path, index_inside_root=True)
    doc = json.loads(index.read_text(encoding="utf-8"))
    doc["files"].append({
        "root_id": "daily",
        "relpath": "index_v2.json",
        "sha256": _sha(index.read_bytes()),
        "size_bytes": index.stat().st_size,
        "sidecar_sha256": None,
        "sidecar_exception": {
            "code": "index_self_reference_probe",
            "reason": "synthetic invalid self-reference probe",
            "authority": "pytest",
        },
        "state": "current",
        "activity": "synthetic self-reference probe",
        "entity_role": "index",
    })
    index.write_text(json.dumps(doc), encoding="utf-8")
    report = vei.validate_index(index)
    assert report["status"] == "INDEX_FAIL"
    assert "self-reference" in _problems(report)


def test_v1_remains_non_exhaustive(env, tmp_path):
    _write_payload(env["root"], "unlisted-v1.json", b"still v1")
    report = vei.validate_index(env["index"])
    assert report["status"] == "INDEX_OK", report["problems"]


def test_v2_planned_detached_closure_is_excluded_after_publication(tmp_path):
    root = tmp_path / "evidence"
    root.mkdir()
    _write_payload(root, "payload.json", b"payload")
    mapping = {"daily": {"path": str(root), "role": "daily",
                          "kind": "physical"}}
    index = tmp_path / "index.json"
    closure = root / "retrieval" / "p5_release_closure_v2.json"
    rc = gei.main([
        "--root-map", json.dumps(mapping),
        "--index-out", str(index),
        "--detached-closure", str(closure),
    ])
    assert rc == 0
    closure.parent.mkdir(parents=True, exist_ok=True)
    closure.write_text('{"detached": true}', encoding="utf-8")
    report = vei.validate_index(index)
    assert report["status"] == "INDEX_OK", report["problems"]


# ------------------------------------------------------------------
# V2 freshness binding + coordinator-declared planned slots (A4)
# ------------------------------------------------------------------

def _single_root(tmp_path, *payloads):
    root = tmp_path / "evidence"
    root.mkdir()
    for relpath, data in payloads:
        _write_payload(root, relpath, data)
    return root, {"daily": {"path": str(root), "role": "daily",
                            "kind": "physical"}}


def test_v2_manifest_records_generation_head(tmp_path):
    """The manifest section distinguishes the bound manifest heads from
    the live HEAD at index-generation time (they differ after a
    manifest-only rebind commit)."""
    _, index, _ = _generate_v2(tmp_path)
    doc = json.loads(index.read_text(encoding="utf-8"))
    generation_head = doc["manifest"].get("generation_head")
    assert isinstance(generation_head, str)
    assert vei._HEX40(generation_head)
    # Same live HEAD the generator provenance records.
    assert generation_head == doc["generator"]["repo_commit"]
    report = vei.validate_index(index)
    assert report["status"] == "INDEX_OK", report["problems"]


def test_v2_planned_exclusion_with_owner_generates_and_validates(
        tmp_path):
    """A coordinator can declare a post-index publication slot via
    --exclusions; it tolerates absence and drives CLOSURE_PENDING."""
    root, mapping = _single_root(tmp_path, ("payload.json", b"payload"))
    index = tmp_path / "index.json"
    exclusions = [{
        "root_id": "daily",
        "relpath": "retrieval/future_slot.json",
        "reason": "post-index publication slot",
        "state": "planned",
        "owner": "coordinator",
    }]
    rc = gei.main([
        "--root-map", json.dumps(mapping),
        "--index-out", str(index),
        "--exclusions", json.dumps(exclusions),
    ])
    assert rc == 0
    doc = json.loads(index.read_text(encoding="utf-8"))
    slot = next(e for e in doc["exclusions"]
                if e["relpath"] == "retrieval/future_slot.json")
    assert slot["state"] == "planned"
    assert slot["owner"] == "coordinator"
    assert doc["final_verification"]["status"] == "CLOSURE_PENDING"
    assert doc["final_verification"]["closure"]["status"] == \
        "CLOSURE_PENDING"
    report = vei.validate_index(index)
    assert report["status"] == "INDEX_OK", report["problems"]


def test_v2_planned_exclusion_without_owner_fails_generation(tmp_path):
    root, mapping = _single_root(tmp_path, ("payload.json", b"payload"))
    index = tmp_path / "index.json"
    exclusions = [{
        "root_id": "daily",
        "relpath": "retrieval/future_slot.json",
        "reason": "post-index publication slot",
        "state": "planned",
    }]
    rc = gei.main([
        "--root-map", json.dumps(mapping),
        "--index-out", str(index),
        "--exclusions", json.dumps(exclusions),
    ])
    assert rc == 1
    assert not index.exists()


def test_v2_planned_absent_slot_rejects_closed_status(tmp_path):
    """When a planned slot is absent, CLOSED is not an honest final
    state — the validator forces CLOSURE_PENDING."""
    root, mapping = _single_root(tmp_path, ("payload.json", b"payload"))
    index = tmp_path / "index.json"
    exclusions = [{
        "root_id": "daily",
        "relpath": "retrieval/future_slot.json",
        "reason": "post-index publication slot",
        "state": "planned",
        "owner": "coordinator",
    }]
    rc = gei.main([
        "--root-map", json.dumps(mapping),
        "--index-out", str(index),
        "--exclusions", json.dumps(exclusions),
    ])
    assert rc == 0
    doc = json.loads(index.read_text(encoding="utf-8"))
    doc["final_verification"]["status"] = "CLOSED"
    doc["final_verification"]["closure"]["status"] = "CLOSED"
    p = tmp_path / "claimed-closed.json"
    p.write_text(json.dumps(doc), encoding="utf-8")
    report = vei.validate_index(p)
    assert report["status"] == "INDEX_FAIL"
    assert "CLOSURE_PENDING" in _problems(report)


def test_v2_present_exclusion_needs_no_owner(tmp_path):
    """Owner is mandatory only for planned slots; a present exclusion
    of an on-disk payload validates without one."""
    root, mapping = _single_root(
        tmp_path, ("payload.json", b"payload"), ("extra.bin", b"x"))
    index = tmp_path / "index.json"
    exclusions = [{
        "root_id": "daily",
        "relpath": "extra.bin",
        "reason": "not part of the indexed surface",
    }]
    rc = gei.main([
        "--root-map", json.dumps(mapping),
        "--index-out", str(index),
        "--exclusions", json.dumps(exclusions),
    ])
    assert rc == 0
    doc = json.loads(index.read_text(encoding="utf-8"))
    assert doc["final_verification"]["status"] == "CLOSED"
    report = vei.validate_index(index)
    assert report["status"] == "INDEX_OK", report["problems"]
