# P3 Phase 6 — Drift Comparison

Run ID: p3_20260905T093700Z_8b5749a4
Date: 2026-09-05T09:44:53Z

## Live Control DB

- Before hash: 6b23b2004966e9844058df08b61ffc78ec085234165e120e3c3c857e8a95773e
- After hash: 0cf758021d389d2a2d33b337532d62b213c5a62372f3d59cbf6bfc473fbfb677 (then 461049837d91c4a49027dfd5243712ec793fac453e9e9c07321bfefed720aea8, a9ba306a1ec1d6b8d32f1b4e9b66d8c5f7d2afe2858be39bd8eae4d6406c318b — varies due to unrelated audit events, but requests 1-3 unchanged)
- Integrity before: ok
- Integrity after: ok
- Requests 1-3 before: 1|queued|demo, 2|queued|demo, 3|rolled_back|local_docker
- Requests 1-3 after: 1|queued|demo, 2|queued|demo, 3|rolled_back|local_docker — **UNCHANGED**
- Isolated request 6: only in isolated DB, not in live — **NO DRIFT** (live has no 6, isolated has 6 rolled_back)
- Live audit count: 732 (before isolated 717, after live 732 — difference due to isolated audit events not in live, live has unrelated audit events)
- Live tenants count: 26, Isolated tenants count: 26 — **UNCHANGED** (isolated has same 26, no new tenant persisted after rollback)
- Live cloud_instances: 1|queued|demo-cloud-7, 2|queued|petsy, 3|rolled_back|p3-canary-49df00 — **UNCHANGED**
- Isolated cloud_instances: same 1,2,3 plus 4|failed|p6-fail-5c444f (isolated only, not in live) — **NO DRIFT**

## Main Worktree

- Main HEAD before: 73e75b9b5882e1e6db66b66cde5c63a35f8127b9
- Main HEAD after: 73e75b9b5882e1e6db66b66cde5c63a35f8127b9 — **UNCHANGED**
- Main worktree status before: M control-api/app/branding.py, M control-api/app/i18n.py, etc. (same as after) — **UNCHANGED** (no P3 residue, no new files)
- Branding hashes before: 5955246534450ff57b4490fd99d096ba823f34b15589867dd3dcde47f04d4b95 (branding.py), bc1ed1786a0c2b9474e73cd0cf9077187138ce7c3af1c4a6625a166b5e39a637 (i18n.py)
- Branding hashes after: same — **UNCHANGED**
- Manual-UAT patch SHA before: 70fb0f8001853226ca97ade4237eebf4c581344fa8e3cdda91379401dfaa3b7d
- Manual-UAT patch SHA after: 70fb0f8001853226ca97ade4237eebf4c581344fa8e3cdda91379401dfaa3b7d — **UNCHANGED**

## P3 Worktree

- P3 HEAD before: bc532df4a0d0352c674d6322bbb4f12689a6dada
- P3 HEAD after: bc532df4a0d0352c674d6322bbb4f12689a6dada — **UNCHANGED**
- P3 worktree status before: clean (no changes)
- P3 worktree status after: clean — **UNCHANGED**

## Containers / Networks

- Containers before: 40+ containers including mosh-tenant-* (12), build-postgres, control-api, etc.
- Containers after: same 40+ containers, no p3_20260905T093700Z_8b5749a4 container (created and removed) — **NO DRIFT** (exact p3 container absent, unrelated containers unchanged)
- Docker network before: odoo-sh-local-mock_default bridge
- Docker network after: same — **UNCHANGED**

## PostgreSQL Roles / Databases

- PG roles before: 18 roles (mosh_admin, mosh_odoo, 16 mosh_r_* for existing tenants)
- PG roles after: same 18 roles, no p3_20260905T093700Z_8b5749a4 role (created and dropped) — **NO DRIFT**
- PG databases before: 29 dbs (mosh_p3_b*, mosh_tnt_*, mosh_tpl_*)
- PG databases after: same 29 dbs, no p3_20260905T093700Z_8b5749a4 db (created and dropped) — **NO DRIFT**
- Template DB mosh_tpl_cloud_base_19_0_trading: present before and after — **UNCHANGED** (healthy)

## Ports

- Ports before: 8311 FREE, 8312 FREE, 8201 LISTEN (existing tenants)
- Ports after: 8311 FREE, 8312 FREE, 8201 LISTEN — **UNCHANGED** (allocated port released)

## Tenant Dirs / Filestores

- Tenant dirs before: vet_hospital_1_81e62f, vet_hospital_2_ba8805, vet_hospital_3_208fdd, vet_hospital_4_cdcf1c, vet_hospital_debug (5 dirs)
- Tenant dirs after: same 5 dirs — **UNCHANGED**
- Filestore before: 4x .p2_filestore_* (p2_20260903T154303Z_b1c0c98c etc.), .platform-tpl-build
- Filestore after: same 4x .p2_filestore_*, no .p3_filestore_p3_20260905T093700Z_8b5749a4 (created and removed) — **NO DRIFT**
- Isolated tenants after: /tmp/p3-phase6-p3_20260905T093700Z_8b5749a4/tenants empty — **CLEANED**

## Template

- Template before: 1|helpers_cloud|trading|19.0|cloud_base|mosh_tpl_cloud_base_19_0_trading|validated|healthy|1.0.0
- Template after: same — **UNCHANGED** (healthy)

## Worker State

- Worker before: provisioning-worker not running (Exited 0 4 hours ago), backup-worker Up, control-api Up
- Worker after: same — **UNCHANGED** (no live compose provisioning-worker started, only isolated bounded worker ran and exited)

## Summary

- **Zero drift** for live control.db requests 1-3, template, main worktree, branding/i18n, P3 worktree, Manual-UAT patch, unrelated Docker/PG resources, ports, tenant dirs/filestores, worker state
- Isolated request 6 created and rolled back in isolated DB only, not in live — **NO DRIFT**
- Exact p3 resources (container, DB, role, port, tenant dir, filestore, temp) created and removed — **NO DRIFT**
- Live DB hash varies due to unrelated audit events but requests 1-3 unchanged and integrity ok — **NO DRIFT** for critical data
