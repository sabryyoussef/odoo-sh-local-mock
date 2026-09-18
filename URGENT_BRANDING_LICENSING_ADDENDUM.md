# URGENT — Catalog branding, licensing, and feature-accuracy addendum

Apply immediately to `/catalog` redesign. Supersedes HMS marketing claims that conflict with this document.

Reference reviewed: https://apps.odoo.com/apps/modules/19.0/acs_hms

## Public product identity

### English
- Primary: `Helpers HMS`
- Full: `Helpers Hospital Management System`
- Positioning: `Hospital and clinic management, hosted and managed by Helpers ERP`

### Arabic
- Primary: `نظام Helpers لإدارة المستشفيات`
- Short: `Helpers HMS`
- Positioning: `نظام لإدارة المستشفيات والعيادات، مستضاف ومدار بواسطة Helpers ERP`

Use Helpers HMS consistently in catalog title, hero, cards, alt text, packages, docs headings, metadata, breadcrumbs, CTAs, feature sections.

## Forbidden public wording
Never show in public catalog HTML:
- AlmightyCS, Almighty Consulting Solutions, HMS by AlmightyCS, ACS HMS
- Vendor titles like `Base - Hospital Management System (HMS by AlmightyCS)`
- Vendor URLs, logos, support, contact
- False claim `Developed by Helpers ERP`

Use truthful:
- `مقدم ومهيأ ومدار بواسطة Helpers ERP`
- `Hosted and managed by Helpers ERP`
- `Configured for your organization by Helpers ERP`
- `Integrated with the Helpers ERP platform`

## Licensing boundary
OPL-1 module. Do NOT modify licensed source attribution, copyright, license headers, directory names (`acs_hms`), imports, models, XML IDs, deps, manifests author fields.

Rebranding is presentation-layer only. Do not copy vendor Odoo Apps marketing, screenshots, icons, or wording.

Screenshots: from our own licensed configured instance only; demo data; no vendor logo; WebP.

## Public technical labels
Never expose publicly: `acs_hms`, `acs_hms_base`, `alzaeem_acs_hms_fix`, original manifest titles/authors, dependency trees.

| Internal | Public EN | Public AR |
|---|---|---|
| acs_hms_base | Hospital Core | النواة الطبية |
| acs_hms | Clinical Operations | العمليات السريرية |
| overlay | Odoo 19 Compatibility Layer | طبقة التوافق مع Odoo 19 |

## Feature accuracy
Inventory states: `verified_in_installed_solution` | `installed_but_not_functionally_verified` | `not_installed_or_addon`

Only verified may be confirmed capabilities. Second = under verification. Third = do not advertise.

Do NOT advertise unless installed+verified: hospitalization, wards, beds, insurance, vaccination, patient portal, online booking, queue screen, webcam, advanced vitals, dashboards, WhatsApp, mobile app, lab/radiology/surgery/nursing/ambulance extensions.

Validate current claims (admissions/wards/pharmacy/billing) against installed modules.

If only clinic/base: reposition as Helpers Clinic and Medical Records Management / نظام Helpers لإدارة العيادات والسجلات الطبية with patients, physicians, appointments, consultations, prescriptions, procedures, core billing.

Use broader Hospital Management System naming only when hospitalization/ward/bed modules installed+verified.

## Package names
EN: Helpers HMS Trial, Helpers Clinic, Helpers Hospital
AR: تجربة Helpers HMS, Helpers للعيادات, Helpers للمستشفيات
Only show packages matching entitlements/modules. Clinic-only ≠ hospital package.

## Tests must assert public HTML excludes
Almighty, AlmightyCS, Almighty Consulting Solutions, HMS by, vendor URLs, original manifest marketing titles.
Also: internal tech names unchanged; public names Helpers HMS; uninstalled addons not included; readiness/packages match inventory.

## Report separately
1. Public-facing names changed
2. Internal identifiers preserved
3. Vendor refs removed from public output
4. Feature claims removed/corrected
5. Installed modules used as evidence
6. Licensing uncertainty needing vendor permission
