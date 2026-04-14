#!/usr/bin/env bash
# Smoke tests for the Breadcrumbs MCP endpoint.
# Usage: ./tests/test_mcp.sh [port]
# Requires server.py to be running.

set -euo pipefail

PORT="${1:-8765}"
URL="http://localhost:${PORT}/mcp"
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

rpc() {
  curl -s -X POST "$URL" -H 'Content-Type: application/json' -d "$1"
}

echo "Testing MCP endpoint at $URL"
echo

# --- Protocol ---

echo "Protocol:"

result=$(rpc '{"jsonrpc":"2.0","method":"initialize","params":{"protocolVersion":"2025-03-26","clientInfo":{"name":"test"}},"id":1}')
check "initialize returns protocolVersion" "2025-03-26" "$result"
check "initialize returns server name" "breadcrumbs" "$result"

result=$(rpc '{"jsonrpc":"2.0","method":"tools/list","id":2}')
tool_count=$(echo "$result" | python3 -c "import sys,json; print(len(json.load(sys.stdin)['result']['tools']))")
check "tools/list returns 5 tools" "5" "$tool_count"

result=$(rpc '{"jsonrpc":"2.0","method":"bogus","id":3}')
check "unknown method returns error" "-32601" "$result"

result=$(rpc '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"bogus_tool","arguments":{}},"id":4}')
check "unknown tool returns error" "-32601" "$result"

echo

# --- Tools ---

echo "Tools:"

result=$(rpc '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"list_projects","arguments":{}},"id":10}')
project_count=$(echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); print(len(json.loads(r['result']['content'][0]['text'])))")
check "list_projects returns projects" "true" "$([ "$project_count" -gt 0 ] && echo true || echo false)"

result=$(rpc '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"list_sessions","arguments":{"limit":3}},"id":11}')
session_count=$(echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); print(len(json.loads(r['result']['content'][0]['text'])))")
check "list_sessions returns sessions" "true" "$([ "$session_count" -gt 0 ] && echo true || echo false)"
check "list_sessions respects limit" "true" "$([ "$session_count" -le 3 ] && echo true || echo false)"

result=$(rpc '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"list_sessions","arguments":{"project":"nonexistent_project_xyz"}},"id":12}')
empty_count=$(echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); print(len(json.loads(r['result']['content'][0]['text'])))")
check "list_sessions filters by project" "0" "$empty_count"

# Get a real session_id for testing
session_id=$(rpc '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"list_sessions","arguments":{"limit":1}},"id":13}' \
  | python3 -c "import sys,json; r=json.load(sys.stdin); sessions=json.loads(r['result']['content'][0]['text']); print(sessions[0]['session_id'])")

result=$(rpc "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"get_session_messages\",\"arguments\":{\"session_id\":\"$session_id\"}},\"id\":14}")
msg_count=$(echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); print(len(json.loads(r['result']['content'][0]['text'])))")
check "get_session_messages returns messages" "true" "$([ "$msg_count" -gt 0 ] && echo true || echo false)"

# Check default types filter (user + assistant only)
msg_types=$(echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); msgs=json.loads(r['result']['content'][0]['text']); print(' '.join(sorted(set(m['type'] for m in msgs))))")
check "get_session_messages defaults to user+assistant" "true" "$(echo "$msg_types" | grep -v tool_result | grep -v system_injection | grep -v progress > /dev/null && echo true || echo false)"

# Pagination: limit
result=$(rpc "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"get_session_messages\",\"arguments\":{\"session_id\":\"$session_id\",\"limit\":3}},\"id\":140}")
limited_count=$(echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); print(len(json.loads(r['result']['content'][0]['text'])))")
check "get_session_messages respects limit" "true" "$([ "$limited_count" -le 3 ] && echo true || echo false)"

# Pagination: negative offset returns tail
result=$(rpc "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"get_session_messages\",\"arguments\":{\"session_id\":\"$session_id\",\"offset\":-2,\"limit\":2}},\"id\":141}")
tail_count=$(echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); print(len(json.loads(r['result']['content'][0]['text'])))")
check "get_session_messages negative offset returns tail" "true" "$([ "$tail_count" -le 2 ] && echo true || echo false)"

# Verify the tail UUIDs match the last 2 UUIDs from the full message list
result_full=$(rpc "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"get_session_messages\",\"arguments\":{\"session_id\":\"$session_id\",\"limit\":9999,\"offset\":0}},\"id\":141}")
uuid_match=$(TAIL_JSON="$result" FULL_JSON="$result_full" python3 -c "
import os, json

tail_msgs = json.loads(json.loads(os.environ['TAIL_JSON'])['result']['content'][0]['text'])
full_msgs = json.loads(json.loads(os.environ['FULL_JSON'])['result']['content'][0]['text'])

if len(full_msgs) < 2:
    print('skipped')
else:
    tail_uuids = [m['uuid'] for m in tail_msgs]
    expected_uuids = [m['uuid'] for m in full_msgs[-2:]]
    print('true' if tail_uuids == expected_uuids else 'false (tail=' + str(tail_uuids) + ' expected=' + str(expected_uuids) + ')')
")
[ "$uuid_match" = "skipped" ] || check "get_session_messages negative offset UUIDs match tail of full list" "true" "$uuid_match"

# Default cap: no limit specified → response is capped (default 100)
result=$(rpc "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"get_session_messages\",\"arguments\":{\"session_id\":\"$session_id\"}},\"id\":142}")
default_count=$(echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); print(len(json.loads(r['result']['content'][0]['text'])))")
check "get_session_messages default caps at 100" "true" "$([ "$default_count" -le 100 ] && echo true || echo false)"

result=$(rpc '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"search_messages","arguments":{"query":"the","limit":5}},"id":15}')
search_count=$(echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); print(len(json.loads(r['result']['content'][0]['text'])))")
check "search_messages returns results" "true" "$([ "$search_count" -gt 0 ] && echo true || echo false)"
check "search_messages respects limit" "true" "$([ "$search_count" -le 5 ] && echo true || echo false)"

result=$(rpc '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"get_stats","arguments":{}},"id":16}')
total_sessions=$(echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); stats=json.loads(r['result']['content'][0]['text']); print(stats['total_sessions'])")
check "get_stats returns session count" "true" "$([ "$total_sessions" -gt 0 ] && echo true || echo false)"
has_top_tools=$(echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); stats=json.loads(r['result']['content'][0]['text']); print('true' if 'top_tools' in stats else 'false')")
check "get_stats includes top_tools" "true" "$has_top_tools"

echo
echo "Results: $PASS passed, $FAIL failed"
[ "$FAIL" -eq 0 ] && exit 0 || exit 1
