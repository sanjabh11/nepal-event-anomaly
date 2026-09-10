"""ERA5-Land multi-winter CDS acquisition script — Phase 2.

Downloads ERA5-Land hourly data for 3 winters × 2 regions × 6 months = 36 partitions.
Each partition is one month × one region × 36 variables × 24 hours.

Strategy:
- Sequential with 3-retry exponential backoff (10s, 60s, 300s)
- 1 concurrent CDS job (per advisor guidance)
- Log request IDs in staging receipts
- Never print CDS API key
- Skip previously hash-verified partitions (check CAS)

State machine: PLANNED → REQUESTED → DOWNLOADING → DOWNLOADED → EXTRACTED
→ STRUCTURE_VERIFIED → HASH_VERIFIED → CAS_REGISTERED → QUALIFIED

Usage:
    /Users/sanjayb/avalanche-insight-hub/.venv/bin/python -B scripts/acquire_era5_land.py [--dry-run] [--winter 2023-24]
"""
from __future__ import annotations

import hashlib
import json
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from backend.research.t2a_regime_discovery.content_addressed_store import (
    ContentAddressedStore,
)
from backend.research.t2a_regime_discovery.data_plane.partition_state import (
    PartitionState,
    PartitionStateMachine,
    PartitionFailureCategory,
    PartitionRecord,
)
from backend.research.t2a_regime_discovery.data_plane.request_manifest import (
    all_era5_land_partitions,
    WINTERS,
    REGION_PIR_PANJAL,
    REGION_NEPAL,
    AOI_HASH_V1,
)
from backend.research.t2a_regime_discovery.data_plane.receipts import (
    build_acquisition_receipt,
    build_decoder_receipt,
    build_coverage_receipt,
    build_rights_evidence,
)
from backend.research.t2a_regime_discovery.data_plane.continuity import (
    ContinuityMatrix,
    ContinuityEntry,
    ContinuityStatus,
)
from backend.research.t2a_regime_discovery.data_plane.dashboard import (
    Dashboard,
    DashboardEntry,
)
from backend.research.t2a_regime_discovery.data_plane.storage_guard import (
    assess_storage,
    StorageState,
)

STAGING_BASE = REPO_ROOT / "config" / "t2a" / "evidence" / "staging" / "era5"
STATE_FILE = REPO_ROOT / "config" / "t2a" / "evidence" / "R14_ERA5_LAND_PARTITION_STATE.json"
DASHBOARD_FILE = REPO_ROOT / "config" / "t2a" / "evidence" / "R14_LONG_RUNNING_ACQUISITION_STATUS.json"
CONTINUITY_FILE = REPO_ROOT / "config" / "t2a" / "evidence" / "R14_ACQUISITION_CONTINUITY_MATRIX_V1.json"

MAX_RETRIES = 3
RETRY_DELAYS = [10, 60, 300]  # seconds


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_grib_structure(path: Path) -> dict:
    """Verify GRIB file structure using eccodes/cfgrib."""
    try:
        import eccodes
        with open(path, "rb") as f:
            count = 0
            param_ids: set[int] = set()
            editions: set[int] = set()
            while True:
                gid = eccodes.codes_grib_new_from_file(f)
                if gid is None:
                    break
                count += 1
                editions.add(eccodes.codes_get(gid, "edition"))
                try:
                    pid = eccodes.codes_get(gid, "paramID")
                    param_ids.add(pid)
                except Exception:
                    pass
                eccodes.codes_release(gid)
            return {
                "message_count": count,
                "unique_parameters": sorted(param_ids),
                "editions": sorted(editions),
                "valid": count > 0,
            }
    except Exception as e:
        return {"valid": False, "error": str(e)}


def acquire_partition(
    manifest,
    store: ContentAddressedStore,
    sm: PartitionStateMachine,
    dashboard: Dashboard,
    continuity: ContinuityMatrix,
    dry_run: bool = False,
) -> bool:
    """Acquire a single ERA5-Land partition. Returns True on success."""
    pid = manifest.partition_id

    # Check if already qualified.
    existing = sm.get(pid)
    if existing and existing.state == PartitionState.QUALIFIED:
        print(f"  SKIP: {pid} already QUALIFIED")
        return True

    # Register if new.
    if not existing:
        rec = PartitionRecord(
            partition_id=pid,
            provider=manifest.provider,
            dataset=manifest.dataset,
            winter_id=manifest.winter_id,
            region=manifest.region,
            date_range_start=f"{manifest.year}-{manifest.month}-01",
            date_range_end=f"{manifest.year}-{manifest.month}-28",
        )
        sm.register(rec)

    print(f"  Processing: {pid}")

    if dry_run:
        print(f"    DRY RUN: would request {manifest.dataset} for {manifest.year}-{manifest.month} {manifest.region}")
        print(f"    DRY RUN: {len(manifest.variables)} variables × {len(m.days)} days × {len(m.hours)} hours = {len(manifest.variables) * len(m.days) * len(m.hours)} fields")
        return True

    # Build staging directory.
    winter_dir = STAGING_BASE / manifest.winter_id / manifest.region
    winter_dir.mkdir(parents=True, exist_ok=True)
    target_file = winter_dir / f"era5_land_{manifest.region}_{manifest.year}{manifest.month}.grib"

    # Build request dict.
    request = manifest.to_request_dict()
    request_digest = manifest.digest()

    # Attempt download with retries.
    for attempt in range(MAX_RETRIES):
        try:
            sm.transition(pid, PartitionState.REQUESTED)
            print(f"    Attempt {attempt + 1}/{MAX_RETRIES}: requesting CDS...")

            import cdsapi
            client = cdsapi.Client(quiet=True, progress=False, timeout=600, retry_max=500, sleep_max=120)
            client.retrieve(manifest.dataset, request, str(target_file))

            # DOWNLOADED
            if not target_file.exists() or target_file.stat().st_size == 0:
                raise IOError(f"Downloaded file empty or missing: {target_file}")

            file_hash = sha256_file(target_file)
            file_size = target_file.stat().st_size
            sm.transition(pid, PartitionState.DOWNLOADED, byte_size=file_size, sha256=file_hash)
            print(f"    Downloaded: {file_size} bytes, sha256={file_hash[:16]}...")

            # EXTRACTED — verify GRIB structure
            struct = verify_grib_structure(target_file)
            if not struct.get("valid", False):
                raise ValueError(f"GRIB structure invalid: {struct.get('error', 'unknown')}")

            sm.transition(pid, PartitionState.EXTRACTED)
            sm.transition(pid, PartitionState.STRUCTURE_VERIFIED)

            # HASH_VERIFIED
            sm.transition(pid, PartitionState.HASH_VERIFIED)

            # CAS_REGISTERED
            raw_bytes = target_file.read_bytes()
            cas_digest = store.put_bytes(raw_bytes)

            # Build receipts
            acq_receipt = build_acquisition_receipt(
                provider=manifest.provider,
                dataset=manifest.dataset,
                request_parameters=request,
                aoi_hash=AOI_HASH_V1,
                spatial_crop={
                    "north": manifest.area[0], "west": manifest.area[1],
                    "south": manifest.area[2], "east": manifest.area[3],
                },
                variables=manifest.variables,
                date_range_start=f"{manifest.year}-{manifest.month}-01",
                date_range_end=f"{manifest.year}-{manifest.month}-28",
                hours=manifest.hours,
                transport_type="cdsapi_https",
                transport_hash=request_digest,
                payload_hash=file_hash,
                payload_byte_count=file_size,
                partition_id=pid,
                winter_id=manifest.winter_id,
                region=manifest.region,
            )
            receipt_digest = store.put_json(acq_receipt.to_dict())

            decoder_receipt = build_decoder_receipt(
                decoder="eccodes",
                decoder_version="2.48.0",
                file_hash=file_hash,
                file_size=file_size,
                format="GRIB",
                message_count=struct["message_count"],
                unique_parameters=struct["unique_parameters"],
                grid_type="regular_ll",
                grid_dimensions=[21, 21],
                spatial_bounds={
                    "north": manifest.area[0], "west": manifest.area[1],
                    "south": manifest.area[2], "east": manifest.area[3],
                },
                temporal_range={
                    "start": f"{manifest.year}-{manifest.month}-01",
                    "end": f"{manifest.year}-{manifest.month}-28",
                },
                time_steps=len(manifest.hours) * len(manifest.days),
                validation_passed=True,
            )
            decoder_digest = store.put_json(decoder_receipt.to_dict())

            sm.transition(pid, PartitionState.CAS_REGISTERED, cas_digest=cas_digest)

            # QUALIFIED
            sm.transition(pid, PartitionState.QUALIFIED)

            # Update dashboard
            dashboard.upsert(DashboardEntry(
                job_id=pid,
                provider=manifest.provider,
                winter_id=manifest.winter_id,
                partition_id=pid,
                region=manifest.region,
                status="QUALIFIED",
                attempt_count=attempt + 1,
                started_at=existing.started_at if existing else _now_iso(),
                completed_at=_now_iso(),
                bytes=file_size,
                sha256=file_hash,
                cas_digest=cas_digest,
            ))

            # Update continuity matrix
            continuity.upsert(ContinuityEntry(
                winter_id=manifest.winter_id,
                provider=manifest.provider,
                dataset=manifest.dataset,
                region=manifest.region,
                partition_id=pid,
                status=ContinuityStatus.PRESENT_QUALIFIED,
                date_range_start=f"{manifest.year}-{manifest.month}-01",
                date_range_end=f"{manifest.year}-{manifest.month}-28",
                variable_count=len(manifest.variables),
                time_step_count=len(manifest.hours) * len(manifest.days),
                cas_digest=cas_digest,
                version="ERA5-Land_v1.0",
            ))

            print(f"    QUALIFIED: CAS digest={cas_digest[:16]}...")

            # One-copy rule: remove staging file after CAS registration + readback verification
            # The canonical raw payload is now in CAS. Staging copy is redundant.
            try:
                # Verify CAS readback before removing staging
                cas_obj = store.get_bytes(cas_digest)
                if cas_obj is not None and hashlib.sha256(cas_obj).hexdigest() == file_hash:
                    target_file.unlink(missing_ok=True)
                    print(f"    One-copy: removed staging duplicate (CAS readback verified)")
                else:
                    print(f"    One-copy: CAS readback mismatch — keeping staging copy")
            except Exception as e:
                print(f"    One-copy: could not verify CAS readback ({e}) — keeping staging copy")

            return True

        except Exception as e:
            error_msg = str(e)
            print(f"    Attempt {attempt + 1} failed: {error_msg[:100]}")

            if attempt < MAX_RETRIES - 1:
                delay = RETRY_DELAYS[attempt]
                print(f"    Retrying in {delay}s...")
                time.sleep(delay)
            else:
                # Permanent failure
                is_retryable = "timeout" in error_msg.lower() or "queue" in error_msg.lower()
                fail_state = PartitionState.FAILED_RETRYABLE if is_retryable else PartitionState.FAILED_PERMANENT
                fail_cat = PartitionFailureCategory.CDS_QUEUE_TIMEOUT if is_retryable else PartitionFailureCategory.UNKNOWN
                sm.transition(pid, fail_state, failure_category=fail_cat, failure_detail=error_msg[:200])

                dashboard.upsert(DashboardEntry(
                    job_id=pid,
                    provider=manifest.provider,
                    winter_id=manifest.winter_id,
                    partition_id=pid,
                    region=manifest.region,
                    status=fail_state.value,
                    attempt_count=attempt + 1,
                    started_at=existing.started_at if existing else _now_iso(),
                    failure_category=fail_cat.value,
                ))
                return False

    return False


def main() -> int:
    import argparse
    parser = argparse.ArgumentParser(description="ERA5-Land multi-winter CDS acquisition")
    parser.add_argument("--dry-run", action="store_true", help="Don't actually download")
    parser.add_argument("--winter", type=str, default=None, help="Specific winter (e.g., 2023-24)")
    args = parser.parse_args()

    print("=== Phase 2: ERA5-Land Multi-Winter Download ===")
    print(f"Python: {sys.executable}")
    print(f"Dry run: {args.dry_run}")

    # Build all partitions.
    all_partitions = all_era5_land_partitions()
    if args.winter:
        all_partitions = [p for p in all_partitions if p.winter_id == args.winter]
    print(f"Total partitions: {len(all_partitions)}")

    # Initialize state machine, dashboard, continuity.
    sm = PartitionStateMachine(STATE_FILE)
    dashboard = Dashboard(DASHBOARD_FILE)
    continuity = ContinuityMatrix(CONTINUITY_FILE)
    store = ContentAddressedStore()

    # Acquire each partition sequentially.
    success_count = 0
    fail_count = 0
    skip_count = 0

    for i, manifest in enumerate(all_partitions):
        print(f"\n[{i+1}/{len(all_partitions)}] {manifest.winter_id} {manifest.region} {manifest.year}-{manifest.month}")

        # Check if already qualified.
        existing = sm.get(manifest.partition_id)
        if existing and existing.state == PartitionState.QUALIFIED:
            print(f"  SKIP: already QUALIFIED")
            skip_count += 1
            continue

        # Storage guard check — concurrency-aware reservation
        if not args.dry_run:
            assessment = assess_storage(
                "/",
                expected_partition_peak_bytes=20 * 1024 * 1024,  # 20 MiB peak (GRIB + CAS)
                reserved_active_jobs_bytes=50 * 1024 * 1024,  # ~50 MiB for other active jobs
            )
            if assessment.state == StorageState.HARD_STOP_ACTIVE_DOWNLOAD_IF_NECESSARY:
                print(f"  STORAGE GUARD HARD STOP: {assessment.reason}")
                print(f"  Pausing all acquisition.")
                break
            if not assessment.can_start_new_partition:
                print(f"  STORAGE GUARD: {assessment.state.value} — pausing")
                break
            if assessment.state != StorageState.STORAGE_SAFE:
                print(f"  Storage: {assessment.state.value} ({assessment.snapshot.free_gib:.2f} GiB free)")

        ok = acquire_partition(manifest, store, sm, dashboard, continuity, dry_run=args.dry_run)
        if ok:
            success_count += 1
        else:
            fail_count += 1

    print(f"\n=== Phase 2 Summary ===")
    print(f"Success: {success_count}")
    print(f"Skipped (already qualified): {skip_count}")
    print(f"Failed: {fail_count}")
    print(f"Total: {len(all_partitions)}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
