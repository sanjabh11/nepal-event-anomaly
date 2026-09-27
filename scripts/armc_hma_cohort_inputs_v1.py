"""Derive analyzer-contract inputs for the HMA held-out cohort.

Reads the frozen Option-A cohort manifest (read-only lane) and emits the two
documents the sealed Route-B analyzers consume:

  * hma_episode_map_v1.json       - schema-compatible with the development
                                  episode map's *shape* (P5_EVENT_EPISODE_MAPPING_V0
                                  fields: units[].unit_id/member_ids/lake/...);
                                  one unit per manifest lake_cluster.
  * hma_adjudication_decision_v1.json - P5_EVENT_ADJUDICATION_V1 schema; every
                                  primary-row event declared ELIGIBLE by cohort
                                  definition with a day-precision interval.

The unit's `lake` field carries the manifest lake_cluster token so the
analyser's lake_key() reproduces the v2 clustering exactly. The human lake
names are preserved under `lake_names`.
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
from p5_safe_io import write_once_json, write_once_sidecar  # noqa: E402


def sha256_file(p) -> str:
    import hashlib
    return hashlib.sha256(Path(p).read_bytes()).hexdigest()


def build(manifest: dict):
    if manifest.get("schema") != "P5_OPTIONA_COHORT_MANIFEST_V1":
        raise ValueError("unexpected cohort manifest schema")
    events = manifest["events"]
    if len(events) != manifest.get("n_events"):
        raise ValueError("n_events mismatch")
    gf_ids = [e["gf_id"] for e in events]
    if len(set(gf_ids)) != len(gf_ids):
        raise ValueError("duplicate gf_id in cohort")

    clusters = {}
    for e in events:
        clusters.setdefault(e["lake_cluster"], []).append(e)

    units, decisions = [], []
    ordered = sorted(clusters.items(), key=lambda kv: min(x["date"] for x in kv[1]))
    for i, (cluster, members) in enumerate(ordered, 1):
        members = sorted(members, key=lambda x: x["date"])
        primary = members[0]
        uid = f"hma_{i:02d}"
        units.append({
            "unit_id": uid,
            "member_ids": [m["gf_id"] for m in members],
            "gf_ids": sorted(m["gf_id"] for m in members),
            "lake": cluster,
            "lake_names": sorted({m["lake"] for m in members}),
            "basin": sorted({m["country"] for m in members})[0] if len({m["country"] for m in members}) == 1
                       else "/".join(sorted({m["country"] for m in members})),
            "countries": sorted({m["country"] for m in members}),
            "era": primary["era"],
            "eras_all_members": sorted({m["era"] for m in members}),
            "gorkha_window": False,
            "driver": primary["driver"],
            "stratum_primary_member": primary["adjudicated_stratum"],
            "strata_all_members": sorted({m["adjudicated_stratum"] for m in members}),
            "lat": primary["lat"],
            "lon": primary["lon"],
            "member_coords": {m["gf_id"]: [m["lat"], m["lon"]] for m in members},
            "n_members": len(members),
            "in_hkh_region": bool(primary.get("in_hkh_region")),
        })
        for m in members:
            y, mo, d = m["date"].split("-")
            end = (_dt.date(int(y), int(mo), int(d)) + _dt.timedelta(days=1)).isoformat()
            decisions.append({
                "event_id": f"HMAGLOFDB:{m['gf_id']}",
                "adjudication": {
                    "disposition": "ELIGIBLE",
                    "event_time_interval": {
                        "start": f"{m['date']}T00:00:00Z",
                        "end": f"{end}T00:00:00Z",
                        "precision": "day",
                    },
                    "basis": "cohort primary row; eligibility declared by optionA_cohort_manifest_v2",
                },
                "local": {"lat": m["lat"], "lon": m["lon"], "lake": m["lake"],
                          "country": m["country"], "province": m.get("province", "")},
                "provenance": {"gf_id": m["gf_id"], "stratum": m["adjudicated_stratum"],
                               "date_status": m["date_status"], "label_basis": m["label_basis"],
                               "lake_cluster": m["lake_cluster"], "era": m["era"],
                               "driver": m["driver"], "lake_type": m.get("lake_type", "")},
            })

    episode_map = {
        "schema": "P5_EVENT_EPISODE_MAPPING_V0",
        "derivation": "synthesized from optionA_cohort_manifest_v2 primary rows; "
                      "one unit per lake_cluster token; member_ids are HMAGLOFDB GF_IDs",
        "heldout": True,
        "units": units,
    }
    decision = {
        "schema": "P5_EVENT_ADJUDICATION_V1",
        "derivation": "synthesized from optionA_cohort_manifest_v2 primary rows; "
                      "disposition ELIGIBLE is the cohort declaration, not a re-adjudication",
        "events": decisions,
    }
    return episode_map, decision


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--manifest", required=True)
    ap.add_argument("--out-dir", required=True)
    a = ap.parse_args()
    manifest = json.loads(Path(a.manifest).read_text())
    em, dec = build(manifest)
    out = Path(a.out_dir)
    em_path = out / "hma_episode_map_v1.json"
    dec_path = out / "hma_adjudication_decision_v1.json"
    for p, doc in ((em_path, em), (dec_path, dec)):
        doc["manifest_sha256"] = sha256_file(a.manifest)
        write_once_json(p, doc, indent=2)
        write_once_sidecar(p)
        print("wrote", p)
    print(json.dumps({"units": len(em["units"]), "events": len(dec["events"])}))


if __name__ == "__main__":
    raise SystemExit(main())
