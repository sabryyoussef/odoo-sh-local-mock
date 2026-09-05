# P3 Phase 5 — Drift Comparison (Live vs Isolated)

**Run ID:** `p3_20260905T071700Z_d1938642`
**Evidence Dir:** `p3_20260905T035704Z_3866e85e`
**Date (UTC):** `2026-09-05T07:29:16Z`

## Live control.db Hash Drift

| Time | Hash | Integrity | Notes |
|------|------|-----------|-------|
| 2026-09-05T07:17:00Z (pre-canary) | `9d049d433dc0562c86524dc1a78255978d086dfe443446300c75bc400bc951f9` | ok | Baseline before isolated DB copy |
| 2026-09-05T07:29:16Z (after canary) | `28a9ccd69b9d86f34406e79446047936330deaede432662deeddca8eccd0ee49` | ok | After canary + cleanup |

**Drift:** Hash changed, but **not due to canary**.

**Investigation:**
- Live DB `tenants.updated_at` changed to `2026-09-05 07:23:05` for multiple tenants (metering update by backup worker, not canary).
- Live DB `audit_events` count 548 vs isolated 550 (isolated has extra canary audit), `users` 10 vs 11 (isolated has canary user), `cloud_provisioning_requests` 3 vs 5 (isolated has canary requests 4,5).
- Live requests 1-3 unchanged: `1 queued`, `2 queued`, `3 rolled_back` — PASS.
- No canary tenant, DB, role, container, filestore, or port leaked to live — PASS.
- Isolated DB hash `ceff22105801a2d824a40313c5aef0b97dbd4b7a0985d9fc79e916aef9d5a514` reflects canary requests 4,5 and cleanup (rolled_back).

**Conclusion:** Drift is **known unrelated API write** (backup worker metering), not unexplained canary mutation. Live DB integrity ok, requests 1-3 preserved, no leaked P3 resources. Drift is **acceptable** and documented.

## Resource Drift

| Resource | Before | After | Drift | Verdict |
|----------|--------|-------|-------|---------|
| Live tenants | 26 | 26 | 0 | PASS |
| Live tenant dirs | 256 | 256 | 0 | PASS |
| Live containers (mosh-tenant) | 15 | 15 | 0 | PASS |
| Live PG DBs (mosh_tnt_p2_p3) | 0 | 0 | 0 | PASS |
| Live PG roles (mosh_r_p2_p3) | 0 | 0 | 0 | PASS |
| Live ports 8301-8302 | 0 | 0 | 0 | PASS |
| Live filestores .p2/.p3 | 0 | 0 | 0 | PASS |
| Isolated tenants | 26 | 26 (after cleanup) | 0 (was 27 before cleanup) | PASS |
| Isolated requests | 3 | 5 (4,5 rolled_back) | +2 canary (expected) | PASS |

## Main/P3 Drift

| Check | Before | After | Verdict |
|-------|--------|-------|---------|
| Main HEAD | 73e75b9 | 73e75b9 | PASS |
| Main dirt | 52 branding/i18n | 52 branding/i18n | PASS |
| P3 HEAD | ee2964d (+8b57d98) | 8b57d98 | PASS (only test commit) |
| P3 status | untracked evidence | untracked evidence | PASS |
| Workers | exited | exited | PASS |
| Manual-UAT patch | 70fb0f... | 70fb0f... | PASS |

## Decision
**No unexplained drift.** All changes are either expected (isolated canary requests) or known unrelated (backup metering). Live DB unchanged for canary-relevant state.
