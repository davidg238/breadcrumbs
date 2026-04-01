# Breadcrumbs MCP Endpoint — Agent Access to Session History

**Date:** 2026-04-01
**Status:** Approved
**Depends on:** viewer-design.md (server.py)

## Purpose

Add an MCP endpoint to the existing `server.py` so any MCP-capable agent can query session history across all projects. Enables project diary reconstruction, pattern mining, and context retrieval from past sessions.

## Architecture

Single new endpoint added to the existing HTTP server: `POST /mcp`. Implements MCP Streamable HTTP transport (2025-03-26 spec). All tools are stateless request/response — no streaming or session state needed.

```
Agent (Claude Code, etc.)
  ↓ POST /mcp (JSON-RPC)
server.py
  ↓ SQL queries
~/.claude/breadcrumbs.db
```

No new files. No new processes. No new dependencies.

## MCP Protocol Handling

Minimal JSON-RPC 2.0 dispatch. Three methods:

| Method | Purpose |
|---|---|
| `initialize` | Return server name, version, capabilities |
| `tools/list` | Return tool schemas |
| `tools/call` | Dispatch to tool handler, return result |

### Request format

```json
{"jsonrpc": "2.0", "method": "tools/call", "params": {"name": "search_messages", "arguments": {"query": "auth middleware"}}, "id": 1}
```

### Response format

```json
{"jsonrpc": "2.0", "result": {"content": [{"type": "text", "text": "..."}]}, "id": 1}
```

### Error codes

| Code | Meaning |
|---|---|
| -32600 | Invalid JSON-RPC request |
| -32601 | Unknown method or tool |
| -32602 | Invalid tool parameters |
| -32603 | Internal/DB error |

## MCP Tools

### list_projects

**Parameters:** none

**Returns:** JSON array of projects with:
- `project` — display name
- `cwd` — working directory
- `session_count` — number of sessions
- `first_session` — earliest session date
- `last_session` — latest session date
- `total_cost_usd` — sum of estimated costs

**SQL:** Aggregates from `sessions` joined with `messages` for cost data. Reuses logic from `get_sessions()`.

### list_sessions

**Parameters:**
- `project` (optional) — filter by project name
- `since` (optional) — ISO date string, sessions started after this date
- `until` (optional) — ISO date string, sessions started before this date
- `limit` (optional, default 20) — max sessions to return

**Returns:** JSON array of sessions with: session_id, name, project, model, started_at, duration_seconds, message_count, estimated_cost_usd.

**SQL:** Filtered query on `sessions` table with optional WHERE clauses.

### get_session_messages

**Parameters:**
- `session_id` (required) — which session
- `types` (optional) — array of types to include, e.g. `["user", "assistant"]`. Defaults to `["user", "assistant"]` (excludes tool_result, system_injection, system, progress by default).

**Returns:** JSON array of messages with: uuid, type, role, content_text, tool_name, timestamp, sequence.

Excludes `tool_input`, `tool_result`, and `usage_json` by default to keep responses concise. The agent sees the conversation flow without verbose tool dumps.

**SQL:** Filtered query on `messages` table.

### search_messages

**Parameters:**
- `query` (required) — text to search for (SQL LIKE with wildcards)
- `project` (optional) — filter by project
- `since` (optional) — ISO date string
- `limit` (optional, default 20) — max results

**Returns:** JSON array of matches with: session_id, session_name, project, uuid, type, content_text (truncated to 500 chars), timestamp.

Provides enough context to identify relevant sessions, then the agent can call `get_session_messages` for full details.

**SQL:** `WHERE content_text LIKE '%query%'` with optional project/date filters.

### get_stats

**Parameters:**
- `project` (optional) — filter by project
- `since` (optional) — ISO date string
- `until` (optional) — ISO date string

**Returns:** JSON object with:
- `total_sessions` — count
- `total_messages` — count
- `total_input_tokens` — sum
- `total_output_tokens` — sum
- `total_cache_tokens` — sum (read + write)
- `total_cost_usd` — sum
- `top_tools` — array of `{tool_name, count}` top 10 most used tools
- `sessions_by_project` — array of `{project, count}` if no project filter

**SQL:** Aggregate queries on `messages` and `sessions`.

## Landing Page Addition

Add to the bottom of the project summary table in the viewer:

```
MCP endpoint: http://localhost:{port}/mcp
```

Displayed as a copyable text element so the user can paste it into their MCP client config.

## Claude Code MCP Config

Once the server is running, the user adds to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "breadcrumbs": {
      "type": "url",
      "url": "http://localhost:8765/mcp"
    }
  }
}
```

## Implementation in server.py

### New code (~100 lines)

1. **`handle_mcp(request_body) -> response_dict`** — JSON-RPC dispatch function
2. **`do_POST` method on Handler** — routes `POST /mcp` to `handle_mcp`
3. **5 tool handler functions** — each takes `arguments` dict, returns MCP content
4. **Tool schema definitions** — JSON Schema for each tool's parameters

### Reused code

- `get_db()` — database connection
- `get_sessions()` — session listing with computed fields (adapted for filtering)
- `get_messages()` — message retrieval (adapted for type filtering)
- `compute_cost()` — cost calculation
- `MODEL_PRICING` — pricing table

## Error Handling

- Malformed JSON body → -32600
- Unknown method → -32601
- Missing required params → -32602
- DB errors → -32603 with error message
- Non-POST to /mcp → 405 Method Not Allowed

## Non-Goals

- No authentication (localhost only, same as viewer)
- No streaming/SSE (all queries are request/response)
- No session state (each request is independent)
- No write operations (read-only access to history)
- No notifications or subscriptions
