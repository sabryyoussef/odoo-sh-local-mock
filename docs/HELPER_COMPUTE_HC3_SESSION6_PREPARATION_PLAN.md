# HC3.6 local preparation — execution record

The operator explicitly approved the frozen bounded plan below, and execution
completed successfully at 2026-09-13T12:16:05.433106+00:00: **`HC3_6_LOCAL_SCHEMA_PREPARED`**.
Approved hashes and the no-write precheck passed; exclusive preparation and
independent read-only verification passed. Seven empty tables, exact DDL and
indexes, integrity, permissions and zero state verified. No forbidden action
occurred. See the [sanitized verification evidence](HELPER_COMPUTE_HC3_SESSION6_LOCAL_SCHEMA_EVIDENCE.json)
and [current freeze disposition](HELPER_COMPUTE_HC3_SESSION6_FREEZE.md).

The target now exists. Do not rerun `apply-approved`, recreate it, or overwrite
it. The `verify` command is the read-only inspection command. The plan text below
is preserved verbatim as the approved specification; its pending-approval and
not-yet-executed wording describes the earlier preparation checkpoint.

---

# HC3.6 bounded local preparation — pending separate approval

The operator approved the identity only: app `hc3-6-lab`, `APP_ENV=test`, tenant
`hc3-6-test-tenant`, customer `hc3-6-test-customer`, database
`/opt/projects/active/odoo-sh-local-mock/data-hc36/control.db`.
That approval does not authorize execution of the preparation below.

## Exact scope proposed for approval

1. Verify the reviewed bundle hashes, unchanged model source, clean process
   identity and absence of the exact target directory.
2. Create only repository `data-hc36/` (0700) and `data-hc36/control.db` (0600),
   with SQLite's transient rollback journal confined to that new directory.
3. Create exactly seven empty tables and their reviewed constraints/indexes:
   `proxmox_reservations`, `proxmox_provisioning_jobs`, `proxmox_vmid_leases`,
   `proxmox_provisioning_audit_events`, `proxmox_clone_approvals`,
   `proxmox_clone_intents`, `proxmox_mutation_controls`.
4. Close the writer and reopen the dedicated DB with `mode=ro` and
   `query_only=ON`; check integrity, exact table/column/index sets, no views or
   triggers, foreign-key consistency and zero rows in every table. Report the
   result for the freeze record without changing any running service.

This is a fresh HC control-schema bootstrap, not an existing-database migration
or a full application installation. Tenant/customer remain designated labels;
no tenant/customer rows or other seed data are inserted. Mutation controls stay
empty: no slot is bootstrapped or armed. It does not prepare an executable clone
job or establish real-transport readiness.

## Reviewed artifacts and provenance

- [prepare.py](hc36-preparation/prepare.py): standard-library-only command runner.
- [schema.sql](hc36-preparation/schema.sql): SQLite DDL rendered from the current
  seven Proxmox models, including current HC3.5/HC3.6 fields.
- [manifest.json](hc36-preparation/manifest.json): identity, model/schema hashes,
  expected columns and indexes; documentation metadata only, not app configuration.

DDL was rendered without an engine or DB connection, with `app.db` replaced by a
metadata-only Base for the rendering process. No application settings, `.env`,
startup, migration, seeding, provider, allocator or worker module was invoked.
Future application compatibility is limited to these seven control tables; a
full API deployment would require a separate plan and approval.

| Artifact | SHA-256 |
| --- | --- |
| `prepare.py` | `7e478ca88088ad057ba6c31d075b4fa8d08e07b74bfcac1fdfaf7b8b51dd96c5` |
| `schema.sql` | `b5ecbfe5db49930fd536464cabe9d53aa80d1f82bf22b7511cce16289d0b9312` |
| `manifest.json` | `c3c0efff05fcfea943896fe21be6523e93e6425c038661b1fa645a91c9ed2a8c` |

Model source SHA-256: `08a39dad5c26d56ad2bc48dc612642bf3e853f0a6fa6a01f84db7d082bd06816`. A changed model or schema stops preparation
for renewed review. Before approved execution, compare all three artifact hashes
with this recorded bundle; do not execute a modified bundle under this approval.

## Exact commands

Run as the current workspace owner, without sudo, Docker, app startup or service
commands. Every invocation uses an empty inherited environment, explicit identity,
isolated Python (`-I`) and no bytecode (`-B`). No `.env` or credential is loaded.
The three process-only environment values do not persist or change existing
runtime configuration. The standard-library runner does not import the app or
open any network connection.

First, review hashes and run the non-mutating precheck:

```bash
sha256sum /opt/projects/active/odoo-sh-local-mock/docs/hc36-preparation/prepare.py /opt/projects/active/odoo-sh-local-mock/docs/hc36-preparation/schema.sql /opt/projects/active/odoo-sh-local-mock/docs/hc36-preparation/manifest.json
env -i PATH=/usr/bin:/bin \
  APP_NAME=hc3-6-lab APP_ENV=test \
  DATABASE_URL=sqlite:////opt/projects/active/odoo-sh-local-mock/data-hc36/control.db \
  /usr/bin/python3 -I -B \
  /opt/projects/active/odoo-sh-local-mock/docs/hc36-preparation/prepare.py check-plan
```

**Only after separate explicit approval of this bounded preparation scope**, run:

```bash
env -i PATH=/usr/bin:/bin \
  APP_NAME=hc3-6-lab APP_ENV=test \
  DATABASE_URL=sqlite:////opt/projects/active/odoo-sh-local-mock/data-hc36/control.db \
  /usr/bin/python3 -I -B \
  /opt/projects/active/odoo-sh-local-mock/docs/hc36-preparation/prepare.py apply-approved
```

The apply command includes read-only post-verification. An independent repeat of
verification is:

```bash
env -i PATH=/usr/bin:/bin \
  APP_NAME=hc3-6-lab APP_ENV=test \
  DATABASE_URL=sqlite:////opt/projects/active/odoo-sh-local-mock/data-hc36/control.db \
  /usr/bin/python3 -I -B \
  /opt/projects/active/odoo-sh-local-mock/docs/hc36-preparation/prepare.py verify
```

Success marker:
`HC3_6_LOCAL_SCHEMA_PREPARED: seven tables, all empty; no execution readiness claimed`.
Stop after verification and report the evidence; do not proceed to provisioning.

## Abort and preservation rules

An existing directory, DB, symlink, altered model/schema, wrong process identity,
or verification failure stops the operation. Creation is exclusive; no overwrite
or automatic reuse is permitted. Schema DDL runs in one explicit transaction.
If preparation fails after exclusive creation, preserve the new directory/DB for
inspection; no automatic deletion, cleanup, retry, migration or repair is included.
Do not point the commands at shared `data/control.db` or pytest artifacts.

No Proxmox request, root/auditor/mutation token loading, ACL/user/role/token change,
VMID allocation, lease/job/reservation/approval insertion, app/worker launch,
configuration-file change, database copy, network/storage modification, clone,
start, stop or delete is part of this approval request. Clone and start remain
prohibited. The network privilege and authoritative cluster pin remain freeze
metadata and are not configured by this plan.

## Validation already performed; no preparation executed

Python syntax and DDL static checks passed; the schema contains seven CREATE TABLE
statements and no INSERT/ALTER/DROP. Metadata generation made no DB connection.
The `check-plan` mode passed, confirming reviewed hashes and an absent target
without opening or creating a database. Apply and verify-on-target modes have
not been executed; their runtime results remain pending approval and execution.
No additional test database was created to validate this plan.

## Single next operator decision

Approve or reject execution of this exact bounded local schema preparation and
read-only verification. Identity designation is already approved. This approval
would permit only the local directory/DB/schema writes listed above, not clone,
start, a live mutation, job/lease creation or any configuration change.
