# DP4 — Platform Templates

Base template code: `odoo19-community-base-v1`

- `TemplateDatabase` extended with `template_code`, `template_kind`, checksums, validation evidence
- `PlatformTemplateBuildJob` — worker-driven build (not HTTP inline)
- Operator: `POST /api/operator/platform/templates/build-base`, `GET /api/operator/platform/templates`

Build installs `BASE_REQUIRED_MODULES` only via one-shot Odoo `-i` init.

DP5 deployment is **blocked** until template `validation_status=ready`.
