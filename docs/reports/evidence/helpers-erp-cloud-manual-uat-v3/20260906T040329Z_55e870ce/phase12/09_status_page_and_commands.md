# Phase 12 — Status Page & Commands for Sabry

## Portal
- Login: http://100.76.217.35:8001/cloud/login (also http://master.tailcf9988.ts.net:8001/cloud/login, http://192.168.100.66:8001/cloud/login — never localhost)
- Accounts: user1/123 (sales/trial), user2/123 (trading/starter), user3/123 (operations/business), user4/123 (full_erp/enterprise)
- Each account has a draft setup (no subscription/request/tenant yet). User clicks Create/Configure to queue provisioning.

## Status Commands (run on master host)
```bash
# UAT status (4 accounts, 0 tenants)
docker exec p3-uat-control-api bash -c "cd /app && python -m app.scripts.seed_helpers_cloud_manual_uat --status"

# DB counts
sqlite3 /tmp/p3-helpers-erp-cloud-windows-uat/data-uat/control.db "SELECT count(*) FROM users WHERE email IN ('user1@demo.local','user2@demo.local','user3@demo.local','user4@demo.local'); SELECT count(*) FROM tenants WHERE database_name IN ('helpers_demo_user1','helpers_demo_user2','helpers_demo_user3','helpers_demo_user4');"

# Portal health
curl -s http://100.76.217.35:8001/health | jq .
curl -s http://100.76.217.35:8001/cloud/login | grep -c "Sign in"

# Ports 8301-8304 (should be FREE until provisioning)
for p in 8301 8302 8303 8304; do ss -tlnp | grep -q ":$p " && echo "port $p IN USE" || echo "port $p FREE"; done

# Live worker must stay Exited
docker ps -a --format '{{.Names}} {{.Status}}' | grep provisioning-worker

# UAT compose
docker ps --format '{{.Names}} {{.Status}}' | grep p3-uat
```

## Isolated Worker (only for manual UAT, allow-listed)
```bash
# Start isolated worker (max 4, allow-list user1-4/requests 4-7)
docker exec p3-uat-control-api bash -c "cd /app && python -m app.manual_uat_worker_main --max-success 4"

# Or host-side (if needed):
# docker compose -f docker-compose.uat.yml exec uat-control-api python -m app.manual_uat_worker_main --max-success 4
```

## Expected Odoo URLs after provisioning
- Internal: http://127.0.0.1:8301/web/login?db=helpers_demo_user1 (etc 8302-8304)
- Public (Windows): http://master.tailcf9988.ts.net:8301/web/login?db=helpers_demo_user1 (Tailscale) or http://100.76.217.35:8301/web/login?db=helpers_demo_user1
- UI uses HELPERS_CLOUD_EXTERNAL_HOST=100.76.217.35 via cloud_external_url.py (never localhost)

## Reset (exact ownership only)
```bash
docker exec p3-uat-control-api bash -c "cd /app && python -m app.scripts.seed_helpers_cloud_manual_uat --reset --dry-run"
docker exec p3-uat-control-api bash -c "cd /app && python -m app.scripts.seed_helpers_cloud_manual_uat --reset"
docker exec p3-uat-control-api bash -c "cd /app && python -m app.scripts.seed_helpers_cloud_manual_uat --prepare-manual-accounts-only"
```

