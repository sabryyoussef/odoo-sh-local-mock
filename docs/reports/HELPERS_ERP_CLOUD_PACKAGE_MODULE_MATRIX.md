# Helpers ERP Cloud — Package Module Matrix (Community 19.0, UAT V2)

> Generated: 2026-09-05T18:45Z | Run: 20260905T173139Z_78fe47d1 | UAT compose: p3-helpers-erp-cloud-windows-uat

## Summary

| User | Portal | Plan | Package | DB | Port | Modules | Distinct |
|------|--------|------|---------|----|------|---------|----------|
| user1 | user1 / 123 | Trial | sales | helpers_demo_user1 | 8301 | 67 | sales-only |
| user2 | user2 / 123 | Starter | trading | helpers_demo_user2 | 8302 | 78 | +purchase/stock |
| user3 | user3 / 123 | Business | operations | helpers_demo_user3 | 8303 | 72 | +hr/mrp/maintenance |
| user4 | user4 / 123 | Enterprise | full_erp | helpers_demo_user4 | 8304 | 112 | +project + all |

All 4 sets are distinct (verified via `SELECT name FROM ir_module_module WHERE state='installed'`).

## Expected Package Modules (validated against odoo:19.0 image)

| Package | Code | Standard Modules (Community) | Helpers Modules |
|---------|------|------------------------------|-----------------|
| Sales | sales | contacts, crm, sale_management, account | (none) |
| Trading | trading | contacts, crm, sale_management, purchase, stock, account | (none) |
| Operations | operations | purchase, stock, maintenance, hr, mrp, account | (none) |
| Full ERP | full_erp | contacts, crm, sale_management, purchase, stock, account, hr, project, maintenance, mrp | (none) |

> Invalid modules removed: `accountant` → `account`, `stock_barcode` → `stock`, `helpdesk` → `project` (not in Community 19.0 image). Validated via `docker run --rm odoo:19.0 ls /usr/lib/python3/dist-packages/odoo/addons`.

## Installed Module Counts (post-rebuild, 2026-09-05)

- user1 (sales): 67 — base + contacts/crm/sale/account
- user2 (trading): 78 — user1 + purchase/stock (+11)
- user3 (operations): 72 — purchase/stock/hr/mrp/maintenance/account (no crm/sale)
- user4 (full_erp): 112 — all above + project (+40 vs user3, +45 vs user1)

## Pairwise Differences

- user1 vs user2: only in 1=0, only in 2=11 (purchase, stock, etc.)
- user1 vs user3: only in 1=15 (crm/sale), only in 3=20 (hr/mrp/maintenance)
- user1 vs user4: only in 1=0, only in 4=45 (project, hr, mrp, etc.)
- user2 vs user3: only in 2=18, only in 3=12
- user2 vs user4: only in 2=0, only in 4=34
- user3 vs user4: only in 3=0, only in 4=40

> user4 is superset of all others (expected for Full ERP).

## Verification Commands

```bash
for n in 1 2 3 4; do
  docker exec p3-uat-build-postgres psql -U mosh_admin -d helpers_demo_user$n -c "SELECT name FROM ir_module_module WHERE state='installed' ORDER BY name;"
done
```

Evidence: `docs/reports/evidence/helpers-erp-cloud-manual-uat-v2/20260905T173139Z_78fe47d1/matrix/mods_user*.txt`
