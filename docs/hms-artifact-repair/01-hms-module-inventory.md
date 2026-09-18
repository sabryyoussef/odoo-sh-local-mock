# Part 1 — HMS Module Inventory

## Source Location

`/home/sabry/odoo_base/base_odoo_19/projects/alzaeem/`

## Discovered HMS Modules

### 1. `acs_hms_base`
- **Path**: `/home/sabry/odoo_base/base_odoo_19/projects/alzaeem/acs_hms_base/`
- **Manifest**: `__manifest__.py`
- **Name**: "Base - Hospital Management System ( HMS by AlmightyCS )"
- **Version**: 19.0.1.0.5
- **Category**: Medical
- **Application**: True
- **License**: OPL-1
- **Depends**: `account`, `stock`, `hr`, `product_expiry`
- **Models**: hms_mixin, hms_base, hms_consumable_line, partner, patient, physician, product, drug, account, ir_sequence, res_config, stock_move, country
- **Key Model**: `hms.patient` (inherits res.partner)
- **Views**: hms_base_views, patient_view, physician_view, product_view, drug_view, account_view, res_config_settings, stock_view, res_country_view, menu_item
- **Menus**: Patient, Physician, Services, Medicines (+ configurations)
- **Demo**: company_demo.xml

### 2. `acs_hms`
- **Path**: `/home/sabry/odoo_base/base_odoo_19/projects/alzaeem/acs_hms/`
- **Manifest**: `__manifest__.py`
- **Name**: "Clinic - Hospital Management System ( HMS by AlmightyCS )"
- **Version**: 19.0.1.0.4
- **Category**: Medical
- **Application**: True
- **License**: OPL-1
- **Depends**: `acs_hms_base`, `website`, `digest`
- **Views**: menu_item, hms_base_views, patient_view, physician_view, evaluation_view, appointment_view, diseases_view, medicament_view, prescription_view, medication_view, treatment_view, procedure_view, resource_cal, medical_alert, account_view, product_kit_view, template, res_config_settings_views, digest_view, res_users
- **Demo**: doctor_demo, patient_demo, appointment_demo, medicament_demo
- **Contains**: Full clinical workflows (appointments, evaluations, prescriptions, treatments, procedures, medical alerts)

### 3. `alzaeem_acs_hms_fix`
- **Path**: `/home/sabry/odoo_base/base_odoo_19/projects/alzaeem/alzaeem_acs_hms_fix/`
- **Manifest**: `__manifest__.py`
- **Name**: "Alzaeem ACS HMS — Odoo 19 Compatibility"
- **Version**: 19.0.1.1.0
- **Category**: Medical
- **Application**: False
- **License**: LGPL-3
- **Depends**: `acs_hms`, `acs_hms_base`
- **Purpose**: Odoo 19 compatibility overlays (physician groups, amount due, pricelist invoice, vitals graph)

## Module Dependency Graph

```
acs_hms_base (depends: account, stock, hr, product_expiry)
    └── acs_hms (depends: acs_hms_base, website, digest)
        └── alzaeem_acs_hms_fix (depends: acs_hms, acs_hms_base)
```

## Installation Order

1. `acs_hms_base` (must be first)
2. `acs_hms` (depends on acs_hms_base)
3. `alzaeem_acs_hms_fix` (depends on both)

## Catalog Reference

In `solution_module_docs_service.py`:
```python
_SOLUTION_MODULES = {
    "hms": ["acs_hms_base", "acs_hms", "alzaeem_acs_hms_fix"],
    ...
}
```

Default roots:
```python
_DEFAULT_ROOTS = {
    "hms": ["/home/sabry/odoo_base/base_odoo_19/projects/alzaeem"],
    ...
}
```

## HMS-Specific Identifiers

- **Model**: `hms.patient`
- **Model**: `hms.physician`
- **Menu**: `main_menu_patient` (Patient)
- **Menu**: `main_menu_physician` (Physician)
- **Menu**: `acs_services_root` (Services)
- **Menu**: `acs_medicine_root` (Medicines)
