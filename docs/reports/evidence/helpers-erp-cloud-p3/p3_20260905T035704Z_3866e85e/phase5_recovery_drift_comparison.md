# Phase 5 recovery — drift comparison

Compared against:

- Phase 5 preflight inventory (`containers_before.txt`, `pg_databases_before.txt`, `pg_roles_before.txt`, `queued_requests_before.json`, `preflight_manifest.json`)
- Pre-canary DB backup `data/control.db.backup.20260905T035704Z_p3_preflight`

| Surface | Pre-canary / Phase 5 preflight | After recovery | Drift |
| --- | --- | --- | --- |
| Main HEAD | `73e75b9` | `73e75b9` | none |
| P3 HEAD | `52e0d44` | `52e0d44` | none (uncommitted adapter/`_redacted`/tests remain) |
| P3 merged to main | no | no | none |
| provisioning-worker | Up (since Sep 2) | **exited/stopped** `05:53:30Z` | intended stop; keep stopped |
| mosh-tenant containers | 15 Up + debug Exited | same 16 names | none |
| PG `mosh_*` DBs | 29 | 29 identical list | none |
| PG `mosh_*` roles | 18 | 18 identical list | none |
| Template `mosh_tpl_cloud_base_19_0_trading` | present | present | none |
| Port 8216 | absent at original preflight; briefly used during canary | absent | canary cleaned |
| Requests 1, 2 | queued demo, timestamps 2026-09-02 / 2026-09-03 | identical | **preserved** |
| Request 3 | did not exist | `rolled_back` local_docker, retained | leftover canary **control row** (intentional) |
| Users 9 and 10 | absent | present (canary owner + operator) | leftover identity rows |
| Order 3 / subscription 3 / instance 3 | absent | present (paid/active/rolled_back) | leftover commercial/audit rows; exclusive to this canary; **not deleted** |
| `cloud_subscriptions` columns | missing 4 lifecycle timestamps | 4 nullable DATETIME added | **retained**; matches committed models |
| Canary container/DB/role/filestore/dir | absent | absent | cleaned |
| Main P3 leaked files | absent pre-canary | **restored to HEAD / untracked file removed** | leakage cleared |
| Branding/i18n dirt | present | present (same set minus the 4 leaked P3 paths) | preserved |
| Isolated `p3-helpers-erp-cloud-p3-build-postgres-1` | absent at original 03:57 preflight | Up ~2h | leftover P3 worktree compose Postgres; not the canary; not dropped |

## Safe to leave

- Request 3 rolled_back audit graph
- Additive subscription timestamp columns
- Stopped provisioning-worker
- P3 worktree uncommitted adapter tests/fixes
- Isolated P3 compose Postgres (not a live provisioning consumer)

## Not done (out of scope / forbidden this job)

- Canary retry
- Phase 6
- Merge
- Worker start
- DROP COLUMN
- Full control.db restore
- Deletion of order/subscription/customer unless exclusive **and** required (they are exclusive but retained as audit)
