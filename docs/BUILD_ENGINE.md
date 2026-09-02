"""
FIRST_REAL_ODOO_BUILD_ENGINE notes
==================================

Edition: Odoo Community only (official `odoo:19.0` / `18.0` / `17.0` images).
Enterprise entitlement/runtime is out of scope.

PostgreSQL: shared `build-postgres` service (PostgreSQL 16) for Odoo build DBs.
Control plane remains SQLite at `/data/control.db`.

Docker strategy (Option B):
  Mount the checked-out customer repository into a generic Odoo Community container.
  Do not rebuild Odoo core per customer.

Docker socket:
  Mounted only into `control-api` for this local prototype (root-equivalent Docker control).
  Never mounted into Odoo/build containers. Must be revisited for hardened production.

Failed DB policy (v1): retain failed PostgreSQL databases until Delete Build.

Restart reconciliation:
  Transitional builds → failed ("Build interrupted by control-plane restart.")
  Running builds → re-check container + HTTP; remain running if healthy, else failed.

Prototype security debt:
  Shared `mosh_odoo` role used by Odoo containers; admin role creates/drops DBs.
  Prefer stricter per-build roles later.
"""
