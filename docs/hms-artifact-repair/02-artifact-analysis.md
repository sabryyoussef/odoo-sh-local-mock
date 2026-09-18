# Part 2 — Current HMS Artifact Analysis

## Artifact Record (from DB)

| Field | Value |
|-------|-------|
| Solution Code | `hms` |
| Solution Name | Hospital Management System (HMS) |
| Artifact ID | 2 |
| Artifact Code | `hms-v1.0.0-demo-artifact` |
| Version | 1.0.0-demo |
| Status | published |
| Edition | community |
| Odoo Version | 19.0 |
| Source Type | `solution_vertical` |
| Package Identifier | `hms@1.0.0-demo` |
| is_verified | True |
| deployment_ready | True |
| verification_state | verified |

## Verification Evidence

The artifact was marked `verified` by `hc311-hms-artifact-verification-v1`. The verification performed:

1. Artifact exists
2. Odoo version matches (19.0)
3. Version is supported
4. Required modules declared (from `solution.required_modules` field)
5. Module technical names valid
6. Required dependencies present in declared module list
7. No forbidden dependencies
8. Artifact installable (status=published)
9. Edition valid (community)
10. License metadata present
11. Module allowlist compliance
12. Checksum generated (SHA256 fingerprint)

### Critical Observation

**The verification did NOT check the actual database, installed modules, or HMS application content.**

It only validated:
- The solution record's `required_modules` text field
- The artifact record's metadata (version, edition, status)

The "required_modules" for HMS in the Solution record are:
```
base, mail, contacts, account, stock, purchase
```

**These are generic Odoo modules — NOT the actual HMS modules** (`acs_hms_base`, `acs_hms`, `alzaeem_acs_hms_fix`).

## What "verified" Actually Proved

- The artifact record exists and has plausible metadata
- The declared module list contains valid-looking technical names
- The artifact status allows installation
- A deterministic fingerprint was generated from the declared metadata

## What "verified" Did NOT Prove

- ❌ HMS application modules (`acs_hms_base`, `acs_hms`, `alzaeem_acs_hms_fix`) are installed
- ❌ HMS models (`hms.patient`, `hms.physician`) exist
- ❌ HMS menus (Patient, Physician, etc.) are present
- ❌ The database was ever populated with HMS content
- ❌ The addons_path for the tenant contains HMS modules
- ❌ The HMS source modules (`/home/sabry/odoo_base/.../alzaeem/`) are reachable from the tenant container

## Artifact Source Gap

The artifact's `source_type` is `solution_vertical` and `install_strategy` is `restore`, but:

1. The `template_database_id` is NULL (no template DB binding)
2. The provisioning system uses `TemplateDatabase` records with `postgres_database_name`
3. The `ensure_demo_template_validated()` only initializes with `-i base` (base module only)
4. There is **no step** that installs `acs_hms_base`, `acs_hms`, or `alzaeem_acs_hms_fix`

## Conclusion

The artifact verification is a **metadata-only check**. It does not verify the actual deployable artifact contains HMS functionality. A "verified" status here only means "the database record looks plausible," NOT "HMS will work when deployed."
