"""Adversarial tests for the PoC closeout reconciliation artifact builder.

Covers the sidecar binding (_bound_json), the write-once publish path
(_publish_or_match), the build() output contract against the real sealed
evidence, the verify subcommand, and build() determinism.
"""
import hashlib
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import hma_closeout_reconciliation as rec  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SCRIPT = REPO / "scripts" / "hma_closeout_reconciliation.py"
E = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
V3 = E / "india-phase0-source-intake" / "hma-lake-trajectory-poc-v3"
SEALED = V3 / f"{rec.SCHEMA}.json"
_PRESENT = (V3 / "HMA_LAKE_TRAJECTORIES_V3.json").is_file()
_HEX64 = re.compile(r"^[0-9a-f]{64}$")

# Keys of doc["input_artifacts"] mapped to the on-disk files build() binds.
# Filenames mirror build() so a drifted binding fails the digest check below.
_INPUT_PATHS = {
    "trajectories_v3": rec.V3 / "HMA_LAKE_TRAJECTORIES_V3.json",
    "nkp_result": rec.V3 / "HMA_NKP_RESULT_V0.json",
    "frozen_nkp_plan": rec.V3 / "FROZEN_NKP_PLAN_V0.json",
    "linkage_review_v1": rec.E / "INDIA_LAKE_LINKAGE_REVIEW_V1.json",
    "gcal_result": rec.GCAL / "HMA_GATE_CALIBRATION_V0.json",
    "independent_audit": rec.AUDIT / "HMA_INDEPENDENT_AUDIT_V0.json",
    "error_disposition": rec.E / "HMA_ERROR_FIELD_DISPOSITION_V0.json",
    "linkability_census": rec.E / "HMA_EVENT_LINKABILITY_CENSUS_V0.json",
    "proof_binding_correction":
        rec.V2 / "HMA_PROOF_BINDING_CORRECTION_V0.json",
}


def _write_sealed(root: Path, name: str, obj: dict) -> Path:
    """Write a JSON doc plus a correct '<sha>  <name>' sidecar."""
    p = root / name
    p.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    Path(f"{p}.sha256").write_text(f"{digest}  {p.name}\n")
    return p


# --- _bound_json: sidecar binding must fail closed ------------------------

def test_bound_json_roundtrips_clean_fixture(tmp_path):
    p = _write_sealed(tmp_path, "DOC.json", {"k": "v", "n": 7})
    assert rec._bound_json(p) == {"k": "v", "n": 7}


def test_bound_json_rejects_flipped_payload_byte(tmp_path):
    p = _write_sealed(tmp_path, "DOC.json", {"k": "value"})
    data = bytearray(p.read_bytes())
    i = data.index(b"v")
    data[i] = ord("x") if data[i] != ord("x") else ord("y")
    p.write_bytes(bytes(data))
    with pytest.raises(ValueError):
        rec._bound_json(p)


def test_bound_json_rejects_missing_sidecar(tmp_path):
    p = _write_sealed(tmp_path, "DOC.json", {"k": "v"})
    Path(f"{p}.sha256").unlink()
    with pytest.raises(ValueError):
        rec._bound_json(p)


def test_bound_json_rejects_zeroed_sidecar_digest(tmp_path):
    p = _write_sealed(tmp_path, "DOC.json", {"k": "v"})
    Path(f"{p}.sha256").write_text(f"{'0' * 64}  {p.name}\n")
    with pytest.raises(ValueError):
        rec._bound_json(p)


def test_bound_json_rejects_sidecar_with_wrong_filename(tmp_path):
    p = _write_sealed(tmp_path, "DOC.json", {"k": "v"})
    digest = hashlib.sha256(p.read_bytes()).hexdigest()
    Path(f"{p}.sha256").write_text(f"{digest}  OTHER.json\n")
    with pytest.raises(ValueError):
        rec._bound_json(p)


# --- _publish_or_match: write-once publish semantics ----------------------

def test_publish_first_call_writes_doc_and_sidecar(tmp_path):
    p = tmp_path / "OUT.json"
    doc = {"a": 1, "b": ["x"]}
    sha = rec._publish_or_match(p, doc)
    assert _HEX64.match(sha)
    assert sha == hashlib.sha256(p.read_bytes()).hexdigest()
    assert Path(f"{p}.sha256").read_text() == f"{sha}  {p.name}\n"
    assert json.loads(p.read_text()) == doc


def test_publish_identical_doc_returns_same_sha(tmp_path):
    p = tmp_path / "OUT.json"
    doc = {"a": 1}
    sha1 = rec._publish_or_match(p, doc)
    sha2 = rec._publish_or_match(p, doc)
    assert sha1 == sha2


def test_publish_divergent_doc_rejected(tmp_path):
    p = tmp_path / "OUT.json"
    rec._publish_or_match(p, {"a": 1})
    with pytest.raises(FileExistsError):
        rec._publish_or_match(p, {"a": 2})
    # original bytes untouched
    assert json.loads(p.read_text()) == {"a": 1}


def test_publish_with_tampered_sidecar_rejected(tmp_path):
    p = tmp_path / "OUT.json"
    doc = {"a": 1}
    rec._publish_or_match(p, doc)
    Path(f"{p}.sha256").write_text(f"{'0' * 64}  {p.name}\n")
    with pytest.raises(ValueError):
        rec._publish_or_match(p, doc)


def test_publish_with_tampered_payload_rejected(tmp_path):
    p = tmp_path / "OUT.json"
    rec._publish_or_match(p, {"a": 1})
    p.write_text(json.dumps({"a": 2, "backdoor": True}) + "\n")
    # payload drift now disagrees with the (untouched) sidecar first
    with pytest.raises(ValueError):
        rec._publish_or_match(p, {"a": 1})


# --- build() output contract over the real sealed evidence ----------------

@pytest.mark.skipif(not _PRESENT, reason="evidence root unavailable")
def test_build_contract_against_sealed_inputs():
    doc = rec.build()
    assert doc["schema"] == "HMA_POC_CLOSEOUT_RECONCILIATION_V0"

    cohort = doc["cohort_reconciliation"]
    assert cohort["frozen_nkp_complete_paths"] == 5047
    assert cohort["relaxed_audit_nested08_reference"] == 5087

    assert doc["event_association_branch"] == "CLOSED"
    assert all(v is False for v in doc["authority"].values())
    assert doc["authority"] == dict(rec.linkage.AUTHORITY_FLAGS)

    inputs = doc["input_artifacts"]
    assert len(inputs) >= 8
    assert set(inputs) == set(_INPUT_PATHS)
    for name, digest in inputs.items():
        assert _HEX64.match(digest), (name, digest)

    ceiling = doc["claim_ceiling"]
    for key in ("authorized", "prohibited"):
        phrases = ceiling[key]
        assert isinstance(phrases, list) and phrases, key
        assert all(isinstance(s, str) and s.strip() for s in phrases)

    assert (doc["hypothesis_outcomes"]["status"]
            == "CONTRAST_ONLY_NO_PERSISTENCE")


@pytest.mark.skipif(not _PRESENT, reason="evidence root unavailable")
def test_build_input_digests_match_on_disk_artifacts():
    """Each recorded digest must equal the sha256 of the file on disk.

    Catches a stale or re-pointed binding that a shape-only check misses.
    """
    doc = rec.build()
    for name, path in _INPUT_PATHS.items():
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        assert doc["input_artifacts"][name] == actual, name


@pytest.mark.skipif(not _PRESENT, reason="evidence root unavailable")
def test_build_sidecar_verified_inputs_match_sealed_reconciliation():
    """build() output must equal the sealed reconciliation bytes."""
    sealed = rec._bound_json(SEALED)
    assert rec.build() == sealed


# --- verify subcommand ----------------------------------------------------

@pytest.mark.skipif(not _PRESENT, reason="evidence root unavailable")
def test_verify_subcommand_clean_on_real_artifact():
    out = subprocess.run(
        [sys.executable, "-B", str(SCRIPT), "verify"],
        cwd=REPO, capture_output=True, text=True, timeout=300)
    assert out.returncode == 0, out.stdout + out.stderr
    assert "RECONCILIATION_VERIFY_OK" in out.stdout
    assert "RECONCILIATION_BLOCKED" not in out.stdout


# --- determinism ----------------------------------------------------------

@pytest.mark.skipif(not _PRESENT, reason="evidence root unavailable")
def test_build_is_deterministic():
    a = rec.build()
    b = rec.build()
    assert a == b
    assert (json.dumps(a, sort_keys=True)
            == json.dumps(b, sort_keys=True))
