#!/usr/bin/env python3
"""Breadcrumbs — Claude Code session recorder.

Records all Claude Code sessions into a SQLite database.
Invoked by Claude Code hooks:
  - UserPromptSubmit: session_recorder.py prompt
  - Stop:             session_recorder.py sync
"""

import base64
import hashlib
import json
import os
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path.home() / ".claude" / "breadcrumbs.db"
CLAUDE_DIR = Path.home() / ".claude"

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    project      TEXT,
    cwd          TEXT,
    model        TEXT,
    started_at   TEXT,
    updated_at   TEXT,
    git_branch   TEXT,
    version      TEXT
);

CREATE TABLE IF NOT EXISTS messages (
    uuid         TEXT PRIMARY KEY,
    session_id   TEXT NOT NULL,
    parent_uuid  TEXT,
    type         TEXT NOT NULL,
    role         TEXT,
    content_text TEXT,
    tool_name    TEXT,
    tool_input   TEXT,
    tool_result  TEXT,
    model        TEXT,
    timestamp    TEXT NOT NULL,
    sequence     INTEGER,
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE TABLE IF NOT EXISTS message_images (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    message_uuid TEXT NOT NULL,
    image_index  INTEGER NOT NULL,
    media_type   TEXT,
    data         BLOB NOT NULL,
    hash         TEXT,
    UNIQUE(message_uuid, image_index),
    FOREIGN KEY (message_uuid) REFERENCES messages(uuid)
);

CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, sequence);
CREATE INDEX IF NOT EXISTS idx_messages_type ON messages(type);
CREATE INDEX IF NOT EXISTS idx_messages_timestamp ON messages(timestamp);
CREATE INDEX IF NOT EXISTS idx_images_hash ON message_images(hash);
"""


def get_db():
    """Open (and initialize if needed) the database."""
    db = sqlite3.connect(str(DB_PATH), timeout=5)
    db.execute("PRAGMA journal_mode=WAL")
    db.execute("PRAGMA foreign_keys=ON")
    db.executescript(SCHEMA)
    return db


def transcript_path(session_id, cwd):
    """Derive the transcript JSONL path from session_id and cwd."""
    slug = cwd.replace("/", "-")
    return CLAUDE_DIR / "projects" / slug / f"{session_id}.jsonl"


def extract_text(content):
    """Extract plain text from a message's content field."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, dict):
                if block.get("type") == "text":
                    parts.append(block.get("text", ""))
                elif block.get("type") == "tool_use":
                    pass  # handled separately
                elif block.get("type") == "tool_result":
                    # Extract text from tool result content
                    result_content = block.get("content", "")
                    if isinstance(result_content, str):
                        parts.append(result_content)
                    elif isinstance(result_content, list):
                        for rb in result_content:
                            if isinstance(rb, dict) and rb.get("type") == "text":
                                parts.append(rb.get("text", ""))
        return "\n".join(parts) if parts else None
    return None


def extract_tool_use(content):
    """Extract first tool_use block from content."""
    if not isinstance(content, list):
        return None, None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return block.get("name"), json.dumps(block.get("input", {}))
    return None, None


def extract_tool_result(content):
    """Extract tool_result text from content."""
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_result":
            rc = block.get("content", "")
            if isinstance(rc, str):
                return rc
            if isinstance(rc, list):
                parts = []
                for rb in rc:
                    if isinstance(rb, dict) and rb.get("type") == "text":
                        parts.append(rb.get("text", ""))
                return "\n".join(parts) if parts else None
    return None


def extract_images(content):
    """Extract base64 images from content blocks. Yields (index, media_type, raw_bytes, hash)."""
    if not isinstance(content, list):
        return
    img_idx = 0
    for block in content:
        if isinstance(block, dict) and block.get("type") == "image":
            source = block.get("source", {})
            if source.get("type") == "base64":
                raw = base64.b64decode(source.get("data", ""))
                h = hashlib.sha256(raw).hexdigest()
                yield (img_idx, source.get("media_type", "image/png"), raw, h)
                img_idx += 1


def upsert_session(db, session_id, cwd, project=None, model=None,
                   started_at=None, updated_at=None, git_branch=None, version=None):
    """Insert or update a session row."""
    db.execute("""
        INSERT INTO sessions (session_id, project, cwd, model, started_at, updated_at, git_branch, version)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(session_id) DO UPDATE SET
            project    = COALESCE(excluded.project, sessions.project),
            cwd        = COALESCE(excluded.cwd, sessions.cwd),
            model      = COALESCE(excluded.model, sessions.model),
            started_at = COALESCE(sessions.started_at, excluded.started_at),
            updated_at = COALESCE(excluded.updated_at, sessions.updated_at),
            git_branch = COALESCE(excluded.git_branch, sessions.git_branch),
            version    = COALESCE(excluded.version, sessions.version)
    """, (session_id, project, cwd, model, started_at, updated_at, git_branch, version))


def upsert_message(db, uuid, session_id, parent_uuid, msg_type, role,
                   content_text, tool_name, tool_input, tool_result,
                   model, timestamp, sequence):
    """Insert or ignore a message row (idempotent by UUID)."""
    db.execute("""
        INSERT OR REPLACE INTO messages
            (uuid, session_id, parent_uuid, type, role, content_text,
             tool_name, tool_input, tool_result, model, timestamp, sequence)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (uuid, session_id, parent_uuid, msg_type, role,
          content_text, tool_name, tool_input, tool_result,
          model, timestamp, sequence))


def upsert_image(db, message_uuid, image_index, media_type, data, img_hash):
    """Insert or ignore an image."""
    db.execute("""
        INSERT OR IGNORE INTO message_images (message_uuid, image_index, media_type, data, hash)
        VALUES (?, ?, ?, ?, ?)
    """, (message_uuid, image_index, media_type, data, img_hash))


def handle_prompt(hook_input):
    """Handle UserPromptSubmit: record user prompt immediately."""
    session_id = hook_input.get("session_id")
    cwd = hook_input.get("cwd", "")
    prompt = hook_input.get("prompt", "")

    if not session_id:
        return

    db = get_db()
    try:
        project = cwd.replace("/", "-")
        now = hook_input.get("timestamp") or ""

        upsert_session(db, session_id, cwd, project=project, updated_at=now)

        # Generate a UUID for this prompt message
        # Use a deterministic ID so the Stop sync can overwrite it cleanly
        prompt_uuid = hashlib.sha256(
            f"{session_id}:prompt:{prompt[:200]}".encode()
        ).hexdigest()[:36]

        upsert_message(
            db, prompt_uuid, session_id,
            parent_uuid=None,
            msg_type="user",
            role="user",
            content_text=prompt,
            tool_name=None, tool_input=None, tool_result=None,
            model=None,
            timestamp=now,
            sequence=None,
        )
        db.commit()
    except Exception as e:
        db.rollback()
        print(f"breadcrumbs: prompt error: {e}", file=sys.stderr)
    finally:
        db.close()


def handle_sync(hook_input):
    """Handle Stop: sync full transcript to database."""
    session_id = hook_input.get("session_id")
    cwd = hook_input.get("cwd", "")

    if not session_id:
        return

    tp = transcript_path(session_id, cwd)
    if not tp.exists():
        print(f"breadcrumbs: transcript not found: {tp}", file=sys.stderr)
        return

    db = get_db()
    try:
        lines = tp.read_text().splitlines()

        sequence = 0
        session_meta_set = False

        for line in lines:
            line = line.strip()
            if not line:
                continue

            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                continue

            entry_type = entry.get("type", "")
            uuid = entry.get("uuid")

            # Skip non-message entries (like file-history-snapshot) that lack a uuid
            if not uuid:
                continue

            # Extract session metadata from first message with session fields
            if not session_meta_set and entry.get("sessionId"):
                msg_obj = entry.get("message", {})
                model = msg_obj.get("model") if isinstance(msg_obj, dict) else None
                upsert_session(
                    db, session_id, cwd,
                    project=cwd.replace("/", "-"),
                    model=model,
                    started_at=entry.get("timestamp"),
                    git_branch=entry.get("gitBranch"),
                    version=entry.get("version"),
                )
                session_meta_set = True

            # Update session timestamp to latest
            ts = entry.get("timestamp", "")
            upsert_session(db, session_id, cwd, updated_at=ts)

            # Parse the message object
            msg = entry.get("message", {})
            if not isinstance(msg, dict):
                msg = {}

            role = msg.get("role")
            content = msg.get("content", "")
            model = msg.get("model")

            content_text = extract_text(content)
            tool_name, tool_input = extract_tool_use(content)
            tool_result = extract_tool_result(content)

            upsert_message(
                db, uuid, session_id,
                parent_uuid=entry.get("parentUuid"),
                msg_type=entry_type,
                role=role,
                content_text=content_text,
                tool_name=tool_name,
                tool_input=tool_input,
                tool_result=tool_result,
                model=model,
                timestamp=ts,
                sequence=sequence,
            )

            # Extract images
            for img_idx, media_type, raw_bytes, img_hash in extract_images(content):
                upsert_image(db, uuid, img_idx, media_type, raw_bytes, img_hash)

            sequence += 1

        db.commit()
    except Exception as e:
        db.rollback()
        print(f"breadcrumbs: sync error: {e}", file=sys.stderr)
    finally:
        db.close()


def main():
    if len(sys.argv) < 2:
        print("Usage: session_recorder.py [prompt|sync]", file=sys.stderr)
        sys.exit(0)

    command = sys.argv[1]

    # Read hook input from stdin
    try:
        hook_input = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        hook_input = {}

    if command == "prompt":
        handle_prompt(hook_input)
    elif command == "sync":
        handle_sync(hook_input)
    else:
        print(f"breadcrumbs: unknown command: {command}", file=sys.stderr)

    # Always exit 0 so we never block Claude Code
    sys.exit(0)


if __name__ == "__main__":
    main()
