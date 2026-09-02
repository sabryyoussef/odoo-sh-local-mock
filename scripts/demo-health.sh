#!/usr/bin/env bash
# Concise demo health — never prints secrets.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

API="${DEMO_API_BASE:-http://127.0.0.1:8000}"

echo "=== Mock Odoo.sh demo health ==="

if curl -fsS "$API/health" >/tmp/mosh_health.json 2>/dev/null; then
  echo "Mock Odoo.sh Control API: OK"
  python3 - <<'PY'
import json
d=json.load(open("/tmp/mosh_health.json"))
print(f"OAuth configured: {'YES' if d.get('oauth_configured') else 'NO'}")
print(f"Webhook public URL: {'configured' if d.get('webhook_public_url_configured') else 'missing'}")
print(f"Phase: {d.get('phase')}")
print(f"Max concurrent builds: {d.get('max_concurrent_builds')}")
PY
else
  echo "Mock Odoo.sh Control API: DOWN"
  exit 1
fi

if docker compose ps --status running build-postgres 2>/dev/null | grep -q build-postgres; then
  echo "PostgreSQL (build-postgres): OK"
else
  echo "PostgreSQL (build-postgres): NOT RUNNING"
fi

# Counts from control DB via API container (no secret dump)
if docker compose exec -T control-api python - <<'PY' 2>/dev/null
from app.db import SessionLocal
from app.models import Project, Build, BUILD_STATUS_RUNNING, BUILD_STATUS_QUEUED, BUILD_STATUS_FAILED
from sqlalchemy import select, func
db=SessionLocal()
projects=db.scalar(select(func.count()).select_from(Project)) or 0
running=db.scalar(select(func.count()).select_from(Build).where(Build.status==BUILD_STATUS_RUNNING)) or 0
queued=db.scalar(select(func.count()).select_from(Build).where(Build.status==BUILD_STATUS_QUEUED)) or 0
failed=db.scalar(select(func.count()).select_from(Build).where(Build.status==BUILD_STATUS_FAILED)) or 0
demo=db.scalar(select(Project).where(Project.slug=="mosh-odoo19-hello"))
print(f"Projects: {projects}")
print(f"Running builds: {running}")
print(f"Queued builds: {queued}")
print(f"Failed builds: {failed}")
if demo:
    print(f"Demo project: OK (webhook_active={bool(demo.github_webhook_active)})")
else:
    print("Demo project: MISSING (mosh-odoo19-hello)")
db.close()
PY
then
  :
else
  echo "Control DB query: unavailable (is control-api up?)"
fi

mosh=$(docker ps -q --filter label=mock_odoo_sh=true | wc -l | tr -d ' ')
echo "Mock Odoo.sh containers: $mosh"
echo "=== end ==="
