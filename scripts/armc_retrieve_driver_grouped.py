"""Grouped-level retrieval worker (amendment v14).

One CDS request per basin x variable x year covers both pressure levels
(500, 700). The raw grouped response is preserved immutably under
retrieval/grouped_responses/; canonical per-level payloads are decomposed
by an exact .sel(pressure_level=lvl) slice (no resampling).

Provenance model:
- requests/{cid}.json keeps the per-level LOGICAL request (uniform across
  all 600 chunks) plus an `execution` object recording the actual grouped
  request digest and v14 amendment.
- chunks/{cid}.json receipts link the canonical payload, its level's
  logical request, AND the grouped response digest (derived_from).

Resume-safe: skips basin-years whose per-level payloads already exist.
"""
import json, sys, tempfile, os, time
from pathlib import Path
sys.path.insert(0, "scripts")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import armc_cds_retrieve as m
import cdsapi
import xarray as xr

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

root = ROOT / f"payload-{VAR}"
retrieval = root / "retrieval"
req_dir, pay_dir, ch_dir = retrieval/"requests", retrieval/"payloads", retrieval/"chunks"
grp_dir = retrieval/"grouped_responses"
for d in (req_dir, pay_dir, ch_dir, grp_dir):
    d.mkdir(parents=True, exist_ok=True)

from p5_safe_io import write_once_json, write_once_sidecar, sha256_bytes
plan_path = retrieval/"armc_request_plan_v0.json"
_plan_entries = json.loads(plan_path.read_text())["requests"]

def by_key(b, y, lvl):
    return next(r for r in _plan_entries if r["basin"] == b and r["year"] == y
                and int(r["pressure_level_hpa"]) == lvl)

client = cdsapi.Client(quiet=True)
records, total_bytes = [], 0
started = m._utc_now()
cap = int(contract["max_download_bytes"])

for b in basins:
    for y in years:
        missing = [lvl for lvl in lvls
                   if not (pay_dir/f"{b}-{VAR}-{lvl}-{y}.nc").exists()]
        if not missing:
            continue
        group_id = f"{b}-{VAR}-grouped-{y}"
        ref = by_key(b, y, min(lvls))
        grouped_req = dict(ref["request"])
        grouped_req["month"] = ["06","07","08"]
        grouped_req["day"] = [f"{d:02d}" for d in range(1,32)]
        grouped_req["pressure_level"] = [str(l) for l in lvls]
        grouped_req_sha = sha256_bytes(m._canonical_json(grouped_req))
        gtarget = grp_dir/f"{group_id}.nc"
        greq_doc = {"schema":"P5_ARMC_GROUPED_REQUEST_V0","grouped_request_id":group_id,
                    "dataset":contract["dataset"],"request":grouped_req,
                    "request_sha256":grouped_req_sha,
                    "grouped_chunk_ids":[f"{b}-{VAR}-{l}-{y}" for l in lvls],
                    "amendment":"p5_amendment_v14_armc_grouped_requests"}
        grp = grp_dir/f"{group_id}.request.json"
        if not grp.exists():
            write_once_json(grp, greq_doc, indent=2); write_once_sidecar(grp)
        if not gtarget.exists():
            last_err = None
            for attempt in range(60):
                try:
                    client.retrieve(contract["dataset"], dict(grouped_req), str(gtarget))
                    last_err = None; break
                except Exception as e:
                    last_err = e
                    msg = str(e)
                    if "temporarily limited" in msg or "429" in msg or "results" in msg:
                        wait = min(300, 120 + attempt*30)
                        print(f"[{VAR}] {group_id} queue-rejected ({msg[:120]}), retry {attempt+1} in {wait}s", flush=True)
                        time.sleep(wait); continue
                    raise
            if last_err is not None:
                raise RuntimeError(f"{group_id}: exhausted retries: {last_err}")
            gsha = write_once_sidecar(gtarget)
        else:
            gsha = gtarget.with_suffix('.nc.sha256').read_text().split()[0]
        total_bytes += gtarget.stat().st_size
        if total_bytes > cap: raise ValueError(f"cap exceeded: {total_bytes}")

        with xr.open_dataset(gtarget) as gsrc:
            gds = gsrc.load()
        if set(gds.coords["pressure_level"].astype(int).values.tolist()) != set(lvls):
            raise ValueError(f"{group_id}: grouped response missing levels")
        for lvl in missing:
            cid = m._safe_chunk_id(f"{b}-{VAR}-{lvl}-{y}")
            # Logical request must equal the plan entry verbatim (auditor compares).
            logical = by_key(b, y, lvl)["request"]
            rd = {"schema":"P5_ARMC_REQUEST_V0","chunk_id":cid,"dataset":contract["dataset"],
                  "request":logical,
                  "request_sha256":sha256_bytes(m._canonical_json(logical)),
                  "execution":{"mode":"grouped_level_pair",
                               "grouped_request_id":group_id,
                               "grouped_request_sha256":grouped_req_sha,
                               "grouped_request_relpath":f"retrieval/grouped_responses/{group_id}.request.json",
                               "amendment":"p5_amendment_v14_armc_grouped_requests"}}
            rp = req_dir/f"{cid}.json"
            if not rp.exists():
                write_once_json(rp, rd, indent=2); write_once_sidecar(rp)
            else:
                # Existing request docs are hash-bound; never mutate them.
                # The grouped-execution linkage lives in the chunk receipt and
                # the grouped request record. Verify logical-request agreement.
                existing = json.loads(rp.read_text())
                if existing.get("request_sha256") != rd["request_sha256"]:
                    raise ValueError(f"{cid}: existing request doc digest mismatch")
            target = pay_dir/f"{cid}.nc"
            if not target.exists():
                slice_ds = gds.sel(pressure_level=[float(lvl)])
                fd, tmp = tempfile.mkstemp(prefix=".grp-", suffix=".nc", dir=pay_dir); os.close(fd)
                try:
                    slice_ds.to_netcdf(tmp)
                    os.link(tmp, target)
                finally:
                    os.unlink(tmp)
            sz = target.stat().st_size
            sha = write_once_sidecar(target)
            rec = {"schema":"P5_ARMC_CHUNK_RECEIPT_V0","chunk_id":cid,
                   "request_relpath":f"retrieval/requests/{cid}.json",
                   "payload_relpath":f"retrieval/payloads/{cid}.nc",
                   "request_sha256":rd["request_sha256"],"response_sha256":sha,
                   "response_bytes":sz,"status":"PAYLOAD_DERIVED_FROM_GROUPED_RESPONSE",
                   "grouped_response_relpath":f"retrieval/grouped_responses/{group_id}.nc",
                   "grouped_response_sha256":gsha,
                   "derived_from":f"retrieval/grouped_responses/{group_id}.nc",
                   "derivation":"xarray .sel(pressure_level=[lvl]) slice of grouped CDS response; no resampling"}
            cp = ch_dir/f"{cid}.json"
            if not cp.exists():
                write_once_json(cp, rec, indent=2); write_once_sidecar(cp)
            records.append(rec)
        print(f"[{VAR}] {group_id} -> {len(missing)} chunks ({len(records)} new)", flush=True)

man = {"schema":"P5_ARMC_RETRIEVAL_MANIFEST_V0",
       "status":"RETRIEVAL_RECEIVED_PENDING_NETCDF_VALIDATION",
       "execution":"grouped_level_pair_v14",
       "started_utc":started,"completed_utc":m._utc_now(),
       "completed_chunks":len(records),"actual_payload_bytes":total_bytes,
       "max_download_bytes":cap,"chunks":records,
       "claim_scope":"research_only_no_operational_authorization"}
mp = retrieval/"armc_grouped_manifest_v14.json"
write_once_json(mp, man, indent=2); write_once_sidecar(mp)
print(f"[{VAR}] DONE new_chunks={len(records)} bytes={total_bytes}", flush=True)
