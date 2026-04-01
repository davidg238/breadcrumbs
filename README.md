# Breadcrumbs

Records all Claude Code sessions into a SQLite database for cross-project analysis.

Captures prompts, responses, tool usage, and screenshots — everything needed to trace product evolution, bugs, and development patterns across projects.

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
install.py           — sets up hooks in Claude Code settings
uninstall.py         — removes hooks, preserves database
```
