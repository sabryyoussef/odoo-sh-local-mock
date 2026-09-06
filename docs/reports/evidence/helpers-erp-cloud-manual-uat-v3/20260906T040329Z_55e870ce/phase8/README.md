# Phase 8 — Automated From-Scratch Verification

Run ID: 20260906T040329Z_55e870ce
Date: 2026-09-06T04:39Z

## Steps Executed
1. Exact reset of only user1–user4 UAT resources (no wildcard) — `seed_helpers_cloud_manual_uat --reset`
   - Verified: 0 tenants, 0 DBs, 0 containers, filestore removed, ports 8301-8304 free
2. Prepare accounts without tenants — `seed_helpers_cloud_manual_uat --prepare-manual`
   - 4 accounts queued, approved, allow-list [4,5,6,7], no tenants, portal login 200
3. Drive visible portal journey for all four (urllib)
   - GET /cloud/login 200, POST login 302→/cloud/instances, GET /cloud/pricing 200 (trial+enterprise), GET /cloud/setup 200, GET /cloud/instances 200
   - Verified no instances before provisioning, requests remain queued
4. Trigger provisioning through isolated worker (same UI/action Sabry will use)
   - `python -m app.manual_uat_worker_main --bounded 4` — isolated UAT control.db, UAT network, allow-list only
   - Observed: queued → provisioning → ready (bounded, heartbeat, 4/4)
5. Verify:
   - Package modules installed per DB (fail-closed): user1 sales 67, user2 trading 78, user3 operations 72, user4 full_erp 112 — all PASS
   - Company: User 1/2/3/4 Demo Company — PASS
   - Filestore: /data/tenants/helpers_demo_userN/filestore with sessions/filestore/addons — PASS
   - Isolation: each DB has only its user, res_company_users_rel cid=1 only — PASS
   - Duplicate prevention: allow-list empty after ready, process_one returns False — PASS
   - Open Odoo URL: http://100.76.217.35:8301-8304/web/login?db=helpers_demo_userN (Tailscale IP) and public_url http://master.tailcf9988.ts.net:8301-8304 — PASS
   - Odoo login: HTTP 200 on all ports, portal instances page shows Open Odoo link — PASS
   - Health: 8301-8304 200 — PASS

## Evidence Files
- 01_reset.log, 01_requests_after_reset.txt, 01_tenants_after_reset.txt, 01_containers_after_reset.txt, 01_filestore_after_reset.txt
- 02_prepare.log, 02_requests_after_prepare.txt, 02_tenants_after_prepare.txt, 02_allow_list.txt, 02_ports.txt, 02_portal_login.txt
- 03_portal_journey.log, 03_requests_before_provision.txt
- 04_before.txt, 04_allow_list_before.txt, 04_worker.log, 04_after_requests.txt, 04_after_tenants.txt, 04_after_containers.txt, 04_heartbeat.json, 04_health.txt
- 05_final_requests.txt, 05_final_tenants.txt, 05_final_containers.txt, 05_modules_verify.txt, 05_company_verify.txt, 05_filestore.txt, 05_isolation.txt, 05_urls.txt, 05_odoo_login_host.txt, 05_portal_instances_user1.txt, 05_duplicate.txt, 05_health.txt

## Result
PASS — Full automated cycle from zero tenants to 4 ready, with fail-closed guarantees.
