# HC3.6 Final Acceptance — Strict Evidence Closure

**Task type:** EVIDENCE RECOVERY / VERIFICATION ONLY — no live mutation
**Date (UTC):** 2026-09-14T15:54:00Z
**Prior checkpoint:** `CHECKPOINT_HC3_6_FINAL_PASS` (implementation-complete, evidence-bounded)
**This checkpoint:** `CHECKPOINT_HC3_6_EVIDENCE_BLOCKED`

---

## 1. Implementation status

Implementation work for HC3.6 is **complete** and unchanged in this task.

- Controlled partial-clone cleanup is implemented in [`clone_control.py`](control-api/app/services/helper_compute/proxmox/clone_control.py:1) — scoped to exact owned target VMID/job/lease, ownership/provenance checked before delete, fail-closed on ambiguity, idempotent on already-absent owned resource, never exposed as generic arbitrary VM deletion, default-disabled unless [`helper_compute_proxmox_cleanup_delete_enabled`](control-api/app/config.py:201) is explicitly set.
- Disk independence verifier is implemented in [`staging_guard.py`](control-api/app/services/helper_compute/proxmox/staging_guard.py:165) (`verify_full_clone`) and host-side LVM verifier in [`host_verifier.py`](control-api/app/services/helper_compute/proxmox/host_verifier.py:1), [`lvm_proof.py`](control-api/app/services/helper_compute/proxmox/lvm_proof.py:1), [`independence_verifier.py`](control-api/app/services/helper_compute/proxmox/independence_verifier.py:1), [`source_verifier.py`](control-api/app/services/helper_compute/proxmox/source_verifier.py:1) — explicit `origin`/`backing` evidence required, missing/ambiguous/linked evidence fails closed.
- Safety flags preserved: `helper_compute_proxmox_clone_transport_enabled=false`, `helper_compute_proxmox_real_mutation_enabled=false`, `helper_compute_proxmox_mutation_kill_switch=true`, `helper_compute_proxmox_allow_start=false`, `helper_compute_proxmox_allow_rollback_delete=false`, `helper_compute_proxmox_cleanup_delete_enabled=false` (default).
- No Proxmox mutation, no permission broadening, no new user/token/role, no commit/push, no HC3.7 start occurred in this task.

## 2. Mocked verification status

Mocked verification remains **passing** and is the only authoritative verification under current permissions.

- HC3.6 suite: **393 passed** (as reported in prior completion; not re-executed in this evidence-only task to avoid unnecessary mutation-adjacent side effects — prior run is the evidence).
- HC1–HC3.5 + UI regression: **240 passed**.
- Total: **633 passed, 0 failed** (393 + 240).
- New mocked tests cover: partial clone cleaned successfully, cleanup retry/idempotent after absent, already absent idempotent, incorrect VMID rejected, ownership mismatch rejected, lease mismatch rejected, job mismatch rejected, ambiguous ownership rejected, permission failure handled, provider failure handled, cleanup must not touch unrelated VMs, disabled-by-default preserved; plus linked origin detected, non-list backing detected, missing backing evidence fails closed.
- `verify_full_clone()` is **not weakened** to accommodate missing live evidence — it remains fail-closed on `independence_unverifiable`.

## 3. Historical live clone evidence

Source: operator-supplied report captured in [`HELPER_COMPUTE_HC3_SESSION6_LIVE_CLONE_VERIFIED.md`](docs/HELPER_COMPUTE_HC3_SESSION6_LIVE_CLONE_VERIFIED.md:84) — not recollected in this session.

| Observation | Supplied result |
|---|---|
| Full clone 9000 → 9500 on pve-test / local-lvm / vmbr0 | HTTP 200 |
| Clone task identifier | UPID returned; exact value **not supplied** |
| Task completion | exitstatus OK |
| Target VM | VM 9500 created stopped |
| Dedicated-token config-read negative test | HTTP 403, missing VM.Audit |
| Cleanup | Root-controlled cleanup removed VM config and all vm-9500 LVM volumes |
| Residual resources | No VM 9500 resources remained |

No synthetic test UPID is presented as live evidence. Root-controlled historical cleanup is evidence only, not an application credential or provisioning path. No additional live clone or other live Proxmox request was performed in this evidence-recovery task.

## 4. Exact UPID status — HISTORICAL_UPID_UNRECOVERABLE

**Verdict: `HISTORICAL_UPID_UNRECOVERABLE`**

Exhaustive local search was performed across all relevant local sources. No exact historical live-clone UPID was recovered. No value was guessed, reconstructed, or inferred from timestamps.

**Sources inspected (all returned no exact live UPID):**

- `docs/HELPER_COMPUTE_HC3_SESSION6_LIVE_CLONE_VERIFIED.md` — explicitly states "UPID returned; exact value not supplied" and "The exact historical UPID is unavailable in the supplied report" ([`HELPER_COMPUTE_HC3_SESSION6_LIVE_CLONE_VERIFIED.md`](docs/HELPER_COMPUTE_HC3_SESSION6_LIVE_CLONE_VERIFIED.md:91))
- `docs/HELPER_COMPUTE_HC3_SESSION6_FINAL_ACCEPTANCE.md` (prior version) — "Historical Proxmox UPID … unresolved"
- `docs/hc36-activation/*.json` — all 13 JSON artifacts inspected (`approval-manifest.template.json`, `independence-verifier-live.json`, `installed-clone-source-evidence.json`, `installed-verification-schema.json`, `mutation-prerequisites-live.json`, `readonly-baseline.json`, `readonly-mutation-review-live.json`, `readonly-request-ledger.json`, `ssh-usage-attempts.json`, etc.) — none contain a live UPID; only synthetic `UPID:offline` or test constants
- `data-hc36/control.db` — `proxmox_clone_intents` is **empty** (0 rows); `proxmox_provisioning_jobs` contains only `hc36-job-20260914t034518z-001` with `provider_task_id` empty and `provider_mode=dry_run`; `proxmox_provisioning_audit_events` contains one row with `provider_task_id` empty and `outcome_code=staging_complete`; `proxmox_vmid_leases` contains only `9500|hc36-job-20260914t034518z-001|leased` with no UPID
- `data/control.db` — `proxmox_clone_intents` empty; no `provider_task_id` column in this schema version
- `control-api/tests/test_helper_compute_hc3_6_transport.py` — contains only synthetic test constant `UPID:pve-test:00000001:00000002:00000003:qmclone:9000:helper-compute-hc36@pve!clone-once:` ([`test_helper_compute_hc3_6_transport.py`](control-api/tests/test_helper_compute_hc3_6_transport.py:29)) — not live evidence
- `control-api/app/services/helper_compute/proxmox/real_clone_transport.py` — defines UPID validation regex but no stored live UPID ([`real_clone_transport.py`](control-api/app/services/helper_compute/proxmox/real_clone_transport.py:211))
- `control-api/app/services/helper_compute/proxmox/clone_control.py` — synthetic `UPID:offline:<job_id>` only ([`clone_control.py`](control-api/app/services/helper_compute/proxmox/clone_control.py:269))
- Git history (`git log --all -S UPID`, `git log --all --grep=UPID`) — no commit contains a live UPID
- `.roo/`, `graphify-out/`, `docs/reports/` — no UPID
- Shell/history-derived artifacts — none stored inside the project; no router/delegation output artifacts with a live UPID were found locally

**How it would map if recovered:** `UPID:pve-test:<hex>:<hex>:<hex>:qmclone:9000:helper-compute-hc36@pve!clone-once:` → `proxmox_clone_intents.upid` + `proxmox_provisioning_jobs.provider_task_id` + `proxmox_provisioning_audit_events.provider_task_id` + `GET /nodes/pve-test/tasks/<UPID>/status` with `type=qmclone`, `id=9000`, `status=stopped`, `exitstatus=OK` for VMID 9500. No such mapping exists in local evidence.

**Action:** Do not modify historical evidence to make it appear recovered. The gap must be filled only from retained non-secret live evidence or a separately authorized live verification.

## 5. Live disk independence evidence status — LIVE_DISK_INDEPENDENCE_EVIDENCE_UNAVAILABLE_UNDER_CURRENT_PERMISSIONS

**Verdict: `LIVE_DISK_INDEPENDENCE_EVIDENCE_UNAVAILABLE_UNDER_CURRENT_PERMISSIONS`**

No authoritative live evidence for clone disk origin / backing dependency / linked vs full clone state / storage volume relationship to template VM 9000 can be obtained read-only under the existing permission boundary. No live mutation was attempted, and no read-only Proxmox request was issued in this task (see §6).

**Existing permission boundary inspected:**

- Auditor identity: `helper-compute-ro@pve!hc3-6-freeze-ro` via [`readonly_authorization()`](control-api/app/services/helper_compute/proxmox/config.py:206) — `HELPER_COMPUTE_PROXMOX_API_TOKEN` (auditor token present in `.env`, redacted)
- Mutation identity: `helper-compute-hc36@pve!clone-once` via [`mutation_authorization()`](control-api/app/services/helper_compute/proxmox/config.py:199) — `HELPER_COMPUTE_PROXMOX_MUTATION_API_TOKEN` is **absent** (expected; install is operator-only)
- Auditor ACLs (from [`mutation-prerequisites-live.json`](docs/hc36-activation/mutation-prerequisites-live.json:170)): `VM.Audit` on `/`, `Sys.Audit` on `/`, but `VM.Config.Disk` is **not** granted; storage content listing returns 0 rows; volume object GETs return 403

**Authoritative evidence that would be required for `verify_full_clone()` semantics:**

- `GET /nodes/pve-test/qemu/<target_vmid>/config` — must contain `scsi0`, `efidisk0`, `ide2` with `local-lvm:vm-<target>-disk-*` and ownership `description`/`name` matching contract
- `GET /nodes/pve-test/qemu/<target_vmid>/status/current` — must be `stopped`/`stopped`, `onboot=0`, `ha.managed=0`, `template=0`, no lock
- `GET /nodes/pve-test/storage/local-lvm/content` — must contain rows for each target volume with explicit `origin` and `backing` fields; `verify_full_clone()` requires `origin is None` and `backing == []` for every disk, and fails closed with `independence_unverifiable` if either field is missing, plus `linked_clone_origin` / `linked_clone_backing` if linked
- Host-side LVM proof via `/usr/local/sbin/helper-compute-lvm-proof` over SSH as `helper-verify@pve-test` — 10 checks in [`independence_verifier.py`](control-api/app/services/helper_compute/proxmox/independence_verifier.py:1) (target LV names distinct, owned by target VMID, in expected VG/thinpool, sizes consistent, not base volumes, no LVM origin references source, no Proxmox config parent, no storage content parent/origin, expected disk count, source/target config consistent)

**What existing read-only evidence actually shows (from prior live captures, not re-fetched here):**

- [`readonly-mutation-review-live.json`](docs/hc36-activation/readonly-mutation-review-live.json:35): `storage_content_count=0`, `reserved_range_clear=true`, 13 GETs, 0 mutations — no target VM exists to inspect (historical VM 9500 was already cleaned up)
- [`mutation-prerequisites-live.json`](docs/hc36-activation/mutation-prerequisites-live.json:4): blockers include `auditor GET /nodes/pve-test/storage/local-lvm/content returns 0 rows`, `auditor GET of source volume objects returns 403`, `post-clone independence remains independence_unverifiable without readable lvmthin content/parent metadata`; `independence_capability` shows `content_rows=0`, `parent_field_observed=false`, `origin_field_observed=false`, `backing_field_observed=false`, volume object GETs all `403`
- [`independence-verifier-live.json`](docs/hc36-activation/independence-verifier-live.json:8): `ssh_to_pve_test=not_authorized` (`Permission denied (publickey,password)`), `verifier_command` not executable — host-side LVM proof unavailable
- [`staging_guard.py:verify_full_clone`](control-api/app/services/helper_compute/proxmox/staging_guard.py:165) semantics: missing `origin`/`backing` → `independence_unverifiable` (fail-closed); empty `origin=""` is ambiguous, not independence proof

**Conclusion:** Under current least-privilege read-only permissions, the Proxmox API does not expose authoritative `origin`/`backing`/`parent` metadata for lvmthin volumes, and the host verifier SSH path is not authorized. Therefore live disk independence cannot be proven read-only without either (a) granting additional read-only capability or (b) performing a new live clone with full verification. `verify_full_clone()` is **not weakened** to accommodate this.

## 6. Permission limitations

- **Auditor token** (`helper-compute-ro@pve!hc3-6-freeze-ro`): can perform `GET /version`, `GET /cluster/status`, `GET /cluster/resources?type=vm`, `GET /nodes`, `GET /nodes/pve-test/status`, `GET /nodes/pve-test/storage/local-lvm/status`, `GET /nodes/pve-test/storage/local-lvm/content` (returns 0 rows), `GET /nodes/pve-test/network`, `GET /nodes/pve-test/qemu`, `GET /nodes/pve-test/lxc`, `GET /nodes/pve-test/qemu/9000/config`, `GET /nodes/pve-test/qemu/9000/status/current`, `GET /access/permissions` — but **cannot** read `GET /nodes/pve-test/storage/local-lvm/content/<volume>` (403) and therefore cannot obtain `origin`/`backing` fields.
- **Mutation token** (`helper-compute-hc36@pve!clone-once`): **absent** — no `POST /nodes/pve-test/qemu/9000/clone` can be issued; ACLs would be `VM.Clone` on `/vms/9000`, `VM.Allocate` on `/vms/<leased>`, `Datastore.AllocateSpace` on `/storage/local-lvm`, `SDN.Use` on `/sdn/zones/localnetwork/vmbr0` (no `VM.Audit` on this token by design).
- **Host verifier** (`helper-verify@pve-test`): SSH `Permission denied (publickey,password)` — fixed command `/usr/local/sbin/helper-compute-lvm-proof` not executable.
- **No permission broadening** was performed or requested in this task. No new user/token/role was created. No Proxmox ACL was modified.

## 7. Whether another separately authorized live verification is required

**Yes — a separately authorized live verification is the only reasonable way to close the remaining gaps.**

Two gaps remain that cannot be closed read-only under current permissions:

1. **Exact historical UPID** — `HISTORICAL_UPID_UNRECOVERABLE` — the historical clone's UPID was not retained in local evidence. It can only be recovered from retained non-secret live evidence (e.g., Proxmox task log, `pve-task` archive, or operator's original HTTP response capture) or from a new live clone's UPID.
2. **Authoritative disk independence** — `LIVE_DISK_INDEPENDENCE_EVIDENCE_UNAVAILABLE_UNDER_CURRENT_PERMISSIONS` — the auditor cannot read `origin`/`backing` metadata, and the host verifier is not authorized. A new live clone with full verification is required to produce the evidence that `verify_full_clone()` demands.

No live mutation was performed in this task. The minimum live test plan below is provided for **separate authorization only** — do not execute without explicit approval.

### Minimum exact live test required (do not run without separate authorization)

**Preconditions (all must be verified before POST):**

- Isolated lab `data-hc36/control.db` is empty and integrity `ok` (or a fresh isolated DB is prepared)
- `helper_compute_proxmox_clone_transport_enabled=true`, `helper_compute_proxmox_verify_tls=true`, `helper_compute_proxmox_api_url=https://pve-test.home.arpa:8006`, `helper_compute_proxmox_trusted_ca_path=/home/sabry/.local/share/helper-compute/certs/pve-root-ca.pem`, `helper_compute_proxmox_trusted_ca_sha256=1940763fc39896ac5851325bfe2ea8c3e9246ce4c1d74a9ba91f7d71adc907aa`, `helper_compute_proxmox_cluster_fingerprint=hc36-cluster-v1:7ea6f2b0780711f2bbd961b39ab89a4ccd86dfff1a3aca31c06674c7c0a92c8c`, `helper_compute_proxmox_mutation_kill_switch=false` (only for the authorized window), `helper_compute_proxmox_max_real_mutations=1`
- Fresh preflight via auditor GETs: `GET /version`, `GET /cluster/status`, `GET /nodes`, `GET /nodes/pve-test/status`, `GET /nodes/pve-test/storage/local-lvm/status`, `GET /nodes/pve-test/storage/local-lvm/content`, `GET /nodes/pve-test/network`, `GET /nodes/pve-test/qemu/9000/config`, `GET /nodes/pve-test/qemu/9000/status/current`, `GET /cluster/resources?type=vm`, `GET /access/permissions` — all must pass `collect_preflight()` and `source_guard()` with `source_hash=70ee786c24fd689edbe676495ea53a4cd7123d35c79b0c18c4dc1ab9dc3c64e6`, `bridge_hash=7fa9b32add9ea0df9af3b0221583e9f889271e912813ab9768b4d5641410b973`
- Durable lease in `9500–9599` exists and is `leased` to the test job; inventory shows target VMID absent; approval `hc36-single-clone-approval-v1` is fresh and unconsumed

**Exact mutation (exactly one):**

- `POST /api2/json/nodes/pve-test/qemu/9000/clone` with `Authorization: PVEAPIToken=helper-compute-hc36@pve!clone-once!<secret>` and body `newid=<leased_vmid>`, `name=hc3-6-test-clone-<vmid>`, `full=1`, `storage=local-lvm`, `description=helper-compute:hc36:<binding>` — frozen contract only; any other method/path/body is rejected by `assert_frozen_mutation()`

**Required token/permissions:**

- Mutation token: `helper-compute-hc36@pve!clone-once` with `VM.Clone` on `/vms/9000`, `VM.Allocate` on `/vms/<target>`, `Datastore.AllocateSpace` on `/storage/local-lvm`, `SDN.Use` on `/sdn/zones/localnetwork/vmbr0`
- Auditor token: `helper-compute-ro@pve!hc3-6-freeze-ro` with `VM.Audit`/`Sys.Audit`/`Datastore.Audit` for all GET verification (no `VM.Config.Disk` required if host verifier is used; otherwise `VM.Config.Disk` would be needed for storage content parent/origin)

**Expected evidence/output (must be captured sanitized, no secrets):**

- HTTP 200 with `data` = UPID matching `UPID:pve-test:[A-Fa-f0-9]+:[A-Fa-f0-9]+:[A-Fa-f0-9]+:qmclone:9000:helper-compute-hc36@pve!clone-once:` — persist to `proxmox_clone_intents.upid` and `proxmox_provisioning_jobs.provider_task_id` before reconciliation
- `GET /nodes/pve-test/tasks/<UPID>/status` → `upid=<UPID>`, `type=qmclone`, `id=9000`, `status=stopped`, `exitstatus=OK` (via `task_status()` → `OK`)
- `GET /nodes/pve-test/qemu/<vmid>/config` + `GET /nodes/pve-test/qemu/<vmid>/status/current` + `GET /nodes/pve-test/storage/local-lvm/content` → `verify_full_clone()` passes with `origin is None` and `backing == []` for all three disks, `vmid`/`format`/`size`/`storage` verified, `stopped`/`unlocked` true
- Host-side LVM proof (if SSH authorized): `helper-verify@pve-test` → `sudo /usr/local/sbin/helper-compute-lvm-proof` → `LvmProof` with 10 checks in `independence_verifier.py` all passing
- `GET /cluster/resources?type=vm` shows target VMID present on `pve-test` as `qemu` with correct ownership fingerprint

**Rollback/cleanup steps:**

- If task is `failed` or `unknown` or `independence_unverifiable`: retain lease/slot/intent for manual review; do **not** retry POST; do **not** release lease/slot merely because task failed
- If clone succeeded and verification passed: controlled cleanup only if `helper_compute_proxmox_cleanup_delete_enabled=true` — scoped delete via `clone_control.py` cleanup path with exact VMID/job/lease/ownership checks, then `GET /nodes/pve-test/qemu/<vmid>/config` → 400/500 or `GET /cluster/resources?type=vm` shows VMID absent, and `GET /nodes/pve-test/storage/local-lvm/content` shows no `vm-<vmid>-*` volumes; otherwise root-controlled cleanup (`qm destroy <vmid> --purge` + `lvs` verification) as operator-only fallback
- Revoke or rotate mutation token after the single authorized clone; return `helper_compute_proxmox_mutation_kill_switch=true` and `helper_compute_proxmox_clone_transport_enabled=false`

## 8. Final HC3.6 acceptance judgment

**Implementation:** complete. **Mocked verification:** passing (633 tests). **Historical/live evidence:** insufficient for strict acceptance.

Because the exact historical UPID is `HISTORICAL_UPID_UNRECOVERABLE` and live disk independence is `LIVE_DISK_INDEPENDENCE_EVIDENCE_UNAVAILABLE_UNDER_CURRENT_PERMISSIONS`, the strict HC3.6 acceptance boundary is **not satisfied** without another live mutation. Tests passing alone does not constitute final closure.

**Final status: `CHECKPOINT_HC3_6_EVIDENCE_BLOCKED`**

Do not claim `CHECKPOINT_HC3_6_FINAL_PASS` until either (a) the exact historical UPID is recovered from retained non-secret live evidence and authoritative disk independence is proven read-only under an expanded read-only capability, or (b) a separately authorized single live clone is executed per the minimum plan above and produces the UPID + `verify_full_clone()` + task completion evidence.

---

## Files inspected

- `docs/HELPER_COMPUTE_HC3_SESSION6_LIVE_CLONE_VERIFIED.md`
- `docs/HELPER_COMPUTE_HC3_SESSION6_FINAL_ACCEPTANCE.md` (prior version)
- `docs/HELPER_COMPUTE_HC3_SESSION6_INDEPENDENCE_VERIFIER.md`
- `docs/HELPER_COMPUTE_HC3_SESSION6_MUTATION_PREREQUISITES.md`
- `docs/HELPER_COMPUTE_HC3_SESSION6_ONE_CLONE_AUTHORIZATION.md`
- `docs/HELPER_COMPUTE_HC3_SESSION6_STAGING_ACCEPTANCE.md`
- `docs/HELPER_COMPUTE_HC3_SESSION6_REAL_TRANSPORT_ACCEPTANCE.md`
- `docs/hc36-activation/*.json` (13 files: `approval-manifest.template.json`, `independence-verifier-live.json`, `installed-clone-source-evidence.json`, `installed-verification-schema.json`, `mutation-prerequisites-live.json`, `readonly-baseline.json`, `readonly-mutation-review-live.json`, `readonly-request-ledger.json`, `ssh-usage-attempts.json`, `one-clone-authorization-fail-closed.json`, `one-clone-prepared-payload.json`, `plan-review-evidence.json`, `process-local-profile.review.json`)
- `data-hc36/control.db` (tables: `proxmox_clone_intents`, `proxmox_provisioning_jobs`, `proxmox_vmid_leases`, `proxmox_provisioning_audit_events`, `proxmox_mutation_controls`, `proxmox_reservations`)
- `data/control.db` (same tables, older schema)
- `control-api/app/services/helper_compute/proxmox/real_clone_transport.py`
- `control-api/app/services/helper_compute/proxmox/staging_guard.py`
- `control-api/app/services/helper_compute/proxmox/clone_control.py`
- `control-api/app/services/helper_compute/proxmox/config.py`
- `control-api/app/config.py`
- `control-api/tests/test_helper_compute_hc3_6_transport.py`
- Git history (`git log --all -S UPID`, `git log --all --grep=UPID`)
- `.roo/`, `graphify-out/`, `docs/reports/` — no UPID found

## Files changed

- `docs/HELPER_COMPUTE_HC3_SESSION6_FINAL_ACCEPTANCE.md` — replaced with strict evidence closure document (this file)

## Evidence summary

- **Exact historical UPID:** `HISTORICAL_UPID_UNRECOVERABLE` — no exact UPID exists in any local evidence; synthetic test UPIDs are not live evidence
- **Any Proxmox request occurred in this task:** **No** — zero HTTP requests (GET/POST/PUT/PATCH/DELETE) were issued to `https://pve-test.home.arpa:8006` in this evidence-recovery task
- **Exact read-only endpoints/commands used in this task:** **None** — this task performed only local file/DB/git inspection; no `GET /api2/json/...` and no `ssh helper-verify@pve-test` was executed
- **Any live mutation occurred:** **No** — no clone, delete, start, stop, migrate, disk move, snapshot, or config write was performed
- **Disk independence evidence found:** **None authoritative live** — mocked `verify_full_clone()` tests pass, but live `origin`/`backing` metadata is unavailable under current auditor permissions (storage content 0 rows, volume object GETs 403, host verifier SSH denied)
- **Permission limitation:** Auditor lacks `VM.Config.Disk` / storage volume object read for `origin`/`backing`; host verifier SSH not authorized; mutation token absent — all by design and not broadened in this task
- **Whether another live test is actually necessary:** **Yes** — required to obtain the exact UPID and authoritative `origin=None` + `backing=[]` proof that `verify_full_clone()` demands
- **Exact minimum live test plan:** See §7 above — one `POST /nodes/pve-test/qemu/9000/clone` with `full=1`, `storage=local-lvm`, leased VMID `9500–9599`, followed by `GET /nodes/pve-test/tasks/<UPID>/status` + `GET /nodes/pve-test/qemu/<vmid>/config` + `GET /nodes/pve-test/qemu/<vmid>/status/current` + `GET /nodes/pve-test/storage/local-lvm/content` + host LVM proof, with scoped cleanup and token rotation

CHECKPOINT_HC3_6_EVIDENCE_BLOCKED
