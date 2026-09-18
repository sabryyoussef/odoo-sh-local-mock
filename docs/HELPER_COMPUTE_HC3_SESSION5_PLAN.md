# Helper Compute HC3 Session 5 — Proposed Plan

**Status:** PLANNING ONLY — no implementation in this checkpoint-planning task  
**Proposed checkpoint:** `CHECKPOINT_HC3_5_PLAN_AND_DRY_RUN_PASS`  
**Prerequisites:** accepted HC1, HC2, HC3.1, HC3.2, HC3.3, HC3.4; successful HC3.4 live read-only verification report  
**Recommended session objective:** Real Proxmox Provisioning Plan Compiler + Mutation-Disabled Adapter Integration

This proposal treats the implementation that follows this document as HC3.5. It does not enable or perform real Proxmox mutations. HC3.5 should connect the durable HC3.3 job model to a typed real-provider execution plan, validate that plan against fresh HC3.4 discovery, and prove the entire path in dry-run and mocked-transport tests. A later, separately approved checkpoint should enable the first controlled clone on an isolated non-production Proxmox target.

---

## 1. Recommended HC3.5 goal

Build a **mutation-disabled real provisioning integration boundary** that converts one claimed HC3.3 `ProxmoxProvisioningJob` plus its active `ProxmoxReservation` into an immutable, auditable `ProxmoxProvisioningPlan`.

HC3.5 should:

1. Reuse HC3.4 read-only discovery to preflight the reserved node, template, storage, and current capacity.
2. Resolve a deterministic VMID and clone parameters without sending POST, PUT, PATCH, or DELETE requests.
3. Define the typed provider operations and results that a later real mutation adapter will implement.
4. Integrate dry-run execution with the HC3.3 job path without consuming or releasing the reservation.
5. Test the future mutation workflow through a stateful fake or mocked HTTP transport only.
6. Produce a sanitized operator-visible plan and audit trail suitable for an explicit go/no-go review.

HC3.5 should end with real provisioning still disabled and no background real-provisioning worker enabled.

## 2. Why HC3.5 is the correct next step

The accepted architecture already has the components needed before infrastructure execution:

- HC1 supplies resource calculation and capacity semantics.
- HC2 supplies customer-facing reservations and committed accounting.
- HC3.1 supplies the provider contract and deterministic fake provider.
- HC3.2 supplies durable Proxmox reservations, deterministic placement, atomic capacity protection, and consume/release operations.
- HC3.3 supplies durable jobs, worker claims, bounded retries, idempotency, rollback states, and reservation handoff.
- HC3.4 supplies real, strictly read-only Proxmox discovery and a separate enablement boundary.

The remaining architectural risk is therefore not another state machine. It is translating business intent into exact Proxmox operations while preserving HC3.3 ownership, idempotency, and safety. Jumping directly from discovery to a live clone would combine too many unproven decisions: VMID ownership, template identity, storage syntax, cloud-init inputs, network policy, task polling, ambiguous timeout recovery, and cleanup.

A plan compiler and dry-run integration closes that gap. It makes the first future mutation reviewable and deterministic while retaining the structural separation established by HC3.4.

HC3.4 live verification is a prerequisite because the plan compiler must be validated against the actual cluster's node names, template inventory, storage types, task behavior, TLS configuration, and permission model. If HC3.4 live findings differ from the acceptance assumptions, HC3.5 planning inputs should be updated before implementation begins.

## 3. Exact scope

The proposed HC3.5 implementation scope is:

- A typed, immutable provisioning plan and normalized operation-result contracts.
- A plan compiler that accepts only an HC3.3 job in `provisioning` state and its referenced active reservation.
- Fresh read-only preflight through the HC3.4 discovery service.
- Deterministic resolution of target node, template VMID, target VMID, storage, CPU, RAM, disk, hostname, tags, and network/cloud-init profile identifiers.
- A provisioning-provider protocol separate from the read-only discovery protocol.
- A dry-run provider that records the operations it would request and performs no mutations.
- A mocked mutation transport used only in automated tests to model clone, task polling, configure, resize, and cleanup responses.
- Durable persistence of the plan fingerprint and provider identifiers required for restart recovery.
- Append-only, sanitized audit events for plan creation, preflight, execution intent, provider task observation, success, failure, and rollback intent.
- HC3.3 integration changes narrowly limited to selecting a provider, compiling a plan, and classifying typed results.
- Unit, integration, failure-injection, idempotency, concurrency, and restart-recovery tests using fake or mocked transports.
- A live **read-only/dry-run** acceptance procedure against the HC3.4 non-production target.

## 4. Explicit out-of-scope items

The following are out of scope for HC3.5:

- Sending any real Proxmox mutation request.
- Creating, cloning, configuring, starting, stopping, resizing, or deleting a real VM.
- Enabling a real provisioning worker or changing production worker scheduling.
- Production credentials, production clusters, or production tenants.
- Automatic IP allocation or integration with a production IPAM system.
- Creating or modifying Proxmox bridges, VLANs, SDN zones, firewall rules, storage pools, templates, roles, users, tokens, HA groups, or backup policies.
- Guest bootstrap beyond producing a bounded cloud-init specification.
- Injecting customer passwords, API keys, application secrets, SSH private keys, or Odoo database credentials.
- Cross-node migration, replication, HA placement, Ceph tuning, snapshots, backup orchestration, or disaster recovery.
- Replacing the HC3.2 placement/reservation mechanism or redesigning the HC3.3 state machine.
- Customer UI, billing changes, order flow changes, or TM-D12/E1.7 work.
- A production rollout or a claim that real provisioning is generally available.

The first real clone should be a later checkpoint with separate authorization, an isolated target, a disposable template, a dedicated mutation token, and a reviewed cleanup procedure.

## 5. Architecture changes

HC3.5 should add a planning/execution seam while preserving discovery and durable orchestration:

```text
HC3.3 claimed job + active HC3.2 reservation
                  |
                  v
       ProvisioningPlanCompiler
          |               |
          |               +--> HC3.4 read-only discovery/preflight
          v
 immutable ProxmoxProvisioningPlan
          |
          v
 ProvisioningProvider protocol
    |                         |
    +--> DryRunProvider       +--> MockMutationProvider (tests only)
          no writes                 simulated operations/tasks

Future checkpoint only:
    +--> RealProxmoxMutationAdapter -> allowlisted Proxmox writes
```

The plan compiler should be pure after it receives a normalized discovery snapshot. It must not read environment variables, open network connections, or write job state. The orchestration service obtains the snapshot, compiles the plan, persists its fingerprint, and invokes the selected provider.

Discovery and provisioning must remain distinct capabilities. The HC3.4 read-only adapter must never acquire mutation methods. A future mutation adapter may share low-level authentication/error primitives, but it must have its own explicit method and path allowlists.

## 6. Provider abstraction changes

Retain the existing discovery-facing `ProxmoxProvider` behavior for compatibility, but introduce a narrower provisioning protocol rather than widening the HC3.4 adapter.

Proposed concepts:

```python
class ProxmoxProvisioningProvider(Protocol):
    provider_name: str
    mode: Literal["fake", "dry_run", "mock_mutation", "real"]

    def preflight(
        self, plan: ProxmoxProvisioningPlan
    ) -> ProvisioningPreflightResult: ...

    def provision(
        self, plan: ProxmoxProvisioningPlan
    ) -> ProvisioningExecutionResult: ...

    def inspect_existing(
        self, identity: ProxmoxResourceIdentity
    ) -> ExistingResourceResult: ...

    def rollback(
        self, plan: ProxmoxProvisioningPlan,
        observed: ProxmoxResourceIdentity
    ) -> ProvisioningRollbackResult: ...
```

Rules:

- `DryRunProxmoxProvisioningProvider.provision()` returns a dry-run result and cannot obtain a mutation-capable client.
- `MockMutationProxmoxProvisioningProvider` exists only under tests or test support and simulates Proxmox UPIDs and resources.
- A future `RealProxmoxMutationAdapter` implements the same protocol but is not selected or enabled in HC3.5.
- Provider results are typed. HC3.3 should no longer infer domain behavior from loosely structured dictionaries for the real-provider path.
- Provider errors use stable codes and sanitized messages; raw HTTP bodies and tokens never cross the adapter boundary.

## 7. How real Proxmox provisioning should eventually connect to HC3.3 jobs

The HC3.3 job remains the source of orchestration truth. Real provisioning should connect at the existing `execute_job()` provider invocation point, with these steps:

1. `claim_job()` atomically moves one eligible job from `queued` to `provisioning`, records the worker lease, and increments `attempt_count`.
2. The executor loads the referenced reservation and verifies it is still `active`, unexpired, and resource-identical to the job.
3. The executor selects a provider through a fail-closed factory based on the job's persisted provider/mode, not on an unrecorded runtime default.
4. The executor obtains a fresh HC3.4 read-only snapshot and compiles or reloads the immutable plan.
5. It verifies the plan fingerprint matches the persisted job fingerprint. A mismatch becomes `plan_conflict` and does not mutate infrastructure.
6. The provider performs `inspect_existing()` before any create/clone request. If the expected VM already exists and carries the matching ownership fingerprint, execution resumes by observation rather than creating another VM.
7. In a future mutation checkpoint, the adapter issues the clone operation with the reserved node/template/VMID and persists the returned UPID before proceeding.
8. The executor polls the task by UPID, reconciles ambiguous timeouts through read-only inspection, applies bounded configuration operations, and verifies final resource identity.
9. Only after the VM exists with the expected ownership fingerprint and required configuration does HC3.3 transition to `ready` and atomically `consume_reservation()`.
10. A permanent failure with no resource releases/fails the reservation. A partial resource enters `rollback_pending`; capacity remains reserved until cleanup is proven.

HC3.5 should exercise this sequence with dry-run and mocked providers. It must not change `dry_run` into `ready`, because no VM exists. The recommended dry-run outcome is an operator-visible preflight result while the job stays `reserved` or returns from a dedicated validation operation without entering the execution state machine. If dry-run is invoked on a claimed test job, the transaction should restore it to `queued` with no attempt charged, or use a separate `preview_job()` path. The separate preview path is preferred.

## 8. Safety model

Safety uses multiple independent gates:

1. **Capability separation:** the HC3.4 adapter remains GET-only and cannot mutate.
2. **Fail-closed provider factory:** unknown mode, incomplete configuration, placeholder URL, or inconsistent flags returns a configuration error.
3. **Dry-run default:** HC3.5 supports fake and dry-run selection only outside tests.
4. **Target allowlist:** future real mode requires an exact cluster fingerprint and allowed node/template/storage/network identifiers.
5. **Environment gate:** real mode is forbidden for `production` during initial mutation checkpoints.
6. **Credential gate:** future mutation credentials are separate from the read-only token and limited to the minimum ACL paths and privileges.
7. **Ownership marker:** every managed VM must carry job ID, request ID, tenant ID, and plan fingerprint in Proxmox tags/description, excluding secrets.
8. **Preflight freshness:** mutations are refused when discovery is stale, the node is offline/maintenance, the template changed, storage is unavailable, or capacity no longer fits.
9. **Bounded mutation surface:** future writes use explicit endpoint/method pairs; arbitrary paths and generic `post()` access are unavailable above the client boundary.
10. **Audit before action:** intent and sanitized plan fingerprint persist before the first write.
11. **No destructive guessing:** rollback deletes only a resource whose VMID and ownership marker both match the job.
12. **Manual quarantine:** ambiguous ownership, failed cleanup, or uncertain task outcome retains the reservation and requires operator review.

## 9. Feature flags

Proposed flags and defaults for a future HC3.5 implementation:

| Setting | Default | HC3.5 allowed values | Purpose |
|---|---:|---|---|
| `HELPER_COMPUTE_PROXMOX_PROVISIONING_MODE` | `fake` | `fake`, `dry_run` | Explicit provider mode; `real` must be rejected in HC3.5 |
| `HELPER_COMPUTE_PROXMOX_DRY_RUN` | `true` | must remain `true` for Proxmox dry-run | Existing independent safety gate |
| `HELPER_COMPUTE_PROXMOX_ENABLED` | `false` | must remain `false` in HC3.5 live acceptance | Existing real provisioning master gate |
| `HELPER_COMPUTE_PROXMOX_WORKER_ENABLED` | `false` | `false` | Prevents background real job execution |
| `HELPER_COMPUTE_PROXMOX_ALLOWED_CLUSTER_FINGERPRINT` | empty | optional in dry-run, mandatory later | Pins the intended cluster |
| `HELPER_COMPUTE_PROXMOX_ALLOWED_NODES` | empty | optional allowlist | Restricts target nodes |
| `HELPER_COMPUTE_PROXMOX_ALLOWED_TEMPLATES` | empty | optional allowlist | Restricts source template VMIDs |
| `HELPER_COMPUTE_PROXMOX_ALLOWED_STORAGES` | empty | optional allowlist | Restricts destination storage |
| `HELPER_COMPUTE_PROXMOX_ALLOWED_BRIDGES` | empty | optional allowlist | Restricts network attachment |
| `HELPER_COMPUTE_PROXMOX_ALLOW_START` | `false` | `false` | Start is a distinct future permission |
| `HELPER_COMPUTE_PROXMOX_ALLOW_ROLLBACK_DELETE` | `false` | `false` | Delete is a distinct future permission |

Compatibility rule: existing flags remain authoritative. A future real provider requires all relevant booleans and mode values to agree; no single flag can enable writes. HC3.5 tests must prove `PROVISIONING_MODE=real` is rejected.

## 10. Idempotency requirements

- `request_id` and `idempotency_key` retain their existing unique guarantees.
- A plan has a canonical serialization and SHA-256 `plan_fingerprint` derived from all mutation-relevant fields: provider, cluster fingerprint, reservation/job IDs, node, template VMID, target VMID, storage, resources, hostname, network profile, cloud-init public inputs, and schema version.
- The same job and unchanged inputs must compile to the same fingerprint across processes and restarts.
- The job stores `target_vmid`, `plan_fingerprint`, and any Proxmox `upid` before moving to the next operation.
- Every execution begins with `inspect_existing(target_vmid)`.
- Existing VM with matching ownership fingerprint means resume/reconcile.
- Existing VM with no marker or a different fingerprint means terminal `vmid_conflict`; never modify or delete it.
- Retrying after a timeout must poll the recorded UPID and inspect the VM before issuing another clone.
- Configure operations must be convergent: compare desired and observed settings, then apply only missing/different allowlisted fields.
- Rollback is idempotent: absent resource is success; matching managed resource may be cleaned in a later authorized checkpoint; mismatched resource is quarantined.
- Audit-event deduplication uses `(job_id, operation_key, attempt, event_type)` or another unique operation identifier.

## 11. Reservation → provisioning → consume/release lifecycle

```text
active reservation
      |
      +--> create HC3.3 job (reserved)
      |         |
      |         +--> preview/dry-run: reservation remains active
      |         |
      |         +--> queued -> claimed/provisioning
      |                         |
      |                         +--> retryable, no partial resource
      |                         |       reservation remains active
      |                         |
      |                         +--> verified managed VM
      |                         |       atomically consume reservation
      |                         |       reserved -> committed; job -> ready
      |                         |
      |                         +--> permanent failure, no VM
      |                         |       fail/release reservation
      |                         |
      |                         +--> partial/ambiguous VM
      |                                 rollback_pending/quarantine
      |                                 reservation remains active
      |                                 release only after absence proven
      |
      +--> cancel before mutation: release reservation
```

Expiry must not release a reservation owned by a queued, provisioning, or rollback-pending job. HC3.5 should define an ownership check or lease extension so reservation expiry cannot race with execution. Consumption must be in the same database transaction as the final job transition to `ready`. Release/failure must likewise update capacity and terminal job state transactionally where practical.

## 12. Failure and rollback behavior

| Failure | Classification | Job action | Reservation action | Infrastructure action in HC3.5 |
|---|---|---|---|---|
| Invalid/stale plan | permanent | `failed` or remain unqueued | release if no operation began | none |
| Auth/permission failure | permanent/config | `failed`, alert | release if absence proven | none |
| Network error before request | retryable | requeue with backoff | retain | none |
| Clone accepted, UPID recorded | in progress | poll/reconcile | retain | mocked only |
| Timeout with unknown acceptance | ambiguous | quarantine/reconcile | retain | read-only inspect only |
| Proxmox task failure, no VM | permanent/retryable by code | fail or requeue | release only if absence proven | mocked only |
| VM exists, config incomplete | partial | `rollback_pending` | retain | mocked rollback intent only |
| VM ownership mismatch | conflict | quarantine | retain | never modify/delete |
| Rollback succeeds | terminal | `rolled_back` | fail/release | mocked only |
| Rollback fails/unknown | manual intervention | `failed` with partial flag | retain | no further automatic action |

HC3.5 should not treat every timeout as retryable. Timeout after a request may mean the task was accepted. The executor must reconcile via UPID and VM inspection before deciding whether another mutation is safe.

## 13. VMID allocation strategy

Recommended strategy: **database-leased allocation from a dedicated, configured non-production VMID range**, checked against live Proxmox inventory.

- Configure a narrow range, such as an operator-assigned HC range; do not assume or hardcode a sample range in code.
- Persist a `ProxmoxVmidLease` (or equivalent) with `vmid`, `job_id`, `request_id`, `cluster_fingerprint`, `state`, timestamps, and unique constraints on `(cluster_fingerprint, vmid)` and `job_id`.
- Select the lowest available VMID within the range for deterministic behavior.
- Acquire with a database uniqueness constraint/conditional insert so concurrent planners cannot lease the same VMID.
- Before finalizing the lease, use HC3.4 discovery or a dedicated read-only lookup to prove the VMID is absent cluster-wide.
- The lease remains attached through retry, partial failure, and quarantine. Release it only when no VM exists or cleanup is proven.
- An existing matching managed VM retains the same lease and resumes.
- An externally occupied VMID marks the lease conflicted and allocates a new VMID only before any mutation/ownership marker exists.

Hash-only VMIDs are not recommended because collisions and operator-created resources require probing anyway. Calling Proxmox `cluster/nextid` alone is also insufficient because it does not reserve the returned ID against concurrent external actors.

## 14. Template clone strategy

- Use **full clone** for the first real provisioning checkpoint unless the selected storage and template have an explicitly validated linked-clone policy.
- Resolve the business `template_id` to the authoritative HC3.4 template record and persist source node plus numeric source VMID in the plan.
- Require `template=1`, allowlisted template VMID, known template version/image checksum metadata, compatible storage, and required cloud-init drive.
- Refuse a template that changes identity between planning and execution.
- Clone to the reserved target node and selected target storage using the preallocated target VMID and deterministic name.
- Persist the returned Proxmox UPID immediately.
- Poll task status to a terminal result; do not infer success merely because the VM appears.
- Apply resource resizing only after clone completion and through explicit, convergent configuration steps.
- Starting the VM is a later, separately gated operation after cloud-init/network plan validation.

## 15. Cloud-init boundary

HC3.5 should define and validate a `CloudInitSpec`; it should not deliver secrets to a guest or write live VM configuration.

Allowed plan fields:

- sanitized hostname;
- authorized **public** SSH keys or a reference to a future secret-delivery mechanism;
- DHCP intent or an opaque approved static-network allocation reference;
- DNS/search-domain values from an allowlisted profile;
- non-secret user-data profile/version identifier;
- locale/timezone if catalog controlled.

Forbidden fields:

- plaintext passwords;
- SSH private keys;
- Proxmox tokens;
- Odoo admin/database credentials;
- arbitrary user-supplied shell commands;
- unrestricted raw cloud-init YAML.

The plan stores secret references, never resolved secret values. A future executor should resolve short-lived secrets only at the last responsible moment and must not persist them in job rows, audit events, exceptions, or rendered dry-run output.

## 16. Network boundary

- HC3.5 maps `network_profile` to a catalog-controlled `NetworkAttachmentSpec`.
- Only an existing, allowlisted bridge and optional allowlisted VLAN tag may be selected.
- No Proxmox network, bridge, VLAN, SDN, firewall, DHCP, DNS, or IPAM mutation belongs in HC3.5.
- Default first-live strategy should be DHCP on an isolated non-production bridge.
- Static networking requires a later durable IPAM lease with uniqueness, expiry, ownership, and release semantics.
- NAT and public exposure require separate design and acceptance.
- The plan should include NIC model, bridge, VLAN tag if applicable, firewall-on intent, and MAC strategy; it must not accept arbitrary Proxmox `net0` strings from users.
- Connectivity verification is outside HC3.5 live acceptance because no VM is started.

## 17. Storage selection

- Select only from online storage discovered on the reserved target node.
- Require the content type to support VM disk images and the template/clone mode to be compatible with the storage backend.
- Respect an explicit storage class mapping maintained by operators; do not rely on a universal `local-lvm` preference.
- Use the HC3.2 reservation's `storage_pool` when present. A different pool requires a new reservation or explicit replan before execution.
- Verify free bytes with headroom immediately before execution and account for full-clone size plus requested expansion.
- Persist the exact storage ID and observed storage type in the plan fingerprint.
- Do not fall back silently. If the reserved pool becomes invalid, fail preflight and re-reserve through an explicit workflow.
- Avoid shared/local assumptions: shared storage affects clone targeting and migration behavior and must be explicit.

## 18. Timeout/retry handling

Separate timeouts by phase:

- API connect/read timeout for individual requests.
- Clone submission timeout.
- Proxmox task polling interval and task deadline.
- Configuration operation deadline.
- Whole-attempt deadline and worker lease timeout.

Retry rules:

- Retry safe GET/poll operations with exponential backoff and bounded jitter.
- Retry a mutation submission only when the adapter proves it was not accepted.
- If acceptance is ambiguous, reconcile by recorded UPID, target VMID, and ownership fingerprint.
- Classify Proxmox errors into stable domain codes: authentication, permission, validation, conflict, capacity, unavailable, task timeout, task failure, transport, and malformed response.
- Use bounded attempts from HC3.3; do not nest unbounded provider retries inside one job attempt.
- Store `next_retry_at`; workers do not sleep while holding a database transaction.
- Retry delays should be configurable and testable with an injected clock/random source.

Suggested initial policy for later mutation tests: API GET retries up to 3; mutation submission at most once per reconciliation cycle; task polling until a configured deadline; HC3.3 job attempts remain capped at 3.

## 19. Concurrency protection

- Keep HC3.2 atomic conditional capacity update.
- Keep HC3.3 optimistic job claim and version check.
- Add a durable worker lease with expiry/heartbeat before real execution so abandoned `provisioning` jobs can be reconciled safely.
- Use database uniqueness for VMID leases and job plan ownership.
- Allow only one active execution operation per job through an operation key and unique constraint.
- Use the persisted UPID as the task identity; two workers observing one UPID may poll, but only the lease owner may transition state.
- Recheck `version`, `claimed_by`, and lease freshness on every state-changing database update.
- Prevent reservation expiry/release while a non-terminal job owns the reservation.
- Test two jobs competing for one VMID and two workers reclaiming an expired lease.

SQLite conditional writes are acceptable for deterministic tests. Before production, use PostgreSQL row-level locking or equivalent transactional semantics and verify them under the deployed database.

## 20. Audit logging

Add an append-only domain audit record rather than relying only on application logs. Proposed fields:

- `event_id`, `job_id`, `reservation_id`, `request_id`, `tenant_id`;
- `event_type`, `operation_key`, `from_state`, `to_state`, `attempt`;
- `provider`, `provider_mode`, `cluster_fingerprint`, `node_id`, `target_vmid`;
- `plan_fingerprint`, sanitized `provider_task_id`/UPID;
- `outcome_code`, sanitized message, `actor_type`, `actor_id`, timestamp;
- structured metadata constrained by an allowlist.

Required events include plan compiled, preflight passed/failed, dry-run rendered, worker claimed, operation intended, task accepted, task observed, reconciliation result, state transitioned, reservation consumed/released/retained, rollback requested/completed/failed, and manual intervention required.

Never record authorization headers, token IDs plus secrets, cloud-init secret payloads, raw API responses, passwords, private keys, or arbitrary exception representations. Test both value redaction and key-name redaction.

## 21. Secret handling

- Keep credentials in environment-backed secret injection or a secrets manager; never in source, `.env.example` values, database rows, job metadata, plan JSON, audit records, or evidence documents.
- Use a separate future mutation token from the HC3.4 `PVEAuditor` token.
- Scope the mutation token to the isolated pool/path, template, target storage, and required privileges only.
- Avoid root credentials and password authentication.
- Keep TLS verification enabled with an installed CA whenever possible. Disabling verification is acceptable only for a documented isolated acceptance target and must remain an explicit exception.
- Construct authentication headers at request time and redact them from tracing.
- Support token rotation by reading credentials through a provider/client factory; durable jobs store no token-derived state.
- Sanitize nested mappings, URLs, headers, exceptions, task output, and audit metadata.
- Dry-run output shows secret references as opaque identifiers and never resolves them.

## 22. Dry-run strategy

Dry-run is a first-class preview, not a fake success.

`preview_provisioning_job()` should:

1. Load the job and active reservation without claiming the job.
2. Fetch fresh HC3.4 discovery data.
3. Compile and validate the immutable plan.
4. Run provider preflight and existing-resource inspection using GET-only capability.
5. Render an ordered list of intended operations with method/path categories, never credentials or sensitive payloads.
6. Persist the plan fingerprint and sanitized audit event only if the caller explicitly requests durable preview; otherwise remain side-effect-free in infrastructure.
7. Return `dry_run=True`, `mutation_attempted=False`, and a list of gates that would still block real execution.

Tests must intercept all outbound HTTP calls during dry-run and assert every call is GET. They must also prove the job attempt count, state, reservation status, reserved counters, and committed counters remain unchanged.

The dry-run plan should identify operations such as lease VMID, clone template, await UPID, configure CPU/RAM, resize disk, attach approved network, set cloud-init fields, optionally start, verify, and consume reservation. It must clearly mark start/delete as disabled when their flags are false.

## 23. Proposed API/service interfaces

Proposed domain types:

```python
@dataclass(frozen=True)
class ProxmoxResourceIdentity:
    cluster_fingerprint: str
    node_id: str
    vmid: int
    ownership_fingerprint: str

@dataclass(frozen=True)
class CloudInitSpec:
    hostname: str
    ssh_public_key_refs: tuple[str, ...]
    user_data_profile: str | None
    network: NetworkAttachmentSpec

@dataclass(frozen=True)
class ProxmoxProvisioningPlan:
    schema_version: int
    job_id: str
    reservation_id: str
    request_id: str
    target: ProxmoxResourceIdentity
    source_node_id: str
    template_vmid: int
    template_version: str
    storage_pool: str
    clone_mode: Literal["full"]
    vcpu: int
    ram_mb: int
    disk_gb: int
    hostname: str
    cloud_init: CloudInitSpec
    tags: tuple[str, ...]
    plan_fingerprint: str

@dataclass(frozen=True)
class ProvisioningPreflightResult:
    valid: bool
    dry_run: bool
    mutation_attempted: bool
    errors: tuple[ProviderError, ...]
    warnings: tuple[ProviderWarning, ...]
    observed_snapshot_fingerprint: str

@dataclass(frozen=True)
class ProvisioningExecutionResult:
    outcome: Literal["ready", "in_progress", "retryable_failure",
                     "permanent_failure", "partial", "ambiguous"]
    resource: ProxmoxResourceIdentity | None
    provider_task_id: str | None
    retryable: bool
    safe_to_release_reservation: bool
    error: ProviderError | None
```

Proposed services:

```python
def compile_provisioning_plan(
    *, job: ProxmoxProvisioningJob,
    reservation: ProxmoxReservation,
    snapshot: ClusterDiscoverySnapshot,
    policy: ProxmoxProvisioningPolicy,
    vmid: ProxmoxVmidLease,
) -> ProxmoxProvisioningPlan: ...

def preview_provisioning_job(
    db: Session, job_id: str, *, actor: AuditActor
) -> ProvisioningPreview: ...

def reconcile_provisioning_job(
    db: Session, job_id: str, *, provider: ProxmoxProvisioningProvider
) -> ReconciliationResult: ...

def get_provisioning_provider(
    *, mode: str
) -> ProxmoxProvisioningProvider: ...
```

Potential persistence additions for the eventual implementation:

- Job fields: `provider_mode`, `plan_schema_version`, `plan_fingerprint`, `target_vmid`, `provider_task_id`, `worker_lease_expires_at`, `last_reconciled_at`.
- `ProxmoxVmidLease` table.
- `ProxmoxProvisioningAuditEvent` table.

Schema changes should be additive. Exact migration strategy must match the project's database migration policy and be tested on both a fresh database and an upgraded fixture.

## 24. Proposed tests

Create a new HC3.5 test file; do not modify accepted HC3.4 tests merely to make the new design pass.

### Plan compiler tests

- Same inputs produce byte-identical canonical plan and fingerprint.
- Each mutation-relevant input changes the fingerprint.
- Job/reservation resource mismatch is rejected.
- Non-active or expired reservation is rejected.
- Stale/offline/maintenance node is rejected.
- Missing/changed/non-template source is rejected.
- Storage incompatibility and insufficient full-clone space are rejected.
- Arbitrary network bridge/VLAN/cloud-init input is rejected.

### VMID and concurrency tests

- Lowest eligible VMID is leased deterministically.
- Existing Proxmox VMID is skipped.
- Two concurrent jobs cannot lease the same VMID.
- Repeated planning returns the same lease.
- Conflicting ownership marker never permits adoption or deletion.

### Dry-run safety tests

- All outbound requests are GET.
- No mutation client is constructed.
- `PROVISIONING_MODE=real` is rejected.
- Job state/attempt count and reservation/capacity counters are unchanged.
- Output contains no token, password, private key, raw authorization header, or secret cloud-init value.
- Dry-run produces the ordered intended-operation list and blocking gates.

### HC3.3 integration tests with mocked mutation transport

- Claim → compile → clone task → configure → verify → `ready` consumes exactly once.
- Duplicate execute reconciles and never sends a second clone.
- Restart after UPID persistence resumes polling.
- Timeout before acceptance retries safely.
- Timeout after ambiguous acceptance inspects before deciding.
- Matching existing managed VM resumes; mismatched VMID fails closed.
- Permanent pre-clone failure releases/fails reservation.
- Partial resource retains reservation until mocked rollback proves absence.
- Rollback failure leaves job quarantined and capacity retained.
- Two workers cannot execute the same operation.
- Expired worker lease recovery reconciles before mutation.

### Regression/security tests

- All HC1–HC3.4 tests pass unchanged.
- HC3.4 read-only adapter still has no mutation methods.
- Fake provider behavior remains deterministic.
- Unknown flags/providers fail closed.
- Audit event uniqueness and redaction work recursively.

## 25. Proposed real-environment acceptance test

HC3.5 real-environment acceptance must be **read-only and dry-run only** on the same isolated target validated by HC3.4. It must not use a mutation-capable token.

Preconditions:

- HC3.4 live read-only verification has passed with a dedicated `PVEAuditor` token.
- `HELPER_COMPUTE_PROXMOX_ENABLED=false`.
- `HELPER_COMPUTE_PROXMOX_DRY_RUN=true`.
- `HELPER_COMPUTE_PROXMOX_PROVISIONING_MODE=dry_run`.
- Background provisioning worker disabled.
- Target cluster fingerprint, allowed node, template, storage, and bridge documented.
- A synthetic non-production job/reservation fixture exists only in the application database.

Procedure:

1. Record sanitized flags and cluster fingerprint.
2. Capture database job/reservation/capacity state before preview.
3. Run `preview_provisioning_job()` for one synthetic job.
4. Capture all outbound HTTP methods and paths; assert GET-only HC3.4 allowlisted discovery calls.
5. Compare the compiled node, numeric template VMID, storage type/ID, requested resources, and network profile with live read-only inventory.
6. Verify target VMID is absent using read-only inventory.
7. Run the preview again from a new process/session and assert identical plan fingerprint and VMID lease result.
8. Capture database state after preview and prove job state, attempt count, reservation status, reserved counters, and committed counters are unchanged, except explicitly approved audit/plan-preview records.
9. Search sanitized logs/evidence for credential leakage.
10. Verify no VM/task/config/storage/network changes occurred by comparing before/after live inventories.

Required evidence:

- sanitized configuration matrix;
- cluster/node/template/storage discovery summaries;
- plan fingerprint and intended-operation list;
- outbound method/path capture showing GET only;
- before/after database lifecycle state;
- before/after Proxmox inventory diff showing zero mutations;
- focused and regression test results;
- explicit statement that the read-only token lacks mutation privileges.

Passing this acceptance test authorizes planning of a later controlled mutation checkpoint. It does not authorize a live clone.

## 26. Rollback plan

Because HC3.5 must not mutate Proxmox, its operational rollback is application-only:

1. Set provisioning mode to `fake`, keep the real master gate false, and keep the worker disabled.
2. Stop invoking the preview endpoint/service.
3. Preserve audit evidence and job/reservation records for diagnosis.
4. Remove or disable only HC3.5-added routing/factory selection in a focused change if it causes regressions.
5. Leave HC3.4 read-only discovery available independently if it remains healthy.
6. Do not delete or rewrite accepted HC3.1–HC3.4 records or tests.
7. For additive schema, leave columns/tables in place during rollback unless a separately reviewed migration proves removal is safe.

For the later mutation checkpoint, rollback must be plan-aware: stop new claims, reconcile UPIDs and target VMIDs, clean only matching managed resources, retain capacity for ambiguous/partial resources, rotate/revoke the mutation token if needed, and return provider mode to dry-run.

## 27. Acceptance criteria

HC3.5 implementation should pass only when all of the following are true:

- [ ] Immutable provisioning plan and canonical fingerprint are implemented and deterministic.
- [ ] Plan derives from one HC3.3 job, its active HC3.2 reservation, fresh HC3.4 discovery, explicit policy, and a durable VMID lease.
- [ ] Discovery and provisioning capabilities remain separate.
- [ ] Existing HC3.4 adapter remains structurally GET-only.
- [ ] Runtime supports only fake/dry-run real-environment behavior; selecting real mutations fails closed.
- [ ] Dry-run sends zero mutation requests and changes no lifecycle/capacity state.
- [ ] Provider interfaces and typed results cover future clone, inspection, reconciliation, and rollback.
- [ ] Mocked integration proves success, retry, ambiguity, restart recovery, partial failure, rollback, and concurrency behavior.
- [ ] Exactly-once effect is achieved through inspect/reconcile semantics, not by assuming exactly-once delivery.
- [ ] Reservation is consumed exactly once only after a verified managed VM; it is released only when absence/cleanup is proven.
- [ ] VMID allocation is durable, unique, deterministic, and checked against live inventory.
- [ ] Template, storage, cloud-init, and network inputs are catalog controlled and allowlisted.
- [ ] Audit events are durable, deduplicated, sanitized, and sufficient to reconstruct the operation sequence.
- [ ] Secrets never enter plans, database records, audit events, logs, errors, test output, or evidence.
- [ ] Worker lease and operation-level concurrency behavior are tested.
- [ ] All HC1, HC2, HC3.1, HC3.2, HC3.3, and HC3.4 regressions pass unchanged.
- [ ] Live HC3.5 acceptance uses only the read-only token and proves zero Proxmox mutations.
- [ ] Real provisioning, background worker, production access, TM-D12, and E1.7 remain disabled/untouched.
- [ ] Acceptance report records exact files changed, test totals, sanitized evidence, limitations, and rollback state.

## 28. Proposed final checkpoint token

Recommended implementation checkpoint token:

```text
CHECKPOINT_HC3_5_PLAN_AND_DRY_RUN_PASS
```

This token deliberately records what HC3.5 proves: a deterministic real-provider plan, a safe bridge to HC3.3 orchestration, and live dry-run validation with zero Proxmox mutations. It must not be interpreted as approval for real VM creation.

---

## Recommended HC3.5 definition

**HC3.5 is the Real Proxmox Provisioning Plan Compiler + Mutation-Disabled Adapter Integration checkpoint.** It turns durable HC3.3 jobs and HC3.2 reservations into deterministic, auditable Proxmox plans; validates those plans using HC3.4 read-only discovery; proves execution semantics with fake/mocked providers; and completes a live GET-only dry-run against the isolated Proxmox target. Real mutation remains a later checkpoint.

## Risks

- HC3.4 live inventory may expose template, storage, TLS, or permission assumptions that require plan-policy changes.
- Current HC3.2 database capacity and live Proxmox utilization can drift; preflight must not pretend they are a single atomic system.
- The existing HC3.3 dictionary provider result and `fake_resource_id` fields need careful additive evolution to typed real resource identity.
- Reservation TTL may race with queued or claimed jobs until explicit ownership/lease protection exists.
- Ambiguous Proxmox task timeouts can create duplicates if reconciliation is incomplete.
- SQLite behavior can hide locking differences that appear under PostgreSQL or multiple workers.
- Full clones can require materially more storage and time than the current requested disk estimate.
- Cloud-init and network profiles become secret/injection boundaries if arbitrary payloads are admitted.
- Rollback deletion is inherently high risk and must remain separately gated with ownership proof.

## Dependencies

- Final HC3.4 live read-only verification findings and sanitized cluster fingerprint.
- An eligible non-production cloud-init template with stable numeric VMID and documented version.
- Operator-approved storage-class mapping and adequate full-clone capacity/headroom.
- Operator-approved isolated network profile and bridge allowlist.
- A documented VMID range dedicated to Helper Compute.
- Database migration approach for plan fields, VMID leases, audit events, and worker leases.
- Agreement on reservation ownership/TTL semantics while jobs are queued or provisioning.
- A stateful mocked Proxmox transport capable of UPID/task and partial-resource simulation.
- A later, separate least-privilege mutation token and approval process; not required or used by HC3.5 dry-run acceptance.

## Proposed execution order

1. Freeze and review final HC3.4 live evidence; capture cluster/template/storage/network constraints without modifying HC3.4 artifacts.
2. Approve plan, result, policy, error, and audit contracts.
3. Implement canonical plan compilation and fingerprint tests.
4. Implement durable VMID lease and concurrency tests.
5. Implement dry-run provider/factory gates and prove GET-only behavior.
6. Add preview service using HC3.4 discovery without claiming or advancing jobs.
7. Add persistence/audit fields and restart-recovery tests.
8. Adapt HC3.3 execution behind typed provider results while keeping fake behavior compatible.
9. Add stateful mocked mutation integration, failure injection, reconciliation, and rollback tests.
10. Run unchanged HC1–HC3.4 regressions.
11. Perform the controlled HC3.5 live read-only/dry-run acceptance and produce sanitized evidence.
12. Review results before planning any real mutation checkpoint.

## Files created or modified by this planning task

- Created: `docs/HELPER_COMPUTE_HC3_SESSION5_PLAN.md`
- Modified: none

HC3_5_PLAN_READY
