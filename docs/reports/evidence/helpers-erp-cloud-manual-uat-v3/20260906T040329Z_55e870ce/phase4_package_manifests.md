# Phase 4 Package Manifests — Fail-Closed Verification

## Manifests (Odoo 19 Community)
{
  "full_erp": [
    "contacts",
    "crm",
    "sale_management",
    "purchase",
    "stock",
    "account",
    "hr",
    "project",
    "maintenance",
    "mrp"
  ],
  "operations": [
    "purchase",
    "stock",
    "maintenance",
    "hr",
    "mrp",
    "account"
  ],
  "sales": [
    "contacts",
    "crm",
    "sale_management",
    "account"
  ],
  "trading": [
    "contacts",
    "crm",
    "sale_management",
    "purchase",
    "stock",
    "account"
  ]
}
## Verification
- All 4 packages distinct: PASS
- sales vs trading distinct (stock,purchase diff): PASS
- operations vs others distinct: PASS
- full_erp vs others distinct: PASS
- All modules exist in odoo:19.0 image (685 modules): PASS
- sales: contacts,crm,sale_management,account — all available
- trading: +purchase,stock — all available
- operations: purchase,stock,maintenance,hr,mrp,account — all available
- full_erp: 10 modules — all available

## Fail-Closed Requirements
- Validate all requested modules exist before resource creation: YES (check_output ls + missing check)
- Install required modules deterministically: YES (odoo -i <mods> --stop-after-init)
- Treat required-module failure as provisioning failure: YES (now fail-closed, not best-effort)
- Never mark ready until verification confirms every required module state is installed: YES (verify after install)
- Record dependency modules separately: YES (to_install vs installed)
- Do not require every installed module to differ: YES (compare required business modules)
- Roll back tenant if required installation fails: YES (rollback_manual_uat_request)
- Do not silently remap unavailable module: YES (explicit ValueError)
- Clearly label user4 as Full ERP Community: YES (full_erp)
- Make module installation idempotent: YES (check already installed, skip)

## Fix Applied
- Fixed bytes.wait bug: detach=False returns bytes, not Container — changed to detach=True
- Added bounded timeout 600s, redacted logs, verification of installed state
- Changed best-effort to fail-closed: exception now propagates and triggers rollback
