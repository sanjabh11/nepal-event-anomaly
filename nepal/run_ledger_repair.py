#!/usr/bin/env python3
"""Run-ledger repair + provenance receipts (audit gaps H02/H06/H07).

Repairs an existing run root's ``download_ledger.json`` in place:

- recomputes per-stage (raw/monthly/merged) and total sizes from the
  actual bytes on disk — never caller-supplied numbers (H06);
- normalizes timestamps: original naive ``start_time``/``end_time``
  values are preserved verbatim and re-exposed as ``*_utc`` fields,
  interpreted as system-local time and converted to explicit UTC
  (documented assumption — no silent rewrite) (H07);
- writes ``provenance_receipts.json``: one receipt per retrieval route
  with endpoint, payload digests, sizes, retrieval timestamps (file
  mtime, marked as mtime-derived), dataset id, license URL, and the
  variable mapping.  Anything not recoverable from the run root is
  recorded as ``UNVERIFIED`` — receipts are never fabricated (H02).

Usage:
    python -m nepal.run_ledger_repair --run-root <path>
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

STAGES = ("raw", "monthly", "merged")

# License/landing pages for the routes the hybrid fetcher used. These
# are reference URLs, not fetched content — verification of license
# text is a separate evidence lane.
ROUTE_META = {
    "reanalysis-era5-land-timeseries": {
        "provider": "ECMWF CDS (ARCO copy)",
        "license_url": "https://cds.climate.copernicus.eu/datasets/"
                       "reanalysis-era5-land-timeseries",
        "license_id": "copernicus-license (CC BY 4.0 attribution)",
    },
    "reanalysis-era5-land": {
        "provider": "ECMWF CDS / MARS",
        "license_url": "https://cds.climate.copernicus.eu/datasets/"
                       "reanalysis-era5-land",
        "license_id": "copernicus-license (CC BY 4.0 attribution)",
    },
    "earthdatahub destine mirror of reanalysis-era5-land": {
        "provider": "Earth Data Hub (DestinE mirror)",
        "license_url": "https://earthdatahub.destine.eu/"
                       "copernicus-gs-buttons/reanalysis-era5-land",
        "license_id": "copernicus-license (mirror of same archive)",
    },
}


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _naive_to_utc(value: str) -> str | None:
    """Interpret a naive ISO timestamp as system-local time and emit
    explicit UTC.  Returns None when unparseable."""
    try:
        dt = datetime.fromisoformat(value)
    except (ValueError, TypeError):
        return None
    if dt.tzinfo is None:
        dt = dt.astimezone()  # assumes system-local wall clock
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _dir_bytes(path: Path) -> int:
    if not path.is_dir():
        return 0
    return sum(p.stat().st_size for p in path.rglob("*") if p.is_file())


def repair_ledger(run_root: Path) -> dict:
    """Recompute sizes + normalize timestamps in place.  Returns the
    repaired ledger dict."""
    run_root = Path(run_root)
    ledger_path = run_root / "download_ledger.json"
    ledger = json.loads(ledger_path.read_text(encoding="utf-8"))

    # H06 — byte-accurate accounting per stage.
    accounting = {
        "definition": ("sum of file bytes under run_root/{raw,monthly,"
                       "merged} measured at repair time"),
    }
    total = 0
    for stage in STAGES:
        b = _dir_bytes(run_root / stage)
        accounting[f"{stage}_bytes"] = b
        total += b
    accounting["total_bytes"] = total
    ledger["size_accounting"] = accounting
    ledger["total_size_mb"] = round(total / (1024 * 1024), 2)

    # Per-month file sizes recomputed from bytes where the normalized
    # file still exists.
    for m in ledger.get("completed_months", []):
        nf = m.get("normalized_file")
        if nf and Path(nf).is_file():
            m["size_mb"] = round(
                Path(nf).stat().st_size / (1024 * 1024), 3)
        elif nf:
            m["size_mb"] = "UNVERIFIED_FILE_MISSING"

    # H07 — naive originals preserved; *_utc fields added with the
    # interpretation documented.
    # Idempotent: notes keyed by field, not appended per run.
    notes = ledger.setdefault("timestamp_notes", [])
    classified_legacy = False
    for field in ("start_time", "end_time"):
        raw = ledger.get(field)
        if not isinstance(raw, str):
            continue
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            ledger[f"{field}_utc"] = "UNPARSEABLE"
            continue
        if dt.tzinfo is None:
            utc = _naive_to_utc(raw)
            ledger[f"{field}_utc"] = utc or "UNPARSEABLE"
            note = (f"{field}: original value was naive local time; "
                    f"{field}_utc interprets it as system-local")
            classified_legacy = True
        else:
            ledger[f"{field}_utc"] = dt.astimezone(
                timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            note = (f"{field}: original value was tz-aware; "
                    f"{field}_utc normalizes to UTC")
        if note not in notes:
            notes.append(note)
    if classified_legacy:
        ledger["timestamp_classification"] = (
            "legacy_naive_local_with_utc_normalization")
    else:
        ledger["timestamp_classification"] = "utc_bound"
    ledger["repaired_at_utc"] = utc_now_iso()

    ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True)
                           + "\n", encoding="utf-8")
    return ledger


def _payload_route(filename: str) -> str | None:
    """Attribute a raw/ file to its retrieval route by name pattern.
    Returns the varset key, ``"assembly_artifact"``, or None."""
    if filename.startswith("assembled_"):
        return "assembly_artifact"
    if filename.startswith("arco_timeseries"):
        return "t2m,d2m,u10,v10,tp"
    if filename.startswith("sd_sf_edh"):
        return "sd,sf (2001-2025)"
    if filename.startswith("sd_sf_2026"):
        return "sd,sf (2026)"
    if filename.startswith("sd_sf_"):
        return "sd,sf (2001-2025)"  # historical chunk naming
    return None


def write_provenance_receipts(run_root: Path) -> Path:
    """Bind recoverable receipts for each retrieval route (H02).

    Payloads are attributed to their actual route by filename; the
    assembly intermediates (``assembled_*``) are recorded separately,
    not as a retrieval route."""
    run_root = Path(run_root)
    ledger = json.loads(
        (run_root / "download_ledger.json").read_text(encoding="utf-8"))
    receipts = {"run_root": str(run_root),
                "generated_utc": utc_now_iso(),
                "dataset": ledger.get("dataset", "UNVERIFIED"),
                "routes": {},
                "assembly_artifacts": [],
                "unattributed": []}
    raw_dir = run_root / "raw"
    if raw_dir.is_dir():
        for p in sorted(raw_dir.iterdir()):
            if not p.is_file():
                continue
            rec = {
                "file": p.name,
                "sha256": sha256_file(p),
                "size_bytes": p.stat().st_size,
                "retrieval_utc_mtime_derived": datetime.fromtimestamp(
                    p.stat().st_mtime, timezone.utc
                ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            }
            route_key = _payload_route(p.name)
            if route_key == "assembly_artifact":
                receipts["assembly_artifacts"].append(rec)
            elif route_key is None:
                receipts["unattributed"].append(rec)
            else:
                receipts["routes"].setdefault(
                    route_key, {"payloads": []})["payloads"].append(rec)
    for varset, route in ledger.get("retrieval_paths", {}).items():
        meta = ROUTE_META.get(route, {"provider": "UNVERIFIED",
                                      "license_url": "UNVERIFIED",
                                      "license_id": "UNVERIFIED"})
        entry = receipts["routes"].setdefault(varset, {"payloads": []})
        entry.update({
            "endpoint": route,
            "provider": meta["provider"],
            "license_id": meta["license_id"],
            "license_url": meta["license_url"],
            # Job/request IDs are not recoverable from a completed run
            # root — recorded honestly as unverified.
            "response_job_id": "UNVERIFIED",
            "request_digest": "UNVERIFIED",
            "source_version": "UNVERIFIED",
        })
    out = run_root / "provenance_receipts.json"
    out.write_text(json.dumps(receipts, indent=2, sort_keys=True)
                   + "\n", encoding="utf-8")
    return out


def rehash_report(run_root: Path) -> Path:
    """RA-09 — independent read-only rehash of every artifact in the
    run root, compared against any digest already recorded in the
    bundle/ledger/marker.  Writes ``rehash_report.json``."""
    run_root = Path(run_root)
    report = {"run_root": str(run_root),
              "generated_utc": utc_now_iso(),
              "files": [],
              "digest_cross_checks": []}
    for p in sorted(run_root.rglob("*")):
        if not p.is_file() or p.name in ("rehash_report.json",):
            continue
        rel = str(p.relative_to(run_root))
        report["files"].append({"relpath": rel,
                                "sha256": sha256_file(p),
                                "size_bytes": p.stat().st_size})
    by_rel = {f["relpath"]: f["sha256"] for f in report["files"]}

    # Cross-check digests the bundle/ledger/marker already recorded.
    bundle = run_root / "gmm" / "bundle.json"
    if bundle.is_file():
        b = json.loads(bundle.read_text())
        for label, recorded in (
                ("results_digest", b.get("results_digest")),
                ("feature_digest", b.get("feature_digest")),
                ("feature_units_digest",
                 b.get("feature_units_digest"))):
            target = {"results_digest": "gmm/gmm_results.json",
                      "feature_digest":
                          "features/features_nepal_jja_2001_2026.csv",
                      "feature_units_digest":
                          "features/feature_units.json"}.get(label)
            if recorded and target in by_rel:
                report["digest_cross_checks"].append({
                    "digest": label, "file": target,
                    "recorded": recorded,
                    "recomputed": by_rel[target],
                    "match": by_rel[target] == recorded})
        for rel, dig in (b.get("input_digests") or {}).items():
            key = str(rel)
            if key in by_rel:
                report["digest_cross_checks"].append({
                    "digest": "input_digests", "file": key,
                    "recorded": dig,
                    "recomputed": by_rel[key],
                    "match": by_rel[key] == dig})
    marker = run_root / "merged" / "complete.json"
    if marker.is_file():
        mk = json.loads(marker.read_text())
        recorded = mk.get("merged_sha256")
        merged = "merged/era5_land_nepal_jja_2001_2026.nc"
        if recorded and merged in by_rel:
            report["digest_cross_checks"].append({
                "digest": "marker.merged_sha256", "file": merged,
                "recorded": recorded,
                "recomputed": by_rel[merged],
                "match": by_rel[merged] == recorded})
    report["all_cross_checks_match"] = all(
        c["match"] for c in report["digest_cross_checks"]) \
        if report["digest_cross_checks"] else None
    out = run_root / "rehash_report.json"
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-root", required=True)
    p.add_argument("--rehash-only", action="store_true",
                   help="only write rehash_report.json (no ledger "
                        "repair or receipts)")
    args = p.parse_args(argv)
    run_root = Path(args.run_root)
    if not (run_root / "download_ledger.json").is_file():
        print(f"no download_ledger.json under {run_root}",
              file=sys.stderr)
        return 1
    if args.rehash_only:
        report = rehash_report(run_root)
        print(f"rehash report: {report}")
        return 0
    ledger = repair_ledger(run_root)
    receipts = write_provenance_receipts(run_root)
    report = rehash_report(run_root)
    print(f"repaired ledger: total_size_mb="
          f"{ledger['total_size_mb']} "
          f"(independent of caller numbers)")
    print(f"receipts: {receipts}")
    print(f"rehash report: {report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
