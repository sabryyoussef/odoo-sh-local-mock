#!/usr/bin/env bash
# Lightweight demo readiness checks — never prints secret values.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
API="${DEMO_API_BASE:-http://127.0.0.1:8000}"
ok=0
fail=0

check() {
  local name="$1"
  shift
  if "$@" >/dev/null 2>&1; then
    echo "PASS  $name"
    ok=$((ok+1))
  else
    echo "FAIL  $name"
    fail=$((fail+1))
  fi
}

check "health endpoint" curl -fsS "$API/health"
check "control.db exists" test -f "$ROOT/data/control.db"
check ".env present (gitignored)" test -f "$ROOT/.env"
check ".env not tracked" bash -c '! git -C "'"$ROOT"'" ls-files --error-unmatch .env >/dev/null 2>&1'
# Config keys present without printing values
check "GITHUB_CLIENT_ID set" bash -c 'grep -q "^GITHUB_CLIENT_ID=.\+" "'"$ROOT"'/.env"'
check "GITHUB_WEBHOOK_SECRET set" bash -c 'grep -q "^GITHUB_WEBHOOK_SECRET=.\+" "'"$ROOT"'/.env"'
check "SESSION_SECRET set" bash -c 'grep -q "^SESSION_SECRET=.\+" "'"$ROOT"'/.env"'
check "build-postgres running" docker compose ps --status running build-postgres
check "control-api running" docker compose ps --status running control-api

docker compose exec -T control-api python - <<'PY' >/tmp/mosh_demo_check.txt 2>/dev/null || true
from app.db import SessionLocal
from app.models import Project, Build
from sqlalchemy import select
db=SessionLocal()
p=db.scalar(select(Project).where(Project.slug=="mosh-odoo19-hello"))
print("demo_exists", bool(p))
print("webhook_active", bool(p and p.github_webhook_active))
ports={}
for b in db.scalars(select(Build).where(Build.status=="running")).all():
    if b.http_port in ports:
        print("dup_port", b.http_port)
    ports[b.http_port]=b.id
print("running_ports", sorted(p for p in ports if p))
db.close()
PY

if grep -q "demo_exists True" /tmp/mosh_demo_check.txt 2>/dev/null; then
  echo "PASS  demo project exists"
  ok=$((ok+1))
else
  echo "FAIL  demo project exists"
  fail=$((fail+1))
fi
if grep -q "webhook_active True" /tmp/mosh_demo_check.txt 2>/dev/null; then
  echo "PASS  demo webhook enabled"
  ok=$((ok+1))
else
  echo "FAIL  demo webhook enabled"
  fail=$((fail+1))
fi
if grep -q "dup_port" /tmp/mosh_demo_check.txt 2>/dev/null; then
  echo "FAIL  duplicate active ports"
  fail=$((fail+1))
else
  echo "PASS  no duplicate active ports"
  ok=$((ok+1))
fi

echo "----"
echo "Passed: $ok  Failed: $fail"
exit "$fail"
