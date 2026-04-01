# Breadcrumbs — Claude Code Session Recorder

**Date:** 2026-04-01
**Status:** Approved

## Purpose

Record all Claude Code sessions and their complete message history into a SQLite database for cross-project analysis. The goal is to capture the full development trail — prompts, responses, tool usage, screenshots — so that patterns, product evolution, and bug history can be distilled across many projects over time.

## Architecture

One Python script (`session_recorder.py`), zero dependencies beyond Python 3 stdlib. Two Claude Code hooks invoke it. One SQLite database stores everything.

```
Hooks (settings.json)
  ├── UserPromptSubmit → session_recorder.py prompt   (crash insurance)
  └── Stop             → session_recorder.py sync     (full transcript sync)

Storage
  └── ~/.claude/breadcrumbs.db
```

### Hook: `prompt` (UserPromptSubmit)

- Reads hook JSON from stdin
- Upserts session row
- Inserts user message immediately
- Purpose: if Claude crashes mid-response, the prompt is already recorded
- Target: ~10ms execution

### Hook: `sync` (Stop)

- Reads hook JSON from stdin (includes `transcript_path`)
- Reads the full transcript JSONL file
- Upserts session with latest metadata
- For each message: upsert by UUID (idempotent)
- Extracts base64 images into `message_images` table
- All writes in a single transaction

Idempotency guarantee: the Stop hook re-syncs the full transcript every turn. Messages matched by UUID are skipped. The UserPromptSubmit insert and Stop sync cannot conflict because they use the same UUID as primary key with INSERT OR REPLACE.

## Schema

```sql
CREATE TABLE sessions (
    session_id   TEXT PRIMARY KEY,
    project      TEXT,
    cwd          TEXT,
    model        TEXT,
    started_at   TEXT,
    updated_at   TEXT,
    git_branch   TEXT,
    version      TEXT
);

CREATE TABLE messages (
    uuid         TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    parent_uuid  TEXT,
    type         TEXT NOT NULL,  -- user, assistant, system, file-history-snapshot
    role         TEXT,
    content_text TEXT,           -- extracted plain text only
    tool_name    TEXT,           -- if tool_use or tool_result
    tool_input   TEXT,           -- JSON string
    tool_result  TEXT,           -- JSON string
    model        TEXT,
    timestamp    TEXT NOT NULL,
    sequence     INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE message_images (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    message_uuid TEXT NOT NULL,
    image_index  INTEGER NOT NULL,
    media_type   TEXT,
    data         BLOB NOT NULL,
    hash         TEXT,
    UNIQUE(message_uuid, image_index),
    FOREIGN KEY (message_uuid) REFERENCES messages(uuid)
);

CREATE INDEX idx_messages_session ON messages(session_id, sequence);
CREATE INDEX idx_messages_type ON messages(type);
CREATE INDEX idx_messages_timestamp ON messages(timestamp);
CREATE INDEX idx_images_hash ON message_images(hash);
```

## Data Extraction

### From transcript JSONL messages

Each line is a JSON object. Key fields extracted:

| JSONL field | DB column |
|---|---|
| `uuid` | `messages.uuid` (PK) |
| `parentUuid` | `messages.parent_uuid` |
| `type` | `messages.type` |
| `sessionId` | `messages.session_id` |
| `timestamp` | `messages.timestamp` |
| `message.role` | `messages.role` |
| `message.content` (text blocks) | `messages.content_text` |
| `message.content` (tool_use blocks) | `messages.tool_name`, `messages.tool_input` |
| `message.content` (tool_result blocks) | `messages.tool_result` |
| `message.content` (image blocks) | `message_images.data` (decoded from base64) |
| `message.model` | `messages.model` |

### Session metadata

Extracted from the first message in the transcript that contains session-level fields (`sessionId`, `cwd`, `version`, `gitBranch`).

## Error Handling

- All DB writes in a single transaction per invocation
- On any error: rollback, log to stderr, exit 0
- Hooks MUST exit 0 — non-zero would block Claude Code
- DB and tables created on first run via `CREATE TABLE IF NOT EXISTS`
- WAL mode enabled for concurrent read safety

## Installation

`install.py` script that:
1. Copies `session_recorder.py` to `~/.claude/hooks/`
2. Reads `~/.claude/settings.json`
3. Adds hook entries (preserving existing hooks)
4. Writes updated settings

`uninstall.py` reverses the process.

## File Layout

```
breadcrumbs/
  session_recorder.py    -- the recording script
  install.py             -- copies script + patches settings.json
  uninstall.py           -- reverses install
  README.md              -- usage instructions
  docs/specs/            -- this file
```

## Non-Goals

- No background processes or daemons
- No config files beyond settings.json hook entries
- No automatic cleanup/rotation
- No network calls
- No dependencies beyond Python 3 stdlib
- No analysis tooling (that's a separate future concern)
