#!/usr/bin/env bash
# run_vercel_battery.sh — run the full 44-check HTTP battery against the
# Vercel driver on the bench (mock KV + fake Calendar API).
#
# Fetches a fresh Groq surrogate at runtime (never printed, never
# written to disk). Starts the three servers, waits for /health, runs
# tests/test_vercel_e2e.py, then stops everything.
#
# Usage:  tests/run_vercel_battery.sh
# Results: test-evidence/test-vercel-e2e-results.json
set -euo pipefail
cd "$(dirname "$0")/.."

PY=~/workspace/venvs/luna-vercel/bin/python
RESET_KEY="vercel-battery-$(date +%s)"

# --- Groq surrogate (fresh authd credential; never printed) -----------
GROQ_API_KEY=$(PYTHONPATH=/opt/muse/skills/custom-connector-setup/bin "$PY" -c "
from dynamic_credentials import dynamic_credential_entry
print(dynamic_credential_entry('custom.groq')['surrogate'])
")
[ -n "$GROQ_API_KEY" ] || { echo "groq surrogate empty" >&2; exit 1; }
case "$GROQ_API_KEY" in hsurr:*) ;; *) echo "groq surrogate malformed" >&2; exit 1;; esac

# --- clean slate -------------------------------------------------------
rm -f /tmp/luna-fake-calendar-state.json /tmp/luna-fake-kv-state.json

# --- servers -----------------------------------------------------------
"$PY" tests/mock_kv_server.py 18098 test-token >/tmp/luna-vercel-mock-kv.log 2>&1 &
KV_PID=$!
"$PY" tests/fake_calendar_api.py 18103 >/tmp/luna-vercel-fake-cal.log 2>&1 &
CAL_PID=$!
cleanup() {
  kill "$KV_PID" "$CAL_PID" "$DRIVER_PID" 2>/dev/null || true
}
trap cleanup EXIT

export NO_PROXY="localhost,127.0.0.1" no_proxy="localhost,127.0.0.1"
export UPSTASH_REDIS_REST_URL="http://127.0.0.1:18098"
export UPSTASH_REDIS_REST_TOKEN="test-token"
export GOOGLE_API_DISCOVERY_URL="http://127.0.0.1:18103/discovery/calendar-v3.json"
export CALENDAR_ID="test-calendar"
export LUNA_TEST_RESET_KEY="$RESET_KEY"
export LUNA_TEST_OAUTH_JSON='{"refresh_token":"fake-refresh-token","client_id":"fake-client-id","client_secret":"fake-client-secret","token_uri":"http://127.0.0.1:18103/token","scopes":["https://www.googleapis.com/auth/calendar.events"],"access_token":"fake-access-token","expiry":"2030-01-01T00:00:00+00:00"}'
export GROQ_API_KEY
export PORT=18102

"$PY" index.py >/tmp/luna-vercel-driver.log 2>&1 &
DRIVER_PID=$!

# --- wait for /health ---------------------------------------------------
for _ in $(seq 1 30); do
  if curl -sf http://127.0.0.1:18102/health >/dev/null 2>&1; then break; fi
  sleep 1
done
curl -sf http://127.0.0.1:18102/health | "$PY" -c "import json,sys; d=json.load(sys.stdin); print('health:', d.get('ok'), 'store:', d.get('store'), 'calendar_connected:', d.get('calendar_connected'))"

# --- battery -----------------------------------------------------------
LUNA_BASE=http://127.0.0.1:18102 LUNA_TEST_RESET_KEY="$RESET_KEY" \
  CALENDAR_ID="test-calendar" "$PY" -u tests/test_vercel_e2e.py
