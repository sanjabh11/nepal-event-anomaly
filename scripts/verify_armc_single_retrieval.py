"""v16 single-level lane audit + full receipt<->payload binding check.

Audits the 300 v16 single-level chunks end to end (plan, request docs,
request_sha256 recompute, receipts, payload sidecars, dims/time coverage,
source/snapshot values) AND asserts receipt.response_sha256 == payload
sha256 across every chunk in all lanes — including the 600 pressure
chunks whose payload bytes were already audited (the binding itself was
not asserted during frame build on lane v1).
"""
import json, sys, hashlib
from pathlib import Path
sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import numpy as np
import xarray as xr
import armc_cds_retrieve as m
from p5_safe_io import sha256_bytes

ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence/p5-armc-pressure-levels-2026-09-22")
SNAPSHOT = "ZFKDHBCTBVHVXM3BQFV0"
SINGLE_VARS = {"sp", "t2m", "cape", "tcwv"}

def _sha(p): return hashlib.sha256(Path(p).read_bytes()).hexdigest()

def _sidecar_ok(p: Path) -> bool:
    s = Path(str(p) + ".sha256")
    return s.exists() and s.read_text().split()[0] == _sha(p)

def audit_lane(lane: Path, expect_chunks: int, single: bool, problems: list) -> dict:
    name = lane.name
    ret = lane / "retrieval"
    plan = ret / "armc_request_plan_v0.json"
    if not plan.exists() or not _sidecar_ok(plan):
        problems.append(f"{name}: plan missing or sidecar mismatch")
        return {}
    reqs = {r["chunk_id"]: r for r in json.loads(plan.read_text())["requests"]}
    if len(reqs) != expect_chunks:
        problems.append(f"{name}: plan has {len(reqs)} chunks, expected {expect_chunks}")
    ok = 0
    srcs = {}
    for cid, entry in reqs.items():
        rp = ret / "requests" / f"{cid}.json"
        pp = ret / "payloads" / f"{cid}.nc"
        cp = ret / "chunks" / f"{cid}.json"
        for p in (rp, pp, cp):
            if not p.exists():
                problems.append(f"{name}:{cid}: missing {p.name}")
                continue
        if not all(_sidecar_ok(p) for p in (rp, pp, cp)):
            problems.append(f"{name}:{cid}: sidecar digest mismatch")
            continue
        rd = json.loads(rp.read_text())
        rec = json.loads(cp.read_text())
        # request_sha256 covers canonical request body (with trailing \n per writer)
        want = sha256_bytes(m._canonical_json(rd["request"]))
        if rd["request_sha256"] != want or rec["request_sha256"] != want:
            problems.append(f"{name}:{cid}: request_sha256 recompute mismatch")
            continue
        # receipt <-> payload binding (G3)
        if rec["response_sha256"] != _sha(pp):
            problems.append(f"{name}:{cid}: receipt does not bind payload bytes")
            continue
        if rec["request_relpath"] != f"retrieval/requests/{cid}.json" or \
           rec["payload_relpath"] != f"retrieval/payloads/{cid}.nc":
            problems.append(f"{name}:{cid}: receipt relpath drift")
            continue
        srcs[rec.get("source", "absent")] = srcs.get(rec.get("source", "absent"), 0) + 1
        if rec.get("source") == "earthmover_icechunk":
            ex = rd.get("execution", {})
            if ex.get("snapshot_id") != SNAPSHOT or \
               f"@{SNAPSHOT}" not in str(rec.get("derived_from", "")):
                problems.append(f"{name}:{cid}: snapshot binding wrong")
                continue
        # payload shape/time coverage
        ds = xr.open_dataset(pp).load()
        v = [k for k in ds.data_vars]
        if len(v) != 1:
            problems.append(f"{name}:{cid}: {len(v)} data vars")
            continue
        dims = dict(ds.dims)
        if dims.get("valid_time") != 2208:
            problems.append(f"{name}:{cid}: valid_time != 2208")
            continue
        if single and "pressure_level" in dims:
            problems.append(f"{name}:{cid}: unexpected pressure_level dim")
            continue
        if not single and dims.get("pressure_level") != 1:
            problems.append(f"{name}:{cid}: pressure_level dim != 1")
            continue
        # exact coordinate contract
        if int(ds["number"].values) != 0:
            problems.append(f"{name}:{cid}: number != 0")
            continue
        ev = np.unique(ds["expver"].values.astype(str))
        if not (len(ev) == 1 and ev[0] == "0001"):
            problems.append(f"{name}:{cid}: expver not all '0001'")
            continue
        t = pd_ts = np.asarray(ds["valid_time"].values)
        if not np.all(np.diff(t) == np.timedelta64(1, "h")):
            problems.append(f"{name}:{cid}: non-contiguous hourly axis")
            continue
        arr = ds[v[0]].values
        if not np.isfinite(arr).all():
            problems.append(f"{name}:{cid}: non-finite values")
            continue
        ok += 1
    return {"lane": name, "chunks_ok": ok, "expected": expect_chunks,
            "source_counts": srcs}

def main() -> int:
    problems = []
    lanes = []
    # 300 single-level chunks: 4 lanes x 75
    for v in sorted(SINGLE_VARS):
        lanes.append(audit_lane(ROOT / f"payload-single_{v}", 75, True, problems))
    # binding re-check on the 600 pressure chunks (receipt<->payload)
    for v in ("geopotential", "specific_humidity", "temperature", "vertical_velocity"):
        lanes.append(audit_lane(ROOT / f"payload-{v}", 150, False, problems))
    report = {"schema": "P5_ARMC_V16_LANE_AUDIT_V0",
              "claim_scope": "research_only_no_operational_authorization",
              "status": "V16_LANE_AUDIT_OK" if not problems else "PROBLEMS_FOUND",
              "problems": problems, "lanes": lanes,
              "total_chunks_audited": sum(l["chunks_ok"] for l in lanes),
              "snapshot": SNAPSHOT}
    out = ROOT / "retrieval" / "armc_v16_lane_audit_v0.json"
    from p5_safe_io import write_once_json, write_once_sidecar
    write_once_json(out, report, indent=2)
    write_once_sidecar(out)
    print(json.dumps({"status": report["status"],
                      "chunks": report["total_chunks_audited"],
                      "problems": len(problems)}, indent=2))
    for p in problems[:15]:
        print(" -", p)
    return 0 if not problems else 1

if __name__ == "__main__":
    sys.exit(main())
