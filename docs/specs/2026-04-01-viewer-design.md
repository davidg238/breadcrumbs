# Breadcrumbs Viewer — Web UI for Session History

**Date:** 2026-04-01
**Status:** Approved
**Depends on:** breadcrumbs-design.md (session recorder)

## Purpose

A local web server that lets you browse, search, and analyze all recorded Claude Code sessions. Single-file Python script, zero dependencies, opens in your browser.

## Architecture

```
browser (SPA)  ←→  server.py (localhost:PORT)  ←→  ~/.claude/breadcrumbs.db
```

Single file: `server.py`. Uses Python stdlib `http.server` and `sqlite3`. All HTML, CSS, and JS embedded as string constants. Launches with `python3 server.py`, opens browser automatically.

## API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/` | Single-page app (embedded HTML/CSS/JS) |
| GET | `/api/sessions` | All sessions, newest first |
| PATCH | `/api/sessions/<id>` | Update session name |
| GET | `/api/sessions/<id>/messages` | All messages for a session, ordered by sequence |
| GET | `/api/images/<id>` | Raw image bytes with correct content-type header |

### GET /api/sessions

Returns JSON array. Each session includes computed fields:

```json
{
  "session_id": "2f77fc69-...",
  "name": "breadcrumbs design",
  "project": "breadcrumbs",
  "cwd": "/home/david/workspace/breadcrumbs",
  "model": "claude-opus-4-6",
  "started_at": "2026-04-01T19:14:39.434Z",
  "updated_at": "2026-04-01T20:01:12.000Z",
  "git_branch": "master",
  "duration_seconds": 2793,
  "message_count": 134,
  "total_input_tokens": 450000,
  "total_output_tokens": 32000,
  "total_cache_write_tokens": 120000,
  "total_cache_read_tokens": 380000,
  "estimated_cost_usd": 2.30
}
```

**Project display name:** derived from last path component of `cwd` (e.g. `/home/david/workspace/breadcrumbs` → `breadcrumbs`).

**Session name:** from `sessions.name` column. Falls back to `"{project} — {date}"` if null.

### PATCH /api/sessions/<id>

Request body: `{"name": "my session name"}`

Updates `sessions.name`. Returns updated session object.

### GET /api/sessions/<id>/messages

Returns JSON array ordered by sequence:

```json
{
  "uuid": "4c36cc09-...",
  "type": "assistant",
  "role": "assistant",
  "content_text": "Let me research...",
  "tool_name": null,
  "tool_input": null,
  "tool_result": null,
  "model": "claude-opus-4-6",
  "timestamp": "2026-04-01T19:14:43.854Z",
  "sequence": 1,
  "has_images": true,
  "image_ids": [1, 2]
}
```

### GET /api/images/<id>

Returns raw image bytes. Sets `Content-Type` from `message_images.media_type`.

## Schema Changes

Add to `sessions` table:

```sql
ALTER TABLE sessions ADD COLUMN name TEXT;
```

Add to `messages` table:

```sql
ALTER TABLE messages ADD COLUMN usage_json TEXT;
```

The recorder (`session_recorder.py`) must be updated to:
1. Extract `usage` from assistant message objects and store as `usage_json`
2. Support the new columns

Schema migration: `server.py` runs ALTER TABLE on startup, wrapped in try/except (idempotent — fails silently if column already exists).

## UI Layout

### Sidebar (left, ~280px fixed)

- **Search box** at top — filters sessions by name, project, or content
- **Session list** grouped by project name
- Each entry displays:
  - Session name (or fallback: project + date)
  - Message count, duration, estimated cost
  - Subtle project label if not grouped
- Selected session highlighted
- Keyboard: Up/Down arrows to navigate, `/` to focus search

### Conversation Panel (right, flex)

#### Status Bar (top)
- Session name — click to edit inline, Enter to save, Escape to cancel
- Model name
- Duration (e.g. "45 min")
- Start time (e.g. "Apr 1, 2026 2:14 PM")
- Token counts (input / output)
- Estimated cost
- Image default toggle (expanded/collapsed)

#### Message List
Messages rendered as a vertical chat feed:

**User messages:**
- Light distinct background
- "You" label with timestamp
- Full text content

**Assistant messages:**
- Different background
- "Claude" label with timestamp and model
- Content rendered as markdown (basic: headers, code blocks, bold, italic, lists, links)

**Tool calls (collapsed by default):**
- Compact single line: icon + `Tool: {name}` + truncated first line of input
- Twisty/chevron to expand
- Expanded view shows:
  - Full tool input (in a code block)
  - Full tool result (in a code block)
- Consecutive tool calls shown individually, each collapsed

**Images (expanded by default):**
- Rendered inline at reasonable max-width (600px)
- Twisty to collapse to a placeholder line: "Image (click to expand)"
- Toggle in status bar flips the default for all images in current session

**System messages:**
- Dimmed text, collapsed by default
- Twisty to expand

## Cost Calculation

Pricing table embedded in `server.py`:

| Model | Input/MTok | Output/MTok | Cache Write/MTok | Cache Read/MTok |
|---|---|---|---|---|
| claude-opus-4-6 | $15.00 | $75.00 | $18.75 | $1.50 |
| claude-sonnet-4-6 | $3.00 | $15.00 | $3.75 | $0.30 |
| claude-haiku-4-5 | $0.80 | $4.00 | $1.00 | $0.08 |

Cost computed per session by summing `usage_json` across all assistant messages:

```
cost = (input_tokens * input_price
      + output_tokens * output_price
      + cache_creation_input_tokens * cache_write_price
      + cache_read_input_tokens * cache_read_price) / 1_000_000
```

Unknown models: show token counts, display "?" for cost.

## Markdown Rendering

Minimal markdown renderer embedded in JS (no library dependency). Supports:
- Headers (h1-h3)
- Code blocks (``` with language hint for syntax coloring via simple keyword highlighting)
- Inline code
- Bold, italic
- Unordered and ordered lists
- Links
- Line breaks / paragraphs

Tables and complex markdown degrade gracefully to plain text.

## Startup

```bash
python3 server.py                    # default port 8765
python3 server.py --port 9000        # custom port
python3 server.py --no-open          # don't auto-open browser
```

On startup:
1. Check `~/.claude/breadcrumbs.db` exists
2. Run schema migrations (ADD COLUMN IF NOT EXISTS)
3. Bind to `localhost:PORT`
4. Open default browser
5. Print URL to terminal

## Future: MCP Endpoint

Planned for v2, not built in v1. Same `server.py` would add:

- SSE-based MCP server on `/mcp` (or separate `--mcp` flag)
- Tools: `list_sessions`, `get_messages`, `search_messages`, `get_session_stats`
- Maps directly to the same SQL queries as the REST API
- Allows any MCP-capable agent to mine session history across all projects

## Error Handling

- DB not found: print helpful message pointing to install.py, exit 1
- Port in use: try next port, print actual port
- All API errors return JSON `{"error": "message"}` with appropriate HTTP status
- Malformed requests: 400 with description

## File Changes

| File | Change |
|---|---|
| `server.py` | New file — the viewer |
| `session_recorder.py` | Add `usage_json` extraction, support `name` column |
| `install.py` | No change (server.py is run manually, not a hook) |
| `README.md` | Add viewer usage section |

## Non-Goals

- No authentication (localhost only)
- No editing or deleting messages
- No real-time streaming (refresh to see new data)
- No export functionality (use sqlite3 CLI directly)
- No external dependencies

## Addendum 2026-07-07 — session load performance & progress

**Decision 1 — single-scan aggregation.** `get_sessions` computes message counts,
first/last timestamps and token sums in one pass over the `messages` table, keyed
by `session_id`. It replaces the previous GROUP BY join plus one usage query per
session (an N+1) that made the first load ~11s with 500+ sessions; it is now under
2s. The per-message `json.loads` of `usage_json` is the remaining cost — acceptable
for a personal-scale DB; a materialized per-session aggregate table is the next
lever if it ever needs to be faster.

**Decision 2 — streaming load with progress.** `/api/sessions` streams
newline-delimited JSON: a `meta` line with the total, then one session per line.
The viewer shows a full-screen "Indexing X of N sessions" progress bar that fills
as records arrive. This is *progressive delivery of one response*, not live data
streaming — the "no real-time streaming" non-goal above (refresh to see new data)
still holds.

## Addendum 2026-07-08 — user-message classification & injection rendering

**Problem.** Autonomous sessions rendered almost none of the user's typed input as
"You", while pasting giant `<task-notification>` blocks *as* user input. Root cause
was a classifier keyed on content **shape**: every string-form `user` transcript
entry was kept as "You" (so `<task-notification>`, `<command-name>` expansions and
`<local-command-stdout>` showed as the user), and every list-form entry was demoted
to `system_injection` (hiding genuine prompts delivered as a list of text blocks).

**Decision — classify by intent markers, not shape.** `classify_user_entry`
(`session_recorder.py`) demotes a `role=user` entry only when: it carries a
`tool_result`; the transcript entry has `isMeta: true` (skill loads, caveats); or its
text opens with a known Claude Code injection tag (`task-notification`,
`command-name/message/args`, `local-command-stdout/stderr/caveat`, `system-reminder`,
`user-prompt-submit-hook`, `bash-input/stdout/stderr`). Everything else stays `user`,
whether string or text-block list. A user who genuinely types an unknown tag like
`<div>` is still "You" — only the known-tag allowlist demotes. Verified against the
four `nsl-tuvm` transcripts: genuine typed prompts are always `isMeta:false` and
never open with an injection tag (19/19).

**Decision — label + preview for injected rows.** The viewer's `system_injection`
branch now derives a label and one-line preview via `injectionMeta` (`server.py`):
`Task Notification · <status> · <summary>`, `Command`, `Command Output`, `Skill Load`,
`System Reminder`. Rows stay collapsed by default, so a delegated run is scannable
instead of a wall of always-expanded XML.

**Backfill.** Applied to existing data by re-running the idempotent `handle_sync`
over every session whose transcript still exists on disk (164 of 510; the other 346
have no transcript and were left untouched).
