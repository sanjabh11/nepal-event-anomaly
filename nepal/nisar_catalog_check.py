"""NISAR catalog check for Nepal Event — Phase 1.

Queries NASA CMR for NISAR GUNW/GOFF pairs at the source point.
Records acquisition, production, and publication timestamps.
Does NOT download or process data — metadata only.

This implements Astra's recommendation:
"prioritize an acquisition-and-release ledger plus a few GUNW/GOFF quality checks"

Output:
    data/nisar_catalog_ledger.json
"""
from __future__ import annotations

import json
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime

# Source point (HiRisk reference)
SOURCE_LAT = 28.28858
SOURCE_LON = 85.52701

# Event time
EVENT_TIME = "2026-08-26T02:52:00Z"

# Search period: from NISAR provisional start to event
SEARCH_START = "2025-07-30T00:00:00Z"
SEARCH_END = "2026-08-26T02:52:00Z"

# CMR base URL
CMR_BASE = "https://cmr.earthdata.nasa.gov/search/granules.json"

# NISAR collection concept IDs (from Astra's verified queries)
COLLECTIONS = {
    "nisar_gunw_provisional": "C2854335566-ASF",
    "nisar_goff_provisional": "C2854341702-ASF",
    "nisar_gunw_beta": "C2850261892-ASF",
    "nisar_goff_beta": "C2850263910-ASF",
}

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
OUTPUT_FILE = DATA_DIR / "nisar_catalog_ledger.json"


def query_cmr(collection_id: str, lat: float, lon: float,
              start: str, end: str, page_size: int = 200) -> list[dict]:
    """Query CMR for granules at a point within a time range."""
    params = {
        "collection_concept_id": collection_id,
        "point": f"{lon},{lat}",
        "temporal": f"{start},{end}",
        "page_size": str(page_size),
    }
    url = f"{CMR_BASE}?{urllib.parse.urlencode(params)}"

    try:
        req = urllib.request.Request(url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode())
            return data.get("feed", {}).get("entry", [])
    except Exception as e:
        print(f"  Error querying {collection_id}: {e}")
        return []


def extract_granule_info(entry: dict) -> dict:
    """Extract relevant metadata from a CMR granule entry."""
    # Get time-related fields
    time_start = entry.get("time_start", "")
    time_end = entry.get("time_end", "")
    updated = entry.get("updated", "")

    # Get data provider IDs
    granule_ur = entry.get("granule_ur", "")
    producer_granule_id = entry.get("producer_granule_id", "")

    # Get collection
    collection = entry.get("collection_concept_id", "")

    # Get size
    size_mb = 0
    for d in entry.get("data_granule", []):
        size_mb = d.get("size", 0)
        size_unit = d.get("size_unit", "MB")
        if size_unit == "GB":
            size_mb *= 1024

    return {
        "granule_ur": granule_ur,
        "producer_granule_id": producer_granule_id,
        "collection": collection,
        "acquisition_start": time_start,
        "acquisition_end": time_end,
        "catalog_updated": updated,
        "size_mb": round(size_mb, 1),
    }


def is_pre_event(granule: dict, event_time: str) -> bool:
    """Check if a granule's acquisition ends before the event."""
    acq_end = granule.get("acquisition_end", "")
    if not acq_end:
        return False
    # Simple string comparison works for ISO timestamps
    return acq_end < event_time


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)

    print("NISAR Catalog Check — Nepal Event")
    print(f"Source point: ({SOURCE_LAT}°N, {SOURCE_LON}°E)")
    print(f"Search period: {SEARCH_START} to {SEARCH_END}")
    print(f"Event time: {EVENT_TIME}")
    print()

    ledger = {
        "query_time": datetime.now().isoformat(),
        "source_point": (SOURCE_LAT, SOURCE_LON),
        "search_period": (SEARCH_START, SEARCH_END),
        "event_time": EVENT_TIME,
        "collections_queried": {},
        "pre_event_granules": [],
        "post_event_granules": [],
        "publication_latency_findings": {},
    }

    for name, collection_id in COLLECTIONS.items():
        print(f"Querying {name} ({collection_id})...")
        entries = query_cmr(collection_id, SOURCE_LAT, SOURCE_LON,
                           SEARCH_START, SEARCH_END)
        print(f"  Found {len(entries)} granules")

        granules = [extract_granule_info(e) for e in entries]

        # Separate pre-event and post-event
        pre = [g for g in granules if is_pre_event(g, EVENT_TIME)]
        post = [g for g in granules if not is_pre_event(g, EVENT_TIME)]

        ledger["collections_queried"][name] = {
            "collection_id": collection_id,
            "total_granules": len(granules),
            "pre_event": len(pre),
            "post_event": len(post),
        }

        for g in pre:
            g["collection_name"] = name
            ledger["pre_event_granules"].append(g)

        for g in post:
            g["collection_name"] = name
            ledger["post_event_granules"].append(g)

    # Publication latency analysis (Astra's key finding)
    print("\nPublication latency analysis...")
    for g in ledger["pre_event_granules"]:
        acq_end = g.get("acquisition_end", "")
        catalog_updated = g.get("catalog_updated", "")
        if acq_end and catalog_updated:
            # Time between acquisition end and catalog update
            try:
                acq_dt = datetime.fromisoformat(acq_end.replace("Z", "+00:00"))
                pub_dt = datetime.fromisoformat(catalog_updated.replace("Z", "+00:00"))
                latency_hours = (pub_dt - acq_dt).total_seconds() / 3600
                g["publication_latency_hours"] = round(latency_hours, 1)

                # Time between publication and event
                event_dt = datetime.fromisoformat(EVENT_TIME.replace("Z", "+00:00"))
                warning_lead_hours = (event_dt - pub_dt).total_seconds() / 3600
                g["warning_lead_hours"] = round(warning_lead_hours, 1)

                if warning_lead_hours < 72:  # Less than 3 days
                    g["latency_trap"] = "WARNING: publication less than 3 days before event"
            except Exception:
                pass

    # Summary
    total_pre = len(ledger["pre_event_granules"])
    total_post = len(ledger["post_event_granules"])
    print(f"\nSummary:")
    print(f"  Pre-event granules: {total_pre}")
    print(f"  Post-event granules (excluded): {total_post}")

    if ledger["pre_event_granules"]:
        print(f"\n  Pre-event GUNW/GOFF pairs:")
        for g in ledger["pre_event_granules"]:
            lat_h = g.get("publication_latency_hours", "?")
            lead_h = g.get("warning_lead_hours", "?")
            trap = g.get("latency_trap", "")
            print(f"    {g['collection_name']}: {g['acquisition_start']} → {g['acquisition_end']}"
                  f"  (pub latency: {lat_h}h, warning lead: {lead_h}h) {trap}")

    # Key finding (Astra)
    ledger["publication_latency_findings"] = {
        "astra_finding": "The 26 Jul → 19 Aug GUNW pair was inserted into catalog on 25 Aug — only ~26 hours before event",
        "implication": "Acquisition date alone overstates warning time. Must use publication time for any lead-time claim.",
        "recommendation": "Record both acquisition and publication timestamps. Any warning assessment must use publication cutoff.",
    }

    with open(OUTPUT_FILE, "w") as f:
        json.dump(ledger, f, indent=2)

    print(f"\nLedger saved: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()
