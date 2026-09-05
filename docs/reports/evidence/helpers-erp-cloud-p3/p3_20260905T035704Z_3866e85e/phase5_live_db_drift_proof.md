# P3 Phase 5 — Live Control DB Drift Proof

**Run ID:** `p3_20260905T071700Z_d1938642`
**Evidence Dir:** `p3_20260905T035704Z_3866e85e`
**Date (UTC):** `2026-09-05T08:53:00Z`
**Live DB:** `/opt/projects/active/odoo-sh-local-mock/data/control.db`
**Backup (preflight):** `/opt/projects/active/odoo-sh-local-mock/data/control.db.backup.20260905T035704Z_p3_preflight`
**Isolated DB:** `/tmp/p3-canary-p3_20260905T071700Z_d1938642/control.db` (removed after closeout, sanitized evidence preserved)

## Hashes

| File | SHA-256 | Timestamp |
|------|---------|-----------|
| `control.db.backup.20260905T035704Z_p3_preflight` | `8d16985c88f956a22c41aff8e7a6d719d61d06ceeff7a4a5bf9195b4343a58cd` | 2026-09-05T03:57:04Z (preflight) |
| `live_hash.txt` (at canary start) | `9d049d433dc0562c86524dc1a78255978d086dfe443446300c75bc400bc951f9` | 2026-09-05T07:17:00Z |
| `isolated_hash_before.txt` | `9d049d433dc0562c86524dc1a78255978d086dfe443446300c75bc400bc951f9` | 2026-09-05T07:17:00Z (cp live) |
| `control.db` (live, after canary, before closeout) | `a2cdbd65de2b5d08b06767c838de1bd94cfe15b1453b0aaf752dc4b0feaeda05` | 2026-09-05T08:39:33Z |
| `control.db` (live, after closeout) | `0a29ac2ac6235f524cd4b45341ee037aa87934acd07daafb91a86b5f1b726e74` | 2026-09-05T08:53:00Z |
| `control.db` (isolated, before removal) | `ceff22105801a2d824a40313c5aef0b97dbd4b7a0985d9fc79e916aef9d5a514` | 2026-09-05T07:32:00Z |

**Integrity:** `PRAGMA integrity_check` = `ok` for live and isolated.

## Table Counts (Backup vs Live)

| Table | Backup (03:57) | Live (08:53) | Delta |
|-------|----------------|--------------|-------|
| `cloud_provisioning_requests` | 2 | 3 | +1 (request 3) |
| `cloud_instances` | 2 | 3 | +1 (instance 3) |
| `tenants` | 26 | 26 | 0 (but updated_at changed) |
| `audit_events` | 530 | 554 | +24 |
| `tenant_backups` | 131 | 136 | +5 |
| `backup_policies` | 16 | 16 | 0 |

## Detailed Changes

### cloud_provisioning_requests

- **Backup:** ids 1,2 only (both `queued`, `demo`, `2026-09-02`/`2026-09-03`).
- **Live:** ids 1,2 unchanged, plus id 3 `374242ca0f3723c020b32c33d5ec3071` `rolled_back` `ready` `local_docker` `attempt 1` `2026-09-05 05:47:04` → `2026-09-05 06:02:22`.
- **Requests 1–3 unchanged?** Requests 1,2 unchanged (same uuid, status, adapter, attempt). Request 3 was created at 05:47 (before canary window 07:17) as part of P3 Phase 5 recovery (P2 provisioning), not canary. It is `rolled_back` and not requeued. No change to requests 1,2 during canary window. Request 3 not modified during canary window (already rolled_back before 07:17).

### cloud_instances

- **Backup:** ids 1,2 (`demo-cloud-7`, `petsy`, `queued`).
- **Live:** plus id 3 `p3-canary-49df00` `rolled_back` (matches request 3).

### tenants

- **Count:** 26 both.
- **Updated_at:** Active tenants (`pt_trial_1_a89ea9`, `sis_21_b0da13`, `clone_20_aea0ed`, `vet_hospital_19_7f6dcd`, etc.) changed from `2026-09-05 03:22:47` / `03:45:07` to `2026-09-05 08:23:10`.
- **Columns changed:** `updated_at`, `last_metered_at`, `filestore_bytes`, `database_bytes`, `backup_storage_bytes` (metering).
- **Actor:** `backup-worker-1` (metering) and `backup-scheduler`.
- **Not canary:** No tenant with `p2_p3` or `p3-canary` in live.

### audit_events

- **Backup last id:** 530 `backup.succeeded` `2026-09-05 03:45:07`.
- **Live last id:** 554 `backup.scheduled_queued` `2026-09-05 08:45:29`.
- **New rows 531–554:** All `backup.*` events (`backup.queued`, `backup.scheduled_queued`, `backup.retention_deleted`, `backup.succeeded`) for `vet_hospital_19_7f6dcd` every hour (04:45, 05:45, 06:45, 07:45, 08:45) plus `cloud.p2.*` and `cloud_provisioning_approved` for request 3 at 05:47–06:02 (before canary).
- **Timestamps:** Hourly metering, not canary window.
- **Actor:** `backup-scheduler` or empty (system), not canary worker.
- **Canary window 07:17–07:32:** Only `backup.succeeded` at 07:45 (after window) and `backup.queued` at 07:45 — no canary-related writes.

### tenant_backups

- **Backup:** 131 rows, last `2026-09-05 03:45:06` for tenant 18.
- **Live:** 136 rows, new 5 rows for tenant 18 at 04:45, 05:45, 06:45, 07:45, 08:45 (hourly backups).

## Attribution

- **Tenant metering:** Yes, normal. `backup-worker-1` runs hourly, updates `tenants.updated_at`, `last_metered_at`, creates `tenant_backups`, emits `audit_events`. Verified via `backup_worker_heartbeat.json` (`idle`, `2026-09-05T08:54:03`) and `audit_events` actor `backup-scheduler`.
- **Request 3:** Created before canary window (05:47), rolled back before canary (06:02), not metering but legitimate P3 recovery. Not canary-related.
- **Canary writes to live:** **None.** Verified:
  - No `cloud_provisioning_requests` with uuid `0be12838a81319cb86c0c15b0c14908f` (request 4) or `4e7b2a081d4459058b138ddf01b2492d` (request 5) in live.
  - No `tenants` with `tenant_code LIKE '%p2_p3%'` in live.
  - No `cloud_instances` with `requested_subdomain LIKE '%p3-canary%'` except `p3-canary-49df00` (request 3, before canary).
  - No `audit_events` with `actor LIKE '%p3-canary%'` or `run_id p3_20260905T071700Z_d1938642` in live (only `p2:p3_20260905T054700Z_9340f78e` for request 3).
  - Isolated DB hash `ceff221...` differs from live, but live hash change is metering, not canary.

## Requests 1–3

- **Unchanged during canary window:** Yes. Requests 1,2 `queued` unchanged. Request 3 `rolled_back` unchanged (already rolled_back before 07:17, not requeued).

## Isolated Canary Wrote to Live?

- **No.** Isolated DB was `cp live` at 07:17, used `DATABASE_URL=sqlite:////data/control.db` pointing to `/tmp/p3-canary-.../control.db`, not live. Docker run used `-v CANARY_DIR/control.db:/data/control.db` and `-v CANARY_DIR:/data/canary`, no mount of live `/data`. Verified via `phase5_retry_preparation.md` method and `worker_progress.log` (`DATABASE_URL` isolated).

## Conclusion

- Live DB change is **conclusively attributed** to normal tenant metering (hourly backups) plus pre-canary request 3 creation. No unexplained canary-related live writes. No restore needed. `P3_PHASE5_SECURITY_CLOSEOUT` can proceed.

## Evidence

- `control.db.backup.20260905T035704Z_p3_preflight` (hash `8d16985c...`)
- `live_hash.txt` (`9d049d...`)
- `isolated_hash_before.txt` (`9d049d...`)
- `phase5_retry_drift_comparison.md` (existing)
- `phase5_retry_after_inventory.txt`
- `audit_events` dump (ids 527–554)
- `tenants` updated_at comparison
