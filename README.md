# Breadcrumbs

Claude Code already saves every session to disk as JSONL transcript files. You just can't easily browse or search them.

Breadcrumbs changes that:

- **Indexes all your history into SQLite** — `install.py` bulk-imports every existing session across all projects. SQL queries, full-text search, cross-project analysis — all instant instead of parsing flat files.
- **Keeps it current with hooks** — every new prompt and response is recorded automatically. The database stays in sync with your active sessions, even across multiple Claude Code instances.
- **Browse it in your browser** — `server.py` serves a local web UI with a session list, conversation viewer, and project filtering.
- **MCP endpoint for agents** — any MCP-capable agent can query your entire development history: "what bugs did we fix last week?", "how did this architecture evolve?", "what patterns repeat across projects?". Project diaries, decision logs, knowledge extraction — all from data you're already generating.

The raw material has been there all along. Breadcrumbs just makes it accessible.

Inspired by [Shelley](https://github.com/boldsoftware/shelley)'s approach to recording agent sessions in SQLite.

## Requirements

- Python 3.6+
- Claude Code

No additional packages needed — stdlib only.

## Install

```bash
git clone https://github.com/davidg238/breadcrumbs.git
cd breadcrumbs
python3 install.py
```

Then restart Claude Code.

## Uninstall

```bash
cd breadcrumbs
python3 uninstall.py
```

The database is preserved after uninstall. Delete `~/.claude/breadcrumbs.db` manually to remove all data.

## How It Works

Two Claude Code hooks record session data:

- **UserPromptSubmit** — records your prompt immediately (crash insurance)
- **Stop** — syncs the full transcript to SQLite after each Claude response

Data is stored in `~/.claude/breadcrumbs.db` with three tables:

| Table | Contents |
|---|---|
| `sessions` | One row per session — project, cwd, model, git branch, timestamps |
| `messages` | Every message — prompts, responses, tool calls, results |
| `message_images` | Screenshots and images extracted from messages |

## Viewer

Browse sessions in your browser:

```bash
python3 server.py                    # start viewer
python3 server.py --open             # also open browser
python3 server.py --port 9000        # custom port
```

Features:
- Session list grouped by project, with search and keyboard navigation
- Conversation view with user/assistant messages, collapsible tool calls
- Inline image display with expand/collapse toggle
- Token usage per session (input, output, cache)
- Editable session names (click the name in the status bar)
- Keyboard: `/` to search, Up/Down to navigate sessions, Escape to blur

## Run as a Service (Linux)

To have the viewer start automatically at login and run in the background, install it as a systemd user service.

Create `~/.config/systemd/user/breadcrumbs.service`:

```ini
[Unit]
Description=Breadcrumbs Viewer (Claude Code session history)
After=network.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 %h/path/to/breadcrumbs/server.py --port 8765
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
```

Replace `%h/path/to/breadcrumbs` with the absolute path to your clone. Then:

```bash
systemctl --user daemon-reload
systemctl --user enable --now breadcrumbs.service
loginctl enable-linger $USER       # keep it running when logged out (may need sudo)
```

Manage it:

```bash
systemctl --user status breadcrumbs
systemctl --user restart breadcrumbs
journalctl --user -u breadcrumbs -f
```

The server binds to `127.0.0.1` only — not reachable over the network.

**macOS:** use `launchd` with a `~/Library/LaunchAgents/com.breadcrumbs.plist` file instead.
**Windows:** use Task Scheduler with trigger "At log on", or run `server.py` via `pythonw.exe` from the Startup folder.

## MCP Endpoint

The viewer includes an MCP server at `http://localhost:8765/mcp` (shown on the landing page). Any MCP-capable agent can query your session history.

**Setup:** Add to `~/.claude/settings.json` (with `server.py` running):

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

**Available tools:**

| Tool | Description |
|---|---|
| `list_projects` | All projects with session counts and date ranges |
| `list_sessions` | Sessions filtered by project, date range; optional `include_previews` |
| `get_session_messages` | Messages for a session; supports `limit` / `offset` (negative offset = tail) |
| `search_messages` | Full-text search; optional `session_id` scope |
| `get_stats` | Aggregate stats: tokens, top tools used |

## Querying

```bash
sqlite3 ~/.claude/breadcrumbs.db
```

Example queries:

```sql
-- All sessions, most recent first
SELECT session_id, project, started_at FROM sessions ORDER BY started_at DESC;

-- Messages from a specific session
SELECT type, role, substr(content_text, 1, 100), timestamp
FROM messages WHERE session_id = '...' ORDER BY sequence;

-- All tool usage across projects
SELECT tool_name, COUNT(*) as uses
FROM messages WHERE tool_name IS NOT NULL
GROUP BY tool_name ORDER BY uses DESC;

-- Sessions with screenshots
SELECT DISTINCT s.session_id, s.project, s.started_at
FROM sessions s
JOIN messages m ON m.session_id = s.session_id
JOIN message_images i ON i.message_uuid = m.uuid;

-- Full text search across all prompts
SELECT session_id, content_text, timestamp
FROM messages WHERE type = 'user' AND content_text LIKE '%search term%';
```

## Files

```
session_recorder.py  — the recording script (copied to ~/.claude/hooks/ on install)
server.py            — web viewer (run manually to browse sessions)
install.py           — sets up hooks in Claude Code settings
uninstall.py         — removes hooks, preserves database
```
## TODO / Limitations / Notes

*   **Operating Systems:** This tool has currently only been formally tested on Ubuntu 24.04 (Linux). However, because it relies wholly on Python's standard library, it should work on macOS and Windows as well.
*   **UI Limitations:** The web interface currently lacks session management features. It is not possible to delete sessions, clear history, or rename/favorite sessions directly from the UI (besides editing the current session name).
*   **Security Concerns:** The `server.py` web server lacks authentication and binds strictly to localhost (`127.0.0.1`). While this prevents access from the wider network, if run on a shared development machine or remote server, any other user logged into that same machine can access your chat history without authenticating.
