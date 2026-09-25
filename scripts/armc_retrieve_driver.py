"""Per-year retrieval, one worker per variable, retry on CDS queue rejections."""
import json, sys, time
from pathlib import Path
sys.path.insert(0, "scripts")
import armc_cds_retrieve as m
import cdsapi

VAR = sys.argv[1]
AM = "/Users/sanjayb/nepal-event-anomaly-evidence/p5-glof-2026-09-19/retrieval"
ROOT = Path("/Users/sanjayb/nepal-event-anomaly-evidence/p5-armc-pressure-levels-2026-09-22")
gate = json.loads((ROOT / "retrieval" / "armc_metadata_gate_v0.json").read_text())
assert gate["status"] == "METADATA_OK" and gate["retrieval_gate"] == "PAYLOAD_AUTHORIZED"

contract = m.load_contract(Path(f"{AM}/p5_amendment_v10_arm_c_spatial_correction.json"),
                           v8_path=Path(f"{AM}/p5_amendment_v8_arm_c_scope.json"),
                           v9_path=Path(f"{AM}/p5_amendment_v9_arm_c_grid_provenance.json"))
full = m.build_request_plan(contract)
base = [r for r in full if r["variable"] == VAR]
years = sorted({r["year"] for r in base})
basins = sorted({r["basin"] for r in base})
lvls = sorted({r["pressure_level_hpa"] for r in base})
plan = []
for b in basins:
    for lvl in lvls:
        for y in years:
            ref = next(r for r in base if r["basin"] == b and r["pressure_level_hpa"] == lvl and r["year"] == y)
            req = dict(ref["request"]); req["month"] = ["06","07","08"]
            req["day"] = [f"{d:02d}" for d in range(1,32)]
            plan.append({**ref, "chunk_id": f"{b}-{VAR}-{lvl}-{y}", "request": req})

root = ROOT / f"payload-{VAR}"
retrieval = root / "retrieval"
req_dir, pay_dir, ch_dir = retrieval/"requests", retrieval/"payloads", retrieval/"chunks"
ch_dir.mkdir(parents=True, exist_ok=True) if not req_dir.exists() else None
req_dir.mkdir(parents=True, exist_ok=False) if not req_dir.exists() else None
pay_dir.mkdir(parents=True, exist_ok=False) if not pay_dir.exists() else None
ch_dir.mkdir(parents=True, exist_ok=True)

from p5_safe_io import write_once_json, write_once_sidecar, sha256_bytes
plan_doc = {"schema":"P5_ARMC_REQUEST_PLAN_V0","created_utc":m._utc_now(),"contract":dict(contract),
            "request_count":len(plan),"requests":plan,
            "plan_sha256": sha256_bytes(m._canonical_json({"requests": plan})),
            "claim_scope":"research_only_no_operational_authorization"}
pp = retrieval/"armc_request_plan_v0.json"
write_once_json(pp, plan_doc, indent=2); write_once_sidecar(pp)

client = cdsapi.Client(quiet=True)
records, total_bytes = [], 0
started = m._utc_now()
cap = int(contract["max_download_bytes"])
for item in plan:
    cid = m._safe_chunk_id(item["chunk_id"]); req = item["request"]
    rd = {"schema":"P5_ARMC_REQUEST_V0","chunk_id":cid,"dataset":contract["dataset"],
          "request":dict(req),"request_sha256":sha256_bytes(m._canonical_json(req))}
    rp = req_dir/f"{cid}.json"; write_once_json(rp, rd, indent=2); write_once_sidecar(rp)
    target = pay_dir/f"{cid}.nc"
    if target.exists():
        sz = target.stat().st_size; total_bytes += sz
        records.append({"chunk_id":cid,"response_sha256":target.with_suffix('.nc.sha256').read_text().split()[0] if target.with_suffix('.nc.sha256').exists() else None,"response_bytes":sz,"status":"PAYLOAD_RECEIVED_UNPARSED"})
        continue
    last_err = None
    for attempt in range(8):
        try:
            client.retrieve(contract["dataset"], dict(req), str(target)); last_err = None; break
        except Exception as e:
            last_err = e
            msg = str(e)
            if "temporarily limited" in msg or "429" in msg or "results" in msg:
                wait = 60 + attempt*60
                print(f"[{VAR}] {cid} queue-rejected, retry {attempt+1} in {wait}s", flush=True)
                time.sleep(wait); continue
            raise
    if last_err is not None:
        raise RuntimeError(f"{cid}: exhausted retries: {last_err}")
    if not target.is_file() or target.stat().st_size <= 0:
        raise ValueError(f"{cid}: empty payload")
    sz = target.stat().st_size; total_bytes += sz
    if total_bytes > cap: raise ValueError(f"cap exceeded: {total_bytes}")
    sha = write_once_sidecar(target)
    rec = {"schema":"P5_ARMC_CHUNK_RECEIPT_V0","chunk_id":cid,
           "request_relpath":f"retrieval/requests/{cid}.json","payload_relpath":f"retrieval/payloads/{cid}.nc",
           "request_sha256":rd["request_sha256"],"response_sha256":sha,"response_bytes":sz,
           "status":"PAYLOAD_RECEIVED_UNPARSED"}
    cp = ch_dir/f"{cid}.json"; write_once_json(cp, rec, indent=2); write_once_sidecar(cp)
    records.append(rec)
    print(f"[{VAR}] {cid} {sz/1e6:.2f}MB ({len(records)}/{len(plan)})", flush=True)

man = {"schema":"P5_ARMC_RETRIEVAL_MANIFEST_V0","status":"RETRIEVAL_RECEIVED_PENDING_NETCDF_VALIDATION",
       "started_utc":started,"completed_utc":m._utc_now(),"request_count":len(plan),
       "completed_chunks":len(records),"actual_payload_bytes":total_bytes,"max_download_bytes":cap,
       "request_plan_sha256":plan_doc["plan_sha256"],"chunks":records,
       "claim_scope":"research_only_no_operational_authorization"}
mp = retrieval/"armc_retrieval_manifest_v0.json"
write_once_json(mp, man, indent=2); write_once_sidecar(mp)
print(f"[{VAR}] DONE chunks={len(records)} bytes={total_bytes}", flush=True)
