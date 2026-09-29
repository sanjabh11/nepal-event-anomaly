"""Append-only correction of the metadata-access disclosure for the HMA screen."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
from typing import Any

import hma_lake_trajectory_poc as trajectory_v1

ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence/india-phase0-source-intake")
SOURCE = ROOT / "hma-lake-trajectory-poc-v2/HMA_HIGHER_FREQUENCY_ASSESSMENT_V0.json"
OUTPUT = ROOT / "hma-lake-trajectory-poc-v2/HMA_HIGHER_FREQUENCY_ASSESSMENT_V1.json"
SCHEMA = "HMA_HIGHER_FREQUENCY_ASSESSMENT_V1"

METADATA_REFERENCES = [
    {
        "url": "https://essd.copernicus.org/articles/13/741/2021/",
        "description": "Hi-MAG annual inventory paper; metadata page opened",
    },
    {
        "url": "https://essd.copernicus.org/articles/18/5143/2026/",
        "description": "GLO annual inventory paper; metadata page opened",
    },
    {
        "url": "https://www.nature.com/articles/s41561-023-01150-1",
        "description": "Greater Himalaya source paper; search result consulted, page access was blocked",
    },
    {
        "url": "https://www.sciencedirect.com/science/article/pii/S0167947306004622",
        "description": "Hennig (2007) cluster-stability methods paper abstract consulted",
    },
]


def _sha256_file(path: Path) -> str:
    return trajectory_v1.sha256_file(path)


def build_assessment(source: dict[str, Any], source_sha256: str) -> dict[str, Any]:
    if source.get("schema") != "HMA_HIGHER_FREQUENCY_ASSESSMENT_V0":
        raise ValueError("expected the sealed V0 metadata assessment")
    document = copy.deepcopy(source)
    document.pop("payload_or_network_request_made", None)
    document.update({
        "schema": SCHEMA,
        "version": 1,
        "supersedes": {
            "artifact": SOURCE.name,
            "sha256": source_sha256,
            "correction": "V0 incorrectly combined payload and network activity into one false-valued field.",
        },
        "access_disclosure": {
            "metadata_web_searches_performed": True,
            "primary_article_pages_opened": True,
            "search_result_consulted_but_page_blocked": True,
            "dataset_payload_endpoint_requested": False,
            "dataset_payload_downloaded_or_retained": False,
            "new_scientific_data_acquisition": False,
            "data_sources_accessed": METADATA_REFERENCES,
        },
        "access_field_superseded_reason": (
            "Ambiguous compound field removed: publication metadata was accessed over the web, "
            "but no dataset payload endpoint was requested and no payload was downloaded."
        ),
        "generator_script_sha256": _sha256_file(Path(__file__)),
    })
    for candidate in document.get("candidates", []):
        candidate.pop("payload_or_network_request_made", None)
        candidate["metadata_web_search_or_paper_access"] = True
        candidate["dataset_payload_endpoint_requested"] = False
        candidate["dataset_payload_downloaded_or_retained"] = False
    return document


def _load_source() -> tuple[dict[str, Any], str]:
    document, digest = trajectory_v1._read_bound_json(SOURCE)
    return document, digest


def run() -> dict[str, Any]:
    source, source_sha = _load_source()
    expected = build_assessment(source, source_sha)
    if OUTPUT.exists():
        actual, actual_sha = trajectory_v1._read_bound_json(OUTPUT)
        if actual != expected:
            raise FileExistsError(f"write-once assessment differs: {OUTPUT}")
        return {"status": "ASSESSMENT_ALREADY_SEALED", "sha256": actual_sha,
                "path": str(OUTPUT)}
    trajectory_v1.write_once_json(OUTPUT, expected)
    digest = trajectory_v1.write_once_sidecar(OUTPUT)
    return {"status": "ASSESSMENT_SEALED", "sha256": digest, "path": str(OUTPUT)}


def verify() -> dict[str, Any]:
    source, source_sha = _load_source()
    expected = build_assessment(source, source_sha)
    actual, actual_sha = trajectory_v1._read_bound_json(OUTPUT)
    if actual != expected:
        raise ValueError("corrected assessment does not match independent recomputation")
    return {"status": "ASSESSMENT_VERIFY_OK", "sha256": actual_sha,
            "source_sha256": source_sha}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("run", "verify"))
    args = parser.parse_args()
    try:
        result = run() if args.command == "run" else verify()
    except (OSError, ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
        print(f"HIGH_FREQUENCY_ASSESSMENT_{args.command.upper()}_BLOCKED: {exc}")
        return 1
    print(result["status"])
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
