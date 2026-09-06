# Phase 6 True Manual Customer Journey — Prepared State

## Prepared Accounts (no tenants)
- user1: plan=trial, package=sales, company=User 1 Demo Company, subdomain=user1, db=helpers_demo_user1, request=4 queued, approved=True
- user2: plan=starter, package=trading, company=User 2 Demo Company, subdomain=user2, db=helpers_demo_user2, request=5 queued, approved=True
- user3: plan=business, package=operations, company=User 3 Demo Company, subdomain=user3, db=helpers_demo_user3, request=6 queued, approved=True
- user4: plan=enterprise, package=full_erp, company=User 4 Demo Company, subdomain=user4, db=helpers_demo_user4, request=7 queued, approved=True (quote=True)

## Verification
- Portal accounts active: YES (user1-4 exist, password 123 hashed)
- Correct plan assigned: YES (trial, starter, business, enterprise)
- Correct package selected: YES (sales, trading, operations, full_erp)
- Company details prepared: YES
- No tenant: YES (0 tenants for helpers_demo)
- No PostgreSQL database: YES (0 helpers_demo DBs)
- No Odoo container: YES (0 mosh-tenant-manual containers)
- No filestore: YES (tenants dir empty)
- No ready provisioning request: YES (all queued, not ready)
- Ports 8301-8304 free: YES
- Portal login page: OK (200, csrf_token present)

## Expected Journey
1. Login (userN/123) -> 2. Pricing -> 3. Confirm plan -> 4. Confirm billing cycle -> 5. Odoo 19 Community -> 6. Select assigned package -> 7. Confirm company/users/storage/backup -> 8. Review -> 9. Simulated UAT checkout -> 10. Explicit Create/Provision click -> 11. Request becomes queued -> 12. Dedicated UAT worker processes it -> 13. Page shows provisioning -> 14. Page becomes ready -> 15. Open Odoo

## Prevented
- Duplicate requests from repeated clicks: YES (idempotency_key manual-uat:userN:plan:package)
- Duplicate databases/containers: YES (exact DB name check, container collision check)
- User changing another user's request: YES (get_owned_request checks user_id)
- UAT bypass in production: YES (HELPERS_CLOUD_MANUAL_UAT_ENABLED + local env gate)
- user1 selecting user4-only approval path: YES (quote_approved only for user4 enterprise)
- Real payment: YES (simulated checkout, no payment)

## Commands
- Reset: docker exec p3-uat-control-api python -m app.scripts.seed_helpers_cloud_manual_uat --reset
- Prepare: docker exec p3-uat-control-api python -m app.scripts.seed_helpers_cloud_manual_uat --prepare-manual
- Status: docker exec p3-uat-control-api python -m app.scripts.seed_helpers_cloud_manual_uat --status
- Provision: docker exec p3-uat-control-api python -m app.scripts.seed_helpers_cloud_manual_uat --provision-all
