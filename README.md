# Breadcrumbs

Claude Code already saves every session to disk as JSONL transcript files. You just can't easily browse or search them.

Breadcrumbs changes that:

- **Indexes all your history into SQLite** — `install.py` bulk-imports every existing session across all projects. SQL queries, full-text search, cost tracking, cross-project analysis — all instant instead of parsing flat files.
- **Keeps it current with hooks** — every new prompt and response is recorded automatically. The database stays in sync with your active sessions, even across multiple Claude Code instances.
- **Browse it in your browser** — `server.py` serves a local web UI with a session list, conversation viewer, cost tracking, and project filtering.
- **MCP endpoint (planned)** — an agent will be able to query your entire development history: "what bugs did we fix last week?", "how did this architecture evolve?", "what patterns repeat across projects?". Project diaries, decision logs, knowledge extraction — all from data you're already generating.

The raw material has been there all along. Breadcrumbs just makes it accessible.

## Requirements

- Python 3.6+
- Claude Code

No additional packages needed — stdlib only.

## Install

```bash
git clone <this-repo>
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
python3 server.py                    # opens browser automatically
python3 server.py --port 9000        # custom port
python3 server.py --no-open          # don't auto-open browser
```

Features:
- Session list grouped by project, with search and keyboard navigation
- Conversation view with user/assistant messages, collapsible tool calls
- Inline image display with expand/collapse toggle
- Session cost tracking (input, output, cache tokens)
- Editable session names (click the name in the status bar)
- Keyboard: `/` to search, Up/Down to navigate sessions, Escape to blur

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
