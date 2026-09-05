# P3 Phase 5 — Eligibility Matrix (Isolated Canary)

**Run ID:** `p3_20260905T071700Z_d1938642`
**Isolated DB:** `/tmp/p3-canary-p3_20260905T071700Z_d1938642/control.db`
**Date (UTC):** `2026-09-05T07:23:00Z`

## Method
Evaluated every `cloud_provisioning_requests` row via `cloud_request_eligibility_reasons` + `is_cloud_request_approved_and_unchanged` + `status == "queued"` check. No resources created.

## Results

| ID | Status | Adapter | Template | Plan | Approved | Eligible | Reasons |
|----|--------|---------|----------|------|----------|----------|---------|
| 1 | queued | demo | None | trial (demo) | false | false | adapter_not_real, template_missing, subscription_ineligible |
| 2 | queued | demo | None | trial (demo) | false | false | adapter_not_real, template_missing, subscription_ineligible, plan_is_demo |
| 3 | rolled_back | local_docker | 1 (mosh_tpl_cloud_base_19_0_trading) | business | true | false | (not queued — rolled_back, never requeue) |
| 4 | rolled_back | local_docker | 1 | business | true | false | (rolled_back after first worker with wrong password, provision_failed) |
| 5 | queued → ready | local_docker | 1 (mosh_tpl_cloud_base_19_0_trading) | business | true | true | (eligible, Community edition, disposable, authorized) |

## Dry Claim Check
- Before queued IDs: [1, 2, 4] (at dry_claim time, before canary2)
- Eligible IDs: [4] (exactly one)
- Dry claim with worker `p3-dry-worker-p3_20260905T071700Z_d1938642` claimed_id=4, status provisioning, claimed_by correct, lease 2026-09-05 07:28:35.623773+00:00
- Reverted dry claim for id=4, after queued IDs [1,2,4] == before — PASS
- No containers, no PG DBs/roles, no filestore, no ports created — PASS

## After Canary2 Creation
- New request 5 (id 5, uuid 4e7b2a081d4459058b138ddf01b2492d, subdomain p3-canary2-132f26, order CLO-852D1387, sub CLS-34DFBD88) eligible true
- Requests 1,2 ineligible, 3 rolled_back not reused, 4 rolled_back not reused, exactly one eligible (5) — PASS
- Live DB unchanged (requests 1-3 still queued/queued/rolled_back) — PASS

## Conclusion
Eligibility gate is fail-closed, correctly excludes demo, unapproved, rolled_back, and only allows the new canary. Dry claim proves atomic claim and revert without resource creation.
