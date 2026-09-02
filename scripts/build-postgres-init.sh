#!/bin/bash
# Create the Odoo runtime role used by build containers.
# Runs once on first volume init inside build-postgres.
set -euo pipefail
psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
DO \$\$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${MOSH_ODOO_USER}') THEN
    CREATE ROLE ${MOSH_ODOO_USER} LOGIN PASSWORD '${MOSH_ODOO_PASSWORD}';
  END IF;
END
\$\$;
EOSQL
