#!/usr/bin/env bash
# Smoke tests for the Breadcrumbs REST API.
# Usage: ./tests/test_api.sh [port]
# Requires server.py to be running.

set -euo pipefail

PORT="${1:-8765}"
URL="http://localhost:${PORT}"
PASS=0
FAIL=0

check() {
  local name="$1" expected="$2" actual="$3"
  if echo "$actual" | grep -qF -- "$expected"; then
    echo "  PASS: $name"
    PASS=$((PASS + 1))
  else
    echo "  FAIL: $name (expected '$expected', got '$actual')"
    FAIL=$((FAIL + 1))
  fi
}

echo "Testing REST API at $URL"
echo

# --- HTML ---

echo "HTML:"

result=$(curl -s -o /dev/null -w "%{http_code}" "$URL/")
check "GET / returns 200" "200" "$result"

result=$(curl -s "$URL/" | head -1)
check "GET / returns HTML" "DOCTYPE" "$result"

echo

# --- Sessions API ---

echo "Sessions API:"

result=$(curl -s "$URL/api/sessions")
session_count=$(echo "$result" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
check "GET /api/sessions returns sessions" "true" "$([ "$session_count" -gt 0 ] && echo true || echo false)"

# Check session fields
has_fields=$(echo "$result" | python3 -c "
import sys,json
s = json.load(sys.stdin)[0]
fields = ['session_id','name','project','model','started_at','message_count']
print('true' if all(f in s for f in fields) else 'false')
")
check "sessions have expected fields" "true" "$has_fields"

# Get a session_id for further tests
session_id=$(echo "$result" | python3 -c "import sys,json; print(json.load(sys.stdin)[0]['session_id'])")

echo

# --- Messages API ---

echo "Messages API:"

result=$(curl -s "$URL/api/sessions/$session_id/messages")
msg_count=$(echo "$result" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))")
check "GET messages returns messages" "true" "$([ "$msg_count" -gt 0 ] && echo true || echo false)"

has_fields=$(echo "$result" | python3 -c "
import sys,json
m = json.load(sys.stdin)[0]
fields = ['uuid','type','role','content_text','timestamp','sequence']
print('true' if all(f in m for f in fields) else 'false')
")
check "messages have expected fields" "true" "$has_fields"

echo

# --- PATCH session name ---

echo "Session naming:"

result=$(curl -s -X PATCH "$URL/api/sessions/$session_id" \
  -H 'Content-Type: application/json' \
  -d '{"name":"test-rename"}')
check "PATCH session name returns ok" "true" "$result"

# Verify it stuck
result=$(curl -s "$URL/api/sessions" | python3 -c "
import sys,json
sessions = json.load(sys.stdin)
s = next((s for s in sessions if s['session_id'] == '$session_id'), None)
print(s['name'] if s else 'not found')
")
check "session name was updated" "test-rename" "$result"

# Reset name
curl -s -X PATCH "$URL/api/sessions/$session_id" \
  -H 'Content-Type: application/json' \
  -d '{"name":null}' > /dev/null

echo

# --- Error handling ---

echo "Error handling:"

result=$(curl -s -o /dev/null -w "%{http_code}" "$URL/api/nonexistent")
check "unknown route returns 404" "404" "$result"

result=$(curl -s -X PATCH "$URL/api/sessions/$session_id" \
  -H 'Content-Type: application/json' \
  -d 'not json')
check "bad JSON returns error" "error" "$result"

echo
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
