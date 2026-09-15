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
    notes = ledger.setdefault("timestamp_notes", [])
    for field in ("start_time", "end_time"):
        raw = ledger.get(field)
        if isinstance(raw, str) and not raw.endswith("Z") \
                and "+" not in raw:
            utc = _naive_to_utc(raw)
            ledger[f"{field}_utc"] = utc or "UNPARSEABLE"
            notes.append(
                f"{field}: original value was naive local time; "
                f"{field}_utc interprets it as system-local")
    ledger["repaired_at_utc"] = utc_now_iso()

    ledger_path.write_text(json.dumps(ledger, indent=2, sort_keys=True)
                           + "\n", encoding="utf-8")
    return ledger


def write_provenance_receipts(run_root: Path) -> Path:
    """Bind recoverable receipts for each retrieval route (H02)."""
    run_root = Path(run_root)
    ledger = json.loads(
        (run_root / "download_ledger.json").read_text(encoding="utf-8"))
    receipts = {"run_root": str(run_root),
                "generated_utc": utc_now_iso(),
                "dataset": ledger.get("dataset", "UNVERIFIED"),
                "routes": {}}
    for varset, route in ledger.get("retrieval_paths", {}).items():
        meta = ROUTE_META.get(route, {"provider": "UNVERIFIED",
                                      "license_url": "UNVERIFIED",
                                      "license_id": "UNVERIFIED"})
        payloads = []
        raw_dir = run_root / "raw"
        if raw_dir.is_dir():
            for p in sorted(raw_dir.iterdir()):
                if p.is_file():
                    payloads.append({
                        "file": p.name,
                        "sha256": sha256_file(p),
                        "size_bytes": p.stat().st_size,
                        "retrieval_utc_mtime_derived": datetime.fromtimestamp(
                            p.stat().st_mtime, timezone.utc
                        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    })
        receipts["routes"][varset] = {
            "endpoint": route,
            "provider": meta["provider"],
            "license_id": meta["license_id"],
            "license_url": meta["license_url"],
            # Job/request IDs are not recoverable from a completed run
            # root — recorded honestly as unverified.
            "response_job_id": "UNVERIFIED",
            "request_digest": "UNVERIFIED",
            "source_version": "UNVERIFIED",
            "payloads": payloads,
        }
    out = run_root / "provenance_receipts.json"
    out.write_text(json.dumps(receipts, indent=2, sort_keys=True)
                   + "\n", encoding="utf-8")
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-root", required=True)
    args = p.parse_args(argv)
    run_root = Path(args.run_root)
    if not (run_root / "download_ledger.json").is_file():
        print(f"no download_ledger.json under {run_root}",
              file=sys.stderr)
        return 1
    ledger = repair_ledger(run_root)
    receipts = write_provenance_receipts(run_root)
    print(f"repaired ledger: total_size_mb="
          f"{ledger['total_size_mb']} "
          f"(independent of caller numbers)")
    print(f"receipts: {receipts}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
