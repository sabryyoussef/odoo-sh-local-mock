# P3 Phase 5 — Eligibility Matrix

Run ID: p3_20260905T054700Z_9340f78e
Date (UTC): 2026-09-05T05:48:54Z
P3 HEAD: 52e0d44d0676b1e351129053a4784bf4eb51a2a7

## Queued Requests

| ID | Adapter | Product Line | Template | Status | Eligible | Reasons | Approved |
|----|---------|--------------|----------|--------|----------|---------|----------|
| 1 | demo | helpers_cloud | null | queued | false | adapter_not_real, template_missing, subscription_ineligible | false |
| 2 | demo | helpers_cloud | null | queued | false | adapter_not_real, template_missing, subscription_ineligible, plan_is_demo | false |
| 3 | local_docker | helpers_cloud | 1 (mosh_tpl_cloud_base_19_0_trading) | queued | true | (none) | true |

## Dry Claim Result

- Before IDs: [1, 2, 3]
- After IDs: [1, 2, 3]
- Claimed ID (dry, reverted): 3
- Before count: 3
- After count: 3
- No resources created: true (containers, DBs, roles, filestores, ports unchanged)
- Only canary (id 3) eligible
- Demo rows 1,2 ineligible (adapter_not_real, template_missing)
- No other row can be claimed

## Fail-Closed Proof

- helpers_cloud_real_provisioning_enabled=false by default (fail-closed)
- Dry check does not create runtime resources
- Only persistently approved, validated cloud_base template, active subscription, Community edition
- Demo adapter never eligible for real provisioning
