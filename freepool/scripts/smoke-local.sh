#!/usr/bin/env bash
# FreePool local smoke — requires proxy on 127.0.0.1:8080
set -euo pipefail
KEY="${FREEPOOL_PROXY_KEY:-dev-local-key}"
BASE="http://127.0.0.1:8080/v1"
curl -sfS -H "Authorization: Bearer ${KEY}" "${BASE}/models" >/dev/null
echo "models: ok"
if command -v freellmpool >/dev/null 2>&1; then
  FREELLMPOOL_PROXY_URL="${BASE%/v1}" freellmpool ask --max-tokens 16 "Reply with exactly: freepool-ok"
else
  echo "freellmpool CLI not installed; skipped ask"
fi
