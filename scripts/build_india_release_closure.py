"""Build and validate a detached, append-only India Phase-0 release closure.

Version 1 closes the proof gaps in closure V0: it verifies closure and suite
receipt sidecars, validates the receipt schema/counts/head binding, recomputes
the reachable-object payload scan, requires a clean worktree, and can compare
the recorded remote-ref inventory with a fresh ``git ls-remote`` snapshot.
The closure is stored outside Git to avoid self-reference.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import stat
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit


SCHEMA = "INDIA_PHASE0_RELEASE_CLOSURE_V1"
REPO = Path(__file__).resolve().parents[1]
EVIDENCE_ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence")
EVIDENCE_DIR = EVIDENCE_ROOT / "india-phase0-release"
BACKUP_PATH = (EVIDENCE_ROOT / "restricted-backups"
               / "nepal-event-anomaly-prewrite-20260927.bundle")
BACKUP_CUSTODY_PATH = (EVIDENCE_ROOT / "restricted-backups"
                       / "NEPAL_BACKUP_CUSTODY_V1.json")
BASELINE_HEAD = "54aa612f7d478082716dfe5d0bca7d92d62d493b"
PAYLOAD_PATHS = [
    "data/dem_n28e085.tif", "data/temp/era5_land_2001_06.nc",
    "data/download_ledger.json", "data/era5_download_log.txt",
    "data/nisar_catalog_ledger.json",
]
AUTHORITY_FLAGS = {
    "bulk_acquisition_authorized": False,
    "weather_download_authorized": False,
    "satellite_bulk_authorized": False,
    "seismic_waveform_authorized": False,
    "forecast_authorized": False,
    "warning_authorized": False,
    "detector_authorized": False,
    "odds_authorized": False,
    "causal_authorized": False,
    "operational_authorized": False,
}
_HEX40 = re.compile(r"[0-9a-f]{40}").fullmatch
_HEX64 = re.compile(r"[0-9a-f]{64}").fullmatch
_PERMITTED_RELEASE_PATH = re.compile(
    r"docs/science/(?:SUITE_RECEIPT_[A-Za-z0-9_.-]+\.json(?:\.sha256)?|"
    r"MANIFEST_SCOPE_EXCLUSIONS_V0\.json(?:\.sha256)?)\Z")


class ClosureError(ValueError):
    """Raised when a closure cannot be built from verifiable evidence."""


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _git(repo_root: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=repo_root,
                          capture_output=True, text=True, check=False)
    if proc.returncode:
        raise ClosureError(
            f"git {' '.join(args)} failed ({proc.returncode}): "
            f"{proc.stderr.strip()}")
    return proc.stdout.strip()


def _sidecar_problems(path: Path, expected_digest: str | None = None) -> list[str]:
    sidecar = Path(str(path) + ".sha256")
    if not sidecar.is_file():
        return [f"missing sidecar: {sidecar.name}"]
    try:
        fields = sidecar.read_text(encoding="utf-8").split()
    except OSError as exc:
        return [f"unreadable sidecar {sidecar.name}: {exc}"]
    if len(fields) != 2 or not _HEX64(fields[0]):
        return [f"malformed sidecar: {sidecar.name}"]
    actual = _sha256_file(path) if path.is_file() else None
    if actual is None or fields[0] != actual:
        return [f"sidecar digest mismatch: {sidecar.name}"]
    if fields[1] != path.name:
        return [f"sidecar filename mismatch: {sidecar.name}"]
    if expected_digest is not None and actual != expected_digest:
        return [f"bound digest mismatch: {path.name}"]
    return []


def _clean_remote_url(value: str) -> str:
    """Return a remote URL without credentials, or reject unknown syntax."""
    parsed = urlsplit(value)
    if parsed.scheme in {"https", "http"} and parsed.hostname:
        host = parsed.hostname
        if parsed.port:
            host = f"{host}:{parsed.port}"
        # Query strings and userinfo are omitted: either can carry credentials.
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    # Normalize scp-style Git remotes without retaining the user field.
    match = re.fullmatch(r"(?:[^@/:]+@)?([^/:]+):(.+)", value)
    if match:
        return f"ssh://{match.group(1)}/{match.group(2)}"
    raise ClosureError("remote URL must be a supported, non-secret locator")


def capture_remote_inventory(repo_root: Path, remote_name: str = "origin") -> dict:
    """Capture exact public refs and HEAD symref from the named Git remote."""
    remote_url = _git(repo_root, "remote", "get-url", remote_name)
    proc = subprocess.run(
        ["git", "ls-remote", "--symref", remote_name], cwd=repo_root,
        capture_output=True, text=True, check=False)
    if proc.returncode:
        raise ClosureError(
            f"git ls-remote {remote_name} failed: {proc.stderr.strip()}")
    refs: dict[str, str] = {}
    symrefs: dict[str, str] = {}
    head_oid: str | None = None
    for line in proc.stdout.splitlines():
        fields = line.split("\t")
        if len(fields) != 2:
            raise ClosureError(f"malformed ls-remote line: {line!r}")
        value, refname = fields
        if value.startswith("ref: "):
            symrefs[refname] = value[5:]
            continue
        if not _HEX40(value):
            raise ClosureError(f"remote ref has invalid object id: {line!r}")
        if refname == "HEAD":
            head_oid = value
        else:
            refs[refname] = value
    if "refs/heads/main" not in refs:
        raise ClosureError("remote inventory does not contain refs/heads/main")
    return {
        "remote_name": remote_name,
        "remote_url": _clean_remote_url(remote_url),
        "inventory_scope": "all_advertised_refs",
        "captured_utc": datetime.now(timezone.utc).isoformat(),
        "head_oid": head_oid,
        "refs": dict(sorted(refs.items())),
        "symrefs": dict(sorted(symrefs.items())),
    }


def _validate_remote_inventory(value: object) -> list[str]:
    if not isinstance(value, dict):
        return ["remote_ref_inventory must be an object"]
    problems: list[str] = []
    if value.get("remote_name") != "origin":
        problems.append("remote_ref_inventory.remote_name must be origin")
    if value.get("inventory_scope") != "all_advertised_refs":
        problems.append("remote inventory must cover all advertised refs")
    if not isinstance(value.get("remote_url"), str) or not value["remote_url"]:
        problems.append("remote_ref_inventory.remote_url must be non-empty")
    else:
        try:
            if _clean_remote_url(value["remote_url"]) != value["remote_url"]:
                problems.append("remote_ref_inventory.remote_url is not sanitized")
        except ClosureError:
            problems.append("remote_ref_inventory.remote_url is unsupported")
    if not isinstance(value.get("captured_utc"), str) or not value["captured_utc"]:
        problems.append("remote_ref_inventory.captured_utc must be non-empty")
    else:
        try:
            captured = datetime.fromisoformat(
                value["captured_utc"].replace("Z", "+00:00"))
            if captured.tzinfo is None:
                problems.append("remote_ref_inventory.captured_utc must include a timezone")
        except ValueError:
            problems.append("remote_ref_inventory.captured_utc must be ISO-8601")
    head_oid = value.get("head_oid")
    if not isinstance(head_oid, str) or not _HEX40(head_oid):
        problems.append("remote_ref_inventory.head_oid must be a 40-hex id")
    refs = value.get("refs")
    if not isinstance(refs, dict) or not refs:
        problems.append("remote_ref_inventory.refs must be a non-empty object")
    else:
        for refname, oid in refs.items():
            if (not isinstance(refname, str) or not refname.startswith("refs/")):
                problems.append("remote inventory ref names must start with refs/")
            if not isinstance(oid, str) or not _HEX40(oid):
                problems.append(f"remote inventory {refname!r} must bind a 40-hex id")
        if not isinstance(refs.get("refs/heads/main"), str):
            problems.append("remote inventory must contain refs/heads/main")
        elif isinstance(head_oid, str) and _HEX40(head_oid) and head_oid != refs[
                "refs/heads/main"]:
            problems.append("remote HEAD object differs from refs/heads/main")
    symrefs = value.get("symrefs")
    if not isinstance(symrefs, dict):
        problems.append("remote_ref_inventory.symrefs must be an object")
    else:
        if any(not isinstance(k, str) or not isinstance(v, str)
               for k, v in symrefs.items()):
            problems.append("remote symrefs must map strings to strings")
        if symrefs.get("HEAD") != "refs/heads/main":
            problems.append("remote HEAD symref must target refs/heads/main")
    return problems


def _remote_history_scan(repo_root: Path, inventory: object) -> dict:
    """Scan objects reachable from every advertised remote ref in this repo."""
    problems = _validate_remote_inventory(inventory)
    if problems:
        raise ClosureError("remote history scan inventory invalid: "
                           + "; ".join(problems))
    refs = inventory["refs"]
    commits: list[str] = []
    for refname, oid in sorted(refs.items()):
        try:
            commit = _git(repo_root, "rev-parse", "--verify", "--quiet",
                          "--end-of-options", f"{oid}^{{commit}}")
        except ClosureError as exc:
            raise ClosureError(
                f"advertised remote ref {refname} is not locally resolvable "
                "to a commit") from exc
        if not _HEX40(commit):
            raise ClosureError(
                f"advertised remote ref {refname} did not peel to a commit")
        commits.append(commit)
    proc = subprocess.run(["git", "rev-list", "--objects", *commits],
                          cwd=repo_root, capture_output=True, check=False)
    if proc.returncode:
        raise ClosureError("git rev-list over advertised remote refs failed")
    raw = proc.stdout
    hits: set[str] = set()
    object_count = 0
    for line in raw.splitlines():
        object_count += 1
        fields = line.split(b" ", 1)
        if len(fields) == 2:
            try:
                path = fields[1].decode("utf-8")
            except UnicodeDecodeError:
                continue
            if path in PAYLOAD_PATHS:
                hits.add(path)
    canonical_refs = "\n".join(
        f"{name} {oid}" for name, oid in sorted(refs.items()))
    return {
        "method": "git rev-list --objects over all advertised remote refs",
        "checked_refs": [
            {"name": name, "oid": oid}
            for name, oid in sorted(refs.items())],
        "reachable_object_count": object_count,
        "object_listing_sha256": hashlib.sha256(raw).hexdigest(),
        "refs_sha256": hashlib.sha256(canonical_refs.encode("utf-8")).hexdigest(),
        "checked_payload_paths": list(PAYLOAD_PATHS),
        "payload_path_hits": sorted(hits),
        "clean": not hits,
    }


def _history_scan(repo_root: Path) -> dict:
    """Recompute the exact-path scan over objects reachable from all refs."""
    proc = subprocess.run(["git", "rev-list", "--objects", "--all"],
                          cwd=repo_root, capture_output=True, check=False)
    if proc.returncode:
        raise ClosureError("git rev-list --objects --all failed")
    raw = proc.stdout
    hits: set[str] = set()
    object_count = 0
    for line in raw.splitlines():
        object_count += 1
        fields = line.split(b" ", 1)
        if len(fields) == 2:
            try:
                path = fields[1].decode("utf-8")
            except UnicodeDecodeError:
                continue
            if path in PAYLOAD_PATHS:
                hits.add(path)
    refs = _git(repo_root, "for-each-ref", "--format=%(refname) %(objectname)")
    return {
        "method": "git rev-list --objects --all; exact path match",
        "checked_payload_paths": list(PAYLOAD_PATHS),
        "payload_path_hits": sorted(hits),
        "reachable_object_count": object_count,
        "object_listing_sha256": hashlib.sha256(raw).hexdigest(),
        "refs_sha256": hashlib.sha256(refs.encode("utf-8")).hexdigest(),
        "clean": not hits,
    }


def _receipt_problems(receipt_path: Path, expected_digest: str,
                      tested_content_head: str,
                      manifest_digest: str) -> tuple[list[str], dict | None]:
    problems = _sidecar_problems(receipt_path, expected_digest)
    if not receipt_path.is_file():
        return problems + [f"receipt missing: {receipt_path.name}"], None
    try:
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return problems + [f"receipt unreadable or invalid JSON: {exc}"], None
    if not isinstance(receipt, dict):
        return problems + ["receipt root must be an object"], None
    if receipt.get("schema") != "P5_SUITE_RECEIPT_V2":
        problems.append("receipt schema must be P5_SUITE_RECEIPT_V2")
    if receipt.get("repository_head") != tested_content_head:
        problems.append("receipt repository_head differs from tested_content_head")
    if type(receipt.get("exit_code")) is not int or receipt.get("exit_code") != 0:
        problems.append("receipt exit_code must be 0")
    if receipt.get("claim_scope") != "research_only_no_operational_authorization":
        problems.append("receipt claim_scope is not research-only")
    if receipt.get("manifest_sha256") != manifest_digest:
        problems.append("receipt manifest digest differs from closure")
    counts = receipt.get("counts")
    if not isinstance(counts, dict):
        problems.append("receipt counts must be an object")
    else:
        required = ("passed", "skipped", "failed", "errors", "collected")
        if any(not isinstance(counts.get(k), int)
               or isinstance(counts.get(k), bool) or counts[k] < 0
               for k in required):
            problems.append("receipt counts lack valid non-negative integers")
        else:
            actual = counts["passed"] + counts["skipped"] + counts["failed"] + counts["errors"]
            if counts["collected"] != actual:
                problems.append("receipt collected count does not reconcile")
            if counts["failed"] or counts["errors"]:
                problems.append("receipt records test failures or errors")
    return problems, receipt


def _backup_custody_problems(custody_path: Path,
                             repo_root: Path) -> tuple[list[str], dict | None]:
    problems = _sidecar_problems(custody_path)
    if not custody_path.is_file():
        return problems + ["backup custody receipt is missing"], None
    restricted_dir = (EVIDENCE_ROOT / "restricted-backups").resolve()
    if custody_path.resolve().parent != restricted_dir:
        problems.append("backup custody receipt must live in restricted-backups")
    if stat.S_IMODE(custody_path.stat().st_mode) != 0o600:
        problems.append("backup custody receipt mode must be 0600")
    custody_sidecar = Path(str(custody_path) + ".sha256")
    if custody_sidecar.is_file() and stat.S_IMODE(
            custody_sidecar.stat().st_mode) != 0o600:
        problems.append("backup custody sidecar mode must be 0600")
    if not restricted_dir.is_dir() or stat.S_IMODE(
            restricted_dir.stat().st_mode) != 0o700:
        problems.append("actual backup custody directory mode must be 0700")
    try:
        custody = json.loads(custody_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return problems + [f"backup custody receipt is unreadable: {exc}"], None
    if not isinstance(custody, dict):
        return problems + ["backup custody receipt root must be an object"], None
    if (custody.get("schema") != "NEPAL_BACKUP_CUSTODY_V1"
            or custody.get("version") != 1):
        problems.append("backup custody receipt schema is unexpected")
    predecessor = custody.get("supersedes")
    if (not isinstance(predecessor, dict)
            or predecessor.get("file") != "NEPAL_BACKUP_CUSTODY_V0.json"
            or not isinstance(predecessor.get("sha256"), str)
            or not _HEX64(predecessor.get("sha256", ""))):
        problems.append("backup custody successor must bind the v0 receipt")
    else:
        predecessor_path = custody_path.parent / predecessor["file"]
        problems.extend(f"backup custody predecessor: {problem}" for problem in
                        _sidecar_problems(predecessor_path,
                                          predecessor["sha256"]))
    rel = custody.get("bundle_relpath")
    digest = custody.get("bundle_sha256")
    size = custody.get("bundle_size_bytes")
    if (not isinstance(rel, str) or Path(rel).is_absolute()
            or ".." in Path(rel).parts):
        problems.append("backup bundle path must be safe and relative")
        bundle = None
    else:
        expected_rel = BACKUP_PATH.relative_to(EVIDENCE_ROOT).as_posix()
        if rel != expected_rel:
            problems.append("backup bundle path differs from the approved custody path")
        bundle = (EVIDENCE_ROOT / rel).resolve()
        try:
            bundle.relative_to(EVIDENCE_ROOT.resolve())
        except ValueError:
            problems.append("backup bundle path escapes the restricted evidence root")
            bundle = None
    if not isinstance(digest, str) or not _HEX64(digest):
        problems.append("backup bundle SHA-256 is invalid")
    if not isinstance(size, int) or isinstance(size, bool) or size <= 0:
        problems.append("backup bundle size must be a positive integer")
    if bundle is not None:
        if bundle.parent != restricted_dir:
            problems.append("backup bundle must be directly inside restricted-backups")
        if bundle.resolve().is_relative_to(repo_root.resolve()):
            problems.append("backup bundle must remain outside Git")
        elif not bundle.is_file():
            problems.append("backup bundle bytes are missing")
        else:
            if isinstance(digest, str) and _HEX64(digest) and _sha256_file(bundle) != digest:
                problems.append("backup bundle digest differs from custody receipt")
            if isinstance(size, int) and not isinstance(size, bool) and bundle.stat().st_size != size:
                problems.append("backup bundle size differs from custody receipt")
            if bundle.stat().st_mode & 0o777 != 0o600:
                problems.append("backup bundle mode must be 0600")
            try:
                verified = subprocess.run(
                    ["git", "bundle", "verify", str(bundle)], cwd=repo_root,
                    capture_output=True, text=True, check=False)
                if verified.returncode != 0:
                    problems.append("git bundle verify failed for restricted backup")
            except OSError as exc:
                problems.append(f"git bundle verify could not run: {exc}")
    if custody.get("custody_directory_mode_octal") != "0700":
        problems.append("backup custody directory must be documented as mode 0700")
    if custody.get("bundle_file_mode_octal") != "0600":
        problems.append("backup file mode must be documented as 0600")
    if custody.get("retained_outside_git") is not True:
        problems.append("backup must be retained outside Git")
    verification = custody.get("verification")
    if not isinstance(verification, dict) or verification.get(
            "git_bundle_verify") != "PASS":
        problems.append("backup custody must record a successful git bundle verify")
    else:
        verified_utc = verification.get("verified_utc")
        if not isinstance(verified_utc, str):
            problems.append("backup verification timestamp is missing")
        else:
            try:
                parsed_time = datetime.fromisoformat(
                    verified_utc.replace("Z", "+00:00"))
                if parsed_time.tzinfo is None:
                    problems.append("backup verification timestamp needs a timezone")
            except ValueError:
                problems.append("backup verification timestamp must be ISO-8601")
    return problems, custody


def build(repo_root: Path, receipt_path: Path, release_head: str,
          tested_content_head: str, allowed_diff: list[str],
          remote_inventory: dict, supersedes: str | None = None) -> dict:
    repo_root = Path(repo_root).resolve()
    receipt_path = Path(receipt_path).resolve()
    science_dir = (repo_root / "docs/science").resolve()
    try:
        receipt_relpath = receipt_path.relative_to(science_dir).as_posix()
    except ValueError as exc:
        raise ClosureError("suite receipt must be inside docs/science") from exc
    if not _HEX40(release_head) or not _HEX40(tested_content_head):
        raise ClosureError("release and tested content heads must be 40-hex commits")
    if release_head != _git(repo_root, "rev-parse", "HEAD"):
        raise ClosureError("release_head must be the live repository HEAD")
    try:
        _git(repo_root, "cat-file", "-e", f"{tested_content_head}^{{commit}}")
    except ClosureError as exc:
        raise ClosureError("tested_content_head does not resolve to a commit") from exc
    if subprocess.run(["git", "merge-base", "--is-ancestor", tested_content_head,
                       release_head], cwd=repo_root, capture_output=True,
                       check=False).returncode != 0:
        raise ClosureError("tested_content_head must be an ancestor of release_head")
    declared_diff = sorted(set(allowed_diff))
    actual_diff = sorted(filter(None, _git(
        repo_root, "diff", "--name-only", f"{tested_content_head}..{release_head}").splitlines()))
    if actual_diff != declared_diff:
        raise ClosureError("declared release-only paths do not match the actual diff")
    if any(not _PERMITTED_RELEASE_PATH.fullmatch(path) for path in actual_diff):
        raise ClosureError("release-only diff contains paths outside receipt/exclusion evidence")
    if _git(repo_root, "status", "--porcelain", "--untracked-files=all"):
        raise ClosureError("refusing to build closure with a dirty worktree")
    manifest = repo_root / "docs/science/ARTIFACT_MANIFEST_V0.json"
    exclusions = repo_root / "docs/science/MANIFEST_SCOPE_EXCLUSIONS_V0.json"
    if not manifest.is_file() or not exclusions.is_file():
        raise ClosureError("manifest or scope exclusions are missing")
    manifest_digest = _sha256_file(manifest)
    receipt_digest = _sha256_file(receipt_path)
    receipt_problems, receipt_doc = _receipt_problems(
        receipt_path, receipt_digest, tested_content_head, manifest_digest)
    try:
        manifest_doc = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClosureError(f"manifest is unreadable JSON: {exc}") from exc
    if not isinstance(manifest_doc, dict) or manifest_doc.get(
            "schema") != "ARTIFACT_MANIFEST_V0":
        raise ClosureError("manifest root/schema is invalid")
    for field in ("content_head", "manifest_commit"):
        if not isinstance(manifest_doc.get(field), str) or not _HEX40(
                manifest_doc[field]):
            raise ClosureError(f"manifest {field} must be a 40-hex commit")
    if receipt_doc is not None and (
            receipt_doc.get("content_head") != manifest_doc.get("content_head")
            or receipt_doc.get("manifest_commit") != manifest_doc.get("manifest_commit")):
        receipt_problems.append("receipt manifest fields differ from live manifest")
    if receipt_problems:
        raise ClosureError("receipt is not closure-ready: " + "; ".join(receipt_problems))
    backup_problems, backup_doc = _backup_custody_problems(
        BACKUP_CUSTODY_PATH, repo_root)
    if backup_problems:
        raise ClosureError("backup custody is not closure-ready: "
                           + "; ".join(backup_problems))
    remote_problems = _validate_remote_inventory(remote_inventory)
    if remote_problems:
        raise ClosureError("remote inventory invalid: " + "; ".join(remote_problems))
    try:
        configured_origin = _clean_remote_url(
            _git(repo_root, "remote", "get-url", "origin"))
    except ClosureError as exc:
        raise ClosureError("configured origin is unavailable") from exc
    if remote_inventory["remote_url"] != configured_origin:
        raise ClosureError("remote inventory URL differs from configured origin")
    if remote_inventory["refs"].get("refs/heads/main") != release_head:
        raise ClosureError("origin/main does not point at release_head")
    if not supersedes or Path(supersedes).name != supersedes:
        raise ClosureError("a safe predecessor closure filename is required")
    predecessor_path = EVIDENCE_DIR / supersedes
    predecessor_sidecar_problems = _sidecar_problems(predecessor_path)
    if predecessor_sidecar_problems:
        raise ClosureError("predecessor closure is not byte-bound: "
                           + "; ".join(predecessor_sidecar_problems))
    try:
        predecessor_doc = json.loads(predecessor_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ClosureError(f"predecessor closure is unreadable: {exc}") from exc
    if (not isinstance(predecessor_doc, dict)
            or predecessor_doc.get("schema") != "INDIA_PHASE0_RELEASE_CLOSURE_V0"
            or predecessor_doc.get("version") != 0):
        raise ClosureError("predecessor is not an India Phase-0 release closure")
    scan = _history_scan(repo_root)
    if not scan["clean"]:
        raise ClosureError("reachable history contains identified payload paths")
    remote_scan = _remote_history_scan(repo_root, remote_inventory)
    if not remote_scan["clean"]:
        raise ClosureError(
            "advertised remote history contains identified payload paths")
    return {
        "schema": SCHEMA,
        "version": 1,
        "claim_scope": "research_only_no_operational_authorization",
        "authority": dict(AUTHORITY_FLAGS),
        "built_utc": datetime.now(timezone.utc).isoformat(),
        "release_head": release_head,
        "tested_content_head": tested_content_head,
        "baseline_head": BASELINE_HEAD,
        "manifest_sha256": manifest_digest,
        "scope_exclusions_sha256": _sha256_file(exclusions),
        "suite_receipt_relpath": receipt_relpath,
        "suite_receipt_sha256": receipt_digest,
        "backup_custody_receipt": {
            "path": "restricted-backups/NEPAL_BACKUP_CUSTODY_V1.json",
            "sha256": _sha256_file(BACKUP_CUSTODY_PATH),
            "bundle_sha256": backup_doc["bundle_sha256"],
        },
        "allowed_release_only_paths": sorted(set(allowed_diff)),
        "clean_history_scan": scan,
        "remote_history_scan": remote_scan,
        "remote_ref_inventory": remote_inventory,
        "supersedes": {"file": supersedes,
                        "sha256": _sha256_file(predecessor_path)},
        "disclosures": [
            "GitHub forks, caches, and prior clones may retain scrubbed payload bytes; this closure verifies only current reachable remote refs.",
            "The pre-rewrite backup is held offline in a mode-restricted directory and contains historical repository bytes; it is not published.",
            "Backup custody v0 is retained append-only; custody v1 supersedes its placeholder verification time with a measured UTC timestamp.",
            "Checksums establish byte integrity, not source authenticity or legal permission.",
        ],
    }


def validate_closure(closure_path: Path, repo_root: Path,
                     live_remote_inventory: dict | None = None) -> dict:
    repo_root = Path(repo_root).resolve()
    closure_path = Path(closure_path)
    problems: list[str] = []
    if not closure_path.is_file():
        return {"status": "CLOSURE_FAIL", "problems": ["closure missing"],
                "closure": str(closure_path)}
    problems.extend(_sidecar_problems(closure_path))
    try:
        doc = json.loads(closure_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"status": "CLOSURE_FAIL",
                "problems": problems + [f"closure unreadable or invalid JSON: {exc}"],
                "closure": str(closure_path)}
    if not isinstance(doc, dict):
        return {"status": "CLOSURE_FAIL",
                "problems": problems + ["closure root must be an object"],
                "closure": str(closure_path)}
    if doc.get("schema") != SCHEMA or doc.get("version") != 1:
        problems.append("unexpected closure schema/version")
    if doc.get("claim_scope") != "research_only_no_operational_authorization":
        problems.append("claim_scope must remain research-only")
    if doc.get("authority") != AUTHORITY_FLAGS:
        problems.append("authority flags must be present and false")
    custody_binding = doc.get("backup_custody_receipt")
    if (not isinstance(custody_binding, dict)
            or custody_binding.get("path") !=
            "restricted-backups/NEPAL_BACKUP_CUSTODY_V1.json"
            or not isinstance(custody_binding.get("sha256"), str)
            or not _HEX64(custody_binding.get("sha256", ""))):
        problems.append("backup custody receipt binding is missing or malformed")
    else:
        custody_path = EVIDENCE_ROOT / custody_binding["path"]
        custody_problems, custody = _backup_custody_problems(
            custody_path, repo_root)
        problems.extend(f"backup custody: {problem}"
                        for problem in custody_problems)
        if custody is not None:
            if _sha256_file(custody_path) != custody_binding["sha256"]:
                problems.append("backup custody receipt digest mismatch")
            if custody.get("bundle_sha256") != custody_binding.get("bundle_sha256"):
                problems.append("bound backup bundle digest differs from custody receipt")
    predecessor = doc.get("supersedes")
    if (not isinstance(predecessor, dict)
            or not isinstance(predecessor.get("file"), str)
            or Path(predecessor.get("file", "")).name != predecessor.get("file")
            or not isinstance(predecessor.get("sha256"), str)
            or not _HEX64(predecessor.get("sha256", ""))):
        problems.append("supersedes must bind a safe filename and SHA-256")
    else:
        predecessor_path = closure_path.parent / predecessor["file"]
        problems.extend(f"supersedes: {problem}" for problem in
                        _sidecar_problems(predecessor_path,
                                          predecessor["sha256"]))
        try:
            predecessor_doc = json.loads(
                predecessor_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"supersedes predecessor is unreadable: {exc}")
        else:
            if (not isinstance(predecessor_doc, dict)
                    or predecessor_doc.get("schema") !=
                    "INDIA_PHASE0_RELEASE_CLOSURE_V0"
                    or predecessor_doc.get("version") != 0):
                problems.append("supersedes predecessor schema/version is invalid")
    for field in ("release_head", "tested_content_head", "baseline_head"):
        head = doc.get(field)
        if not isinstance(head, str) or not _HEX40(head):
            problems.append(f"{field} must be a 40-hex commit")
            continue
        proc = subprocess.run(["git", "cat-file", "-e", f"{head}^{{commit}}"],
                              cwd=repo_root, capture_output=True)
        if proc.returncode:
            problems.append(f"{field} {head} does not resolve")
    if doc.get("baseline_head") != BASELINE_HEAD:
        problems.append("baseline_head differs from the frozen post-rewrite baseline")
    live_head = _git(repo_root, "rev-parse", "HEAD")
    if doc.get("release_head") != live_head:
        problems.append("release_head does not match live HEAD")
    if _git(repo_root, "status", "--porcelain", "--untracked-files=all"):
        problems.append("worktree is not clean")

    manifest = repo_root / "docs/science/ARTIFACT_MANIFEST_V0.json"
    exclusions = repo_root / "docs/science/MANIFEST_SCOPE_EXCLUSIONS_V0.json"
    for path, field in ((manifest, "manifest_sha256"),
                        (exclusions, "scope_exclusions_sha256")):
        digest = doc.get(field)
        if not path.is_file():
            problems.append(f"bound file missing: {path.name}")
        elif not isinstance(digest, str) or not _HEX64(digest):
            problems.append(f"{field} must be a SHA-256 digest")
        elif _sha256_file(path) != digest:
            problems.append(f"{field} mismatch")
    try:
        manifest_doc = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        manifest_doc = None
        problems.append(f"manifest is unreadable: {exc}")
    if not isinstance(manifest_doc, dict) or manifest_doc.get(
            "schema") != "ARTIFACT_MANIFEST_V0":
        problems.append("manifest root/schema is invalid")
    elif any(not isinstance(manifest_doc.get(field), str)
             or not _HEX40(manifest_doc[field])
             for field in ("content_head", "manifest_commit")):
        problems.append("manifest content_head/manifest_commit are invalid")

    rel = doc.get("suite_receipt_relpath")
    receipt_digest = doc.get("suite_receipt_sha256")
    if (not isinstance(rel, str) or not rel or Path(rel).is_absolute()
            or ".." in Path(rel).parts):
        problems.append("suite_receipt_relpath must be a safe docs/science relative path")
        receipt_path = None
    else:
        receipt_path = (repo_root / "docs/science" / rel).resolve()
        try:
            receipt_path.relative_to((repo_root / "docs/science").resolve())
        except ValueError:
            problems.append("suite receipt escapes docs/science")
            receipt_path = None
    if receipt_path is not None and isinstance(receipt_digest, str):
        receipt_issues, receipt = _receipt_problems(
            receipt_path, receipt_digest,
            doc.get("tested_content_head", ""),
            doc.get("manifest_sha256", ""))
        problems.extend(receipt_issues)
        if receipt is not None:
            if isinstance(manifest_doc, dict) and receipt.get(
                    "manifest_commit") != manifest_doc.get("manifest_commit"):
                problems.append("receipt manifest_commit differs from live manifest")
            if isinstance(manifest_doc, dict) and receipt.get(
                    "content_head") != manifest_doc.get("content_head"):
                problems.append("receipt content_head differs from live manifest")
    else:
        problems.append("suite receipt digest/path is invalid")

    tested = doc.get("tested_content_head")
    release = doc.get("release_head")
    allowed = doc.get("allowed_release_only_paths")
    if not isinstance(allowed, list) or any(not isinstance(x, str) for x in allowed):
        problems.append("allowed_release_only_paths must be a list of paths")
    elif isinstance(tested, str) and _HEX40(tested) and isinstance(release, str) and _HEX40(release):
        if subprocess.run(["git", "merge-base", "--is-ancestor", tested,
                           release], cwd=repo_root, capture_output=True,
                          check=False).returncode != 0:
            problems.append("tested_content_head is not an ancestor of release_head")
        diff = _git(repo_root, "diff", "--name-only", f"{tested}..{release}")
        actual = sorted(p for p in diff.splitlines() if p)
        if actual != sorted(allowed):
            problems.append(f"release-only diff {actual} != declared {sorted(allowed)}")
        unexpected = sorted(path for path in actual
                            if not _PERMITTED_RELEASE_PATH.fullmatch(path))
        if unexpected:
            problems.append("release-only diff contains non-evidence paths: "
                            + ", ".join(unexpected))

    recorded_scan = doc.get("clean_history_scan")
    try:
        actual_scan = _history_scan(repo_root)
    except ClosureError as exc:
        problems.append(str(exc))
        actual_scan = None
    if not isinstance(recorded_scan, dict):
        problems.append("clean_history_scan must be an object")
    elif actual_scan is not None:
        if recorded_scan != actual_scan:
            problems.append("clean_history_scan does not match recomputed reachable-object scan")
        if not actual_scan["clean"]:
            problems.append("identified payload paths remain in reachable history")

    remote_issues = _validate_remote_inventory(doc.get("remote_ref_inventory"))
    problems.extend(remote_issues)
    if not remote_issues:
        recorded_inventory = doc["remote_ref_inventory"]
        if recorded_inventory["refs"].get(
                "refs/heads/main") != doc.get("release_head"):
            problems.append("recorded origin/main does not match release_head")
        try:
            configured_origin = _clean_remote_url(
                _git(repo_root, "remote", "get-url", "origin"))
            if recorded_inventory["remote_url"] != configured_origin:
                problems.append(
                    "recorded remote URL differs from configured origin")
        except ClosureError as exc:
            problems.append(f"configured origin is unavailable: {exc}")
        try:
            actual_remote_scan = _remote_history_scan(
                repo_root, recorded_inventory)
        except ClosureError as exc:
            problems.append(str(exc))
        else:
            recorded_remote_scan = doc.get("remote_history_scan")
            if recorded_remote_scan != actual_remote_scan:
                problems.append(
                    "remote_history_scan does not match advertised remote refs")
            if not actual_remote_scan["clean"]:
                problems.append(
                    "identified payload paths remain in advertised remote history")
    elif doc.get("remote_history_scan") is None:
        problems.append("remote_history_scan is missing")
    if live_remote_inventory is not None:
        live_issues = _validate_remote_inventory(live_remote_inventory)
        problems.extend(f"live remote: {issue}" for issue in live_issues)
        if not live_issues:
            recorded = doc.get("remote_ref_inventory")
            for key in ("remote_name", "remote_url", "inventory_scope",
                        "head_oid", "refs", "symrefs"):
                if recorded.get(key) != live_remote_inventory.get(key):
                    problems.append(f"recorded remote {key} differs from live remote")
            if live_remote_inventory.get("refs", {}).get(
                    "refs/heads/main") != doc.get("release_head"):
                problems.append("live origin/main does not match release_head")

    status = "CLOSURE_OK" if not problems else "CLOSURE_FAIL"
    return {"status": status, "problems": problems,
            "closure": str(closure_path)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["build", "validate"])
    parser.add_argument("--receipt", type=Path)
    parser.add_argument("--closure", type=Path,
                        default=EVIDENCE_DIR / "INDIA_PHASE0_RELEASE_CLOSURE_V3.json")
    parser.add_argument("--release-head")
    parser.add_argument("--tested-content-head")
    parser.add_argument("--allowed-diff", nargs="*", default=[])
    parser.add_argument("--remote-inventory", type=Path,
                        help="JSON file captured from git ls-remote")
    parser.add_argument("--remote-name", default="origin")
    parser.add_argument("--verify-remote", action="store_true",
                        help="compare the closure with a fresh live ls-remote snapshot")
    parser.add_argument("--supersedes")
    args = parser.parse_args()
    try:
        if args.mode == "build":
            if args.receipt is None or args.release_head is None or \
                    args.tested_content_head is None:
                parser.error("build requires --receipt, --release-head, "
                             "--tested-content-head")
            inventory = capture_remote_inventory(REPO, args.remote_name)
            if args.remote_inventory is not None:
                supplied = json.loads(
                    args.remote_inventory.read_text(encoding="utf-8"))
                issues = _validate_remote_inventory(supplied)
                if issues:
                    raise ClosureError("supplied remote inventory invalid: "
                                       + "; ".join(issues))
                for key in ("remote_name", "remote_url", "inventory_scope",
                            "head_oid", "refs", "symrefs"):
                    if supplied.get(key) != inventory.get(key):
                        raise ClosureError(
                            f"supplied remote inventory {key} differs from live origin")
            doc = build(REPO, args.receipt, args.release_head,
                        args.tested_content_head, args.allowed_diff,
                        inventory, args.supersedes)
            after = capture_remote_inventory(REPO, args.remote_name)
            for key in ("remote_name", "remote_url", "inventory_scope",
                        "head_oid", "refs", "symrefs"):
                if after.get(key) != inventory.get(key):
                    raise ClosureError(
                        f"remote {key} changed while closure was being built")
            args.closure.parent.mkdir(parents=True, exist_ok=True)
            import sys
            sys.path.insert(0, str(REPO / "scripts"))
            from p5_safe_io import write_once_json, write_once_sidecar
            write_once_json(args.closure, doc)
            write_once_sidecar(args.closure)
            print(f"CLOSURE_PUBLISHED: {args.closure}")
            return 0
        live = capture_remote_inventory(REPO, args.remote_name) if args.verify_remote else None
        report = validate_closure(args.closure, REPO, live)
        print(json.dumps(report, indent=2, sort_keys=True))
        return 0 if report["status"] == "CLOSURE_OK" else 1
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"CLOSURE_FAIL: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
