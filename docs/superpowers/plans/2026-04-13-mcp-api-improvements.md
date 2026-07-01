# MCP API Improvements Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the MCP API usable for the common "where did we leave off?" query without blowing the token budget — by adding pagination with negative-offset tail support, session previews, and intra-session search.

**Architecture:** Three small, independent changes to existing MCP handlers in `server.py`. No new tools, no new files. Each change adds optional parameters to an existing handler and is backward-compatible. Shell smoke tests in `tests/test_mcp.sh` are extended — matching the project's existing test style (no pytest introduction).

**Tech Stack:** Python 3 stdlib, SQLite, JSON-RPC over HTTP. Bash + curl + python3 for smoke tests.

---

## Background

Current pain point (from `HANDOFF-api-suggestions.md`): `get_session_messages` on a 1064-message session returned 223k chars and exceeded the token limit. The agent only wanted the last ~15 messages. Companion gaps: no way to preview sessions in `list_sessions`, no way to scope `search_messages` to one session.

## File Structure

All changes live in two files:

- **Modify** `server.py` — MCP tool schemas in `MCP_TOOLS` (lines ~680–743) and their handlers (`mcp_list_sessions` ~771, `mcp_get_session_messages` ~795, `mcp_search_messages` ~814).
- **Modify** `tests/test_mcp.sh` — append new `check` cases for each parameter.

No new modules. The handlers are already small and cohesive; growing them slightly is the right call versus splitting.

---

## Task 1: Pagination + default cap on `get_session_messages`

Add `limit` (default 100) and `offset` (default 0, negative = from end) to `get_session_messages`. This subsumes the proposed `get_session_tail` — callers do `offset=-20, limit=20` to get the tail.

**Files:**
- Modify: `server.py` (schema ~701–715, handler ~795–811)
- Test: `tests/test_mcp.sh` (append after line 78)

### Step 1: Write failing smoke tests

- [ ] **Step 1: Append tests to `tests/test_mcp.sh`**

Add after the existing `get_session_messages defaults to user+assistant` check (around line 78), before the blank line that precedes the search_messages section:

```bash
# Pagination: limit
result=$(rpc "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"get_session_messages\",\"arguments\":{\"session_id\":\"$session_id\",\"limit\":3}},\"id\":140}")
limited_count=$(echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); print(len(json.loads(r['result']['content'][0]['text'])))")
check "get_session_messages respects limit" "true" "$([ "$limited_count" -le 3 ] && echo true || echo false)"

# Pagination: negative offset returns tail
result=$(rpc "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"get_session_messages\",\"arguments\":{\"session_id\":\"$session_id\",\"offset\":-2,\"limit\":2}},\"id\":141}")
tail_count=$(echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); print(len(json.loads(r['result']['content'][0]['text'])))")
check "get_session_messages negative offset returns tail" "true" "$([ "$tail_count" -le 2 ] && echo true || echo false)"

# Default cap: no limit specified → response is capped (default 100)
result=$(rpc "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"get_session_messages\",\"arguments\":{\"session_id\":\"$session_id\"}},\"id\":142}")
default_count=$(echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); print(len(json.loads(r['result']['content'][0]['text'])))")
check "get_session_messages default caps at 100" "true" "$([ "$default_count" -le 100 ] && echo true || echo false)"
```

- [ ] **Step 2: Run tests to verify they fail**

In one terminal, ensure `python3 server.py` is running. Then run:

```bash
bash tests/test_mcp.sh
```

Expected: the three new checks fail (actual count may exceed limit because the parameters are currently ignored), or the limit/offset cases pass trivially only if the target session is tiny. If the selected session has <=2 messages the negative-offset check passes by accident — that's fine; the limit=3 check and default-cap check will still exercise the code path once implemented.

### Step 2: Implement pagination

- [ ] **Step 3: Update the tool schema in `server.py`**

Replace the `get_session_messages` entry in `MCP_TOOLS` (around lines 700–715) with:

```python
    {
        "name": "get_session_messages",
        "description": "Get messages for a session. Returns user prompts and assistant responses by default. Supports pagination via limit/offset (negative offset = from end, e.g. offset=-20 returns the last 20 messages after type filtering).",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "description": "Session ID"},
                "types": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Message types to include (default: ['user', 'assistant'])",
                },
                "limit": {"type": "integer", "description": "Max messages to return (default 100)"},
                "offset": {"type": "integer", "description": "Offset into filtered messages. Negative values count from the end (default 0)"},
            },
            "required": ["session_id"],
        },
    },
```

- [ ] **Step 4: Update the handler**

Replace `mcp_get_session_messages` (around lines 795–811) with:

```python
def mcp_get_session_messages(args):
    session_id = args.get("session_id")
    if not session_id:
        raise ValueError("session_id is required")
    types = args.get("types", ["user", "assistant"])
    limit = args.get("limit", 100)
    offset = args.get("offset", 0)
    db = get_db()
    try:
        all_msgs = get_messages(db, session_id)
        filtered = [m for m in all_msgs if m["type"] in types]
        total = len(filtered)
        if offset < 0:
            start = max(0, total + offset)
        else:
            start = min(offset, total)
        end = min(start + limit, total)
        page = filtered[start:end]
        result = [{
            "uuid": m["uuid"], "type": m["type"], "role": m["role"],
            "content_text": m["content_text"], "tool_name": m["tool_name"],
            "timestamp": m["timestamp"], "sequence": m["sequence"],
        } for m in page]
        return json.dumps(result, indent=2)
    finally:
        db.close()
```

Notes for the engineer:
- Offset/limit are applied *after* the type filter, so `offset=-20` consistently means "last 20 user+assistant messages" regardless of how many tool messages were interleaved.
- No pagination metadata (`total`, `has_more`) is returned. Keep response shape identical to today — just potentially shorter. YAGNI: callers who want more can raise `limit`.

- [ ] **Step 5: Run tests to verify they pass**

Restart the server (`pkill -f "python3 server.py"` then `python3 server.py &`), then:

```bash
bash tests/test_mcp.sh
```

Expected: all checks PASS including the three new ones.

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_mcp.sh
git commit -m "feat(mcp): paginate get_session_messages with negative-offset tail

Adds limit (default 100) and offset (negative = from end) params to
get_session_messages. Callers can fetch the closing exchange of a long
session with offset=-20, limit=20 instead of the full transcript.
Default cap prevents token-budget overruns on large sessions."
```

---

## Task 2: Session previews on `list_sessions`

Add an `include_previews` flag to `list_sessions`. When true, each returned session gains `last_user_message` and `last_assistant_message` fields (truncated to 500 chars). Often the preview alone answers the status question.

**Files:**
- Modify: `server.py` (schema ~686–699, handler ~771–792)
- Test: `tests/test_mcp.sh` (append after limit-filter check around line 66)

### Step 1: Write failing smoke test

- [ ] **Step 1: Append test to `tests/test_mcp.sh`**

Add after the `list_sessions filters by project` check (around line 66):

```bash
# Previews
result=$(rpc '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"list_sessions","arguments":{"limit":1,"include_previews":true}},"id":120}')
has_preview=$(echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); sessions=json.loads(r['result']['content'][0]['text']); print('true' if sessions and 'last_user_message' in sessions[0] and 'last_assistant_message' in sessions[0] else 'false')")
check "list_sessions include_previews adds preview fields" "true" "$has_preview"

# Default: no previews
result=$(rpc '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"list_sessions","arguments":{"limit":1}},"id":121}')
no_preview=$(echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); sessions=json.loads(r['result']['content'][0]['text']); print('true' if sessions and 'last_user_message' not in sessions[0] else 'false')")
check "list_sessions without flag omits preview fields" "true" "$no_preview"
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
bash tests/test_mcp.sh
```

Expected: `list_sessions include_previews adds preview fields` FAILs (the field isn't present yet). The "without flag omits preview fields" check passes trivially today.

### Step 2: Implement previews

- [ ] **Step 3: Update the tool schema**

Replace the `list_sessions` entry in `MCP_TOOLS` (around lines 687–699) with:

```python
    {
        "name": "list_sessions",
        "description": "List sessions, optionally filtered by project and date range. Set include_previews=true to attach truncated last_user_message and last_assistant_message to each session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "project": {"type": "string", "description": "Filter by project name"},
                "since": {"type": "string", "description": "ISO date, sessions after this date"},
                "until": {"type": "string", "description": "ISO date, sessions before this date"},
                "limit": {"type": "integer", "description": "Max sessions to return (default 20)"},
                "include_previews": {"type": "boolean", "description": "Attach last_user_message and last_assistant_message (truncated to 500 chars) to each session. Default false."},
            },
            "required": [],
        },
    },
```

- [ ] **Step 4: Update the handler**

Replace `mcp_list_sessions` (around lines 771–792) with:

```python
PREVIEW_MAX_CHARS = 500


def _last_message_of_type(db, session_id, msg_type):
    row = db.execute(
        "SELECT content_text FROM messages WHERE session_id = ? AND type = ? "
        "AND content_text IS NOT NULL AND content_text != '' "
        "ORDER BY sequence DESC LIMIT 1",
        (session_id, msg_type),
    ).fetchone()
    if not row:
        return None
    text = row["content_text"] or ""
    return text[:PREVIEW_MAX_CHARS]


def mcp_list_sessions(args):
    db = get_db()
    try:
        all_sessions = get_sessions(db)
        filtered = all_sessions
        if args.get("project"):
            filtered = [s for s in filtered if s["project"] == args["project"]]
        if args.get("since"):
            filtered = [s for s in filtered if s["started_at"] and s["started_at"] >= args["since"]]
        if args.get("until"):
            filtered = [s for s in filtered if s["started_at"] and s["started_at"] <= args["until"]]
        limit = args.get("limit", 20)
        filtered = filtered[:limit]
        include_previews = bool(args.get("include_previews"))
        result = []
        for s in filtered:
            row = {
                "session_id": s["session_id"], "name": s["name"], "project": s["project"],
                "model": s["model"], "started_at": s["started_at"],
                "duration_seconds": s["duration_seconds"], "message_count": s["message_count"],
                "estimated_cost_usd": s["estimated_cost_usd"],
            }
            if include_previews:
                row["last_user_message"] = _last_message_of_type(db, s["session_id"], "user")
                row["last_assistant_message"] = _last_message_of_type(db, s["session_id"], "assistant")
            result.append(row)
        return json.dumps(result, indent=2)
    finally:
        db.close()
```

Notes:
- One tiny SQL per session per message type — fine for the default `limit=20`. Gated behind the flag so the default response stays lean.
- Add `PREVIEW_MAX_CHARS = 500` and the `_last_message_of_type` helper immediately above `mcp_list_sessions`. Keep them private (single underscore) — they're only used here.

- [ ] **Step 5: Run tests to verify they pass**

Restart the server, then:

```bash
bash tests/test_mcp.sh
```

Expected: all checks PASS.

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_mcp.sh
git commit -m "feat(mcp): add include_previews flag to list_sessions

Attaches last_user_message and last_assistant_message (truncated to
500 chars) to each session when the flag is set. Lets the status
query be answered from list_sessions alone in many cases."
```

---

## Task 3: `session_id` filter on `search_messages`

Add an optional `session_id` param to `search_messages` so callers can search within a single session instead of the whole DB.

**Files:**
- Modify: `server.py` (schema ~716–729, handler ~814–851)
- Test: `tests/test_mcp.sh` (append after existing search_messages checks around line 83)

### Step 1: Write failing smoke test

- [ ] **Step 1: Append test to `tests/test_mcp.sh`**

Add after the `search_messages respects limit` check (around line 83):

```bash
# session_id filter
result=$(rpc "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"search_messages\",\"arguments\":{\"query\":\"the\",\"session_id\":\"$session_id\",\"limit\":10}},\"id\":150}")
all_same_session=$(echo "$result" | python3 -c "import sys,json; r=json.load(sys.stdin); hits=json.loads(r['result']['content'][0]['text']); print('true' if hits and all(h['session_id']=='$session_id' for h in hits) else ('empty' if not hits else 'false'))")
check "search_messages session_id filter scopes to one session" "true" "$([ "$all_same_session" = "true" ] || [ "$all_same_session" = "empty" ] && echo true || echo false)"
```

(The `empty` case is accepted because the target session might not contain "the" — unlikely but possible; absence of cross-session leakage is what we're asserting.)

- [ ] **Step 2: Run tests to verify it fails**

```bash
bash tests/test_mcp.sh
```

Expected: if the target session contains "the", the check FAILs because current implementation ignores `session_id` and returns hits from other sessions. If no hits exist, the check passes vacuously.

### Step 2: Implement filter

- [ ] **Step 3: Update the tool schema**

Replace the `search_messages` entry in `MCP_TOOLS` (around lines 717–729) with:

```python
    {
        "name": "search_messages",
        "description": "Full-text search across session messages. Pass session_id to scope to a single session.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Text to search for"},
                "project": {"type": "string", "description": "Filter by project name"},
                "session_id": {"type": "string", "description": "Scope search to a single session"},
                "since": {"type": "string", "description": "ISO date, messages after this date"},
                "limit": {"type": "integer", "description": "Max results (default 20)"},
            },
            "required": ["query"],
        },
    },
```

- [ ] **Step 4: Update the handler**

In `mcp_search_messages` (around lines 814–851), add a `session_id` extraction and a SQL clause. Replace the handler body with:

```python
def mcp_search_messages(args):
    query = args.get("query")
    if not query:
        raise ValueError("query is required")
    project_filter = args.get("project")
    session_id_filter = args.get("session_id")
    since = args.get("since")
    limit = args.get("limit", 20)
    db = get_db()
    try:
        sql = """
            SELECT m.uuid, m.session_id, m.type, m.content_text, m.timestamp,
                   s.name as session_name, s.cwd
            FROM messages m
            JOIN sessions s ON s.session_id = m.session_id
            WHERE m.content_text LIKE ?
        """
        params = [f"%{query}%"]
        if project_filter:
            sql += " AND s.cwd LIKE ?"
            params.append(f"%/{project_filter}")
        if session_id_filter:
            sql += " AND m.session_id = ?"
            params.append(session_id_filter)
        if since:
            sql += " AND m.timestamp >= ?"
            params.append(since)
        sql += " ORDER BY m.timestamp DESC LIMIT ?"
        params.append(limit)
        rows = db.execute(sql, params).fetchall()
        result = []
        for r in rows:
            cwd = r["cwd"] or ""
            project = cwd.rstrip("/").rsplit("/", 1)[-1] if cwd else ""
            result.append({
                "session_id": r["session_id"], "session_name": r["session_name"],
                "project": project, "uuid": r["uuid"], "type": r["type"],
                "content_text": (r["content_text"] or "")[:500], "timestamp": r["timestamp"],
            })
        return json.dumps(result, indent=2)
    finally:
        db.close()
```

- [ ] **Step 5: Run tests to verify they pass**

Restart the server, then:

```bash
bash tests/test_mcp.sh
```

Expected: all checks PASS (including existing ones — the filter is additive).

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_mcp.sh
git commit -m "feat(mcp): add session_id filter to search_messages

Lets callers scope full-text search to a single session instead of
searching the entire database."
```

---

## Task 4: Update README tool table

**Files:**
- Modify: `README.md` (lines ~139–140 — the MCP tools table)

- [ ] **Step 1: Read the current table**

```bash
sed -n '130,150p' README.md
```

- [ ] **Step 2: Update descriptions for the three modified tools**

Keep the table structure. Change the `list_sessions` and `get_session_messages` rows, and update `search_messages` if it's in the table. For example:

```markdown
| `list_sessions` | Sessions filtered by project, date range; optional `include_previews` |
| `get_session_messages` | Messages for a session; supports `limit` / `offset` (negative offset = tail) |
| `search_messages` | Full-text search; optional `session_id` scope |
```

Only touch rows that exist today. Don't add bullet lists, don't restructure. If the README describes these tools elsewhere in prose, leave that prose alone — the table is the authoritative one-liner.

- [ ] **Step 3: Commit**

```bash
git add README.md
git commit -m "docs: reflect new MCP params in tool table"
```

---

## Verification before declaring done

- [ ] **Step 1: Full test run**

```bash
# Ensure server is running the latest code
pkill -f "python3 server.py" || true
python3 server.py &
sleep 1
bash tests/test_mcp.sh
bash tests/test_api.sh
```

Expected: zero failures in either script.

- [ ] **Step 2: Real-world sanity check (the original pain case)**

Pick a large session from your own DB:

```bash
curl -s -X POST http://localhost:8765/mcp -H 'Content-Type: application/json' \
  -d '{"jsonrpc":"2.0","method":"tools/call","params":{"name":"list_sessions","arguments":{"limit":5,"include_previews":true}},"id":1}' \
  | python3 -m json.tool | head -80
```

Expected: each session has `last_user_message` and `last_assistant_message` populated (or `null` if a session truly has no messages of that type). Response is readable, not truncated.

Then fetch the tail of one:

```bash
SID=<paste a session_id>
curl -s -X POST http://localhost:8765/mcp -H 'Content-Type: application/json' \
  -d "{\"jsonrpc\":\"2.0\",\"method\":\"tools/call\",\"params\":{\"name\":\"get_session_messages\",\"arguments\":{\"session_id\":\"$SID\",\"offset\":-20,\"limit\":20}},\"id\":2}" \
  | python3 -m json.tool | wc -c
```

Expected: response is at most a few tens of KB regardless of session size.

- [ ] **Step 3: Stop the server**

```bash
pkill -f "python3 server.py"
```

---

## Out of scope (deferred)

- `get_session_summary` with synthesized fields (files_modified, git_commits, tool_call_count) — deferred per the handoff discussion. Heuristics over Bash output are fragile; revisit only if tail + previews prove insufficient.
- Tool-name filtering on `get_session_messages` (e.g. `tool_names=["Bash"]`) — the existing `types` param plus pagination covers most real cases. Revisit when there's a concrete need.
- Pagination metadata (`total`, `has_more`) on `get_session_messages` — YAGNI for now; callers bump `limit` if they need more.
