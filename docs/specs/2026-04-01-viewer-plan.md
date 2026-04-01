# Breadcrumbs Viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a single-file Python web server that serves a browser UI for browsing all recorded Claude Code sessions.

**Architecture:** `server.py` uses stdlib `http.server` + `sqlite3`. All HTML/CSS/JS embedded as string constants. REST API serves JSON from `breadcrumbs.db`, frontend is a single-page app that fetches data and renders a sidebar + conversation panel.

**Tech Stack:** Python 3.6+ stdlib only (`http.server`, `sqlite3`, `json`, `argparse`, `webbrowser`)

---

## File Map

| File | Action | Responsibility |
|---|---|---|
| `server.py` | Create | Web server + embedded SPA (all HTML/CSS/JS as string constants) |
| `session_recorder.py` | Modify (lines 21-63, 169-180, 296-312) | Add `usage_json` column to schema + extraction logic |
| `README.md` | Modify | Add viewer usage section |

---

### Task 1: Update session_recorder.py — add usage_json and name columns

**Files:**
- Modify: `session_recorder.py:21-63` (SCHEMA), `session_recorder.py:152-166` (upsert_session), `session_recorder.py:169-180` (upsert_message), `session_recorder.py:296-312` (handle_sync message extraction)

- [ ] **Step 1: Add `name` to sessions and `usage_json` to messages in SCHEMA string**

In `session_recorder.py`, update the SCHEMA constant. Add `name TEXT` after `session_id` in sessions table, add `usage_json TEXT` after `sequence` in messages table:

```python
SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    session_id   TEXT PRIMARY KEY,
    name         TEXT,
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
    usage_json   TEXT,
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
```

- [ ] **Step 2: Update upsert_message to include usage_json parameter**

Replace the `upsert_message` function:

```python
def upsert_message(db, uuid, session_id, parent_uuid, msg_type, role,
                   content_text, tool_name, tool_input, tool_result,
                   model, timestamp, sequence, usage_json=None):
    """Insert or replace a message row (idempotent by UUID)."""
    db.execute("""
        INSERT OR REPLACE INTO messages
            (uuid, session_id, parent_uuid, type, role, content_text,
             tool_name, tool_input, tool_result, model, timestamp, sequence, usage_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, (uuid, session_id, parent_uuid, msg_type, role,
          content_text, tool_name, tool_input, tool_result,
          model, timestamp, sequence, usage_json))
```

- [ ] **Step 3: Extract usage data in handle_sync**

In `handle_sync`, after `model = msg.get("model")` (around line 305), add usage extraction and pass it to upsert_message:

```python
            role = msg.get("role")
            content = msg.get("content", "")
            model = msg.get("model")

            # Extract usage data from assistant messages
            usage = msg.get("usage")
            usage_json_str = json.dumps(usage) if usage else None

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
                usage_json=usage_json_str,
            )
```

- [ ] **Step 4: Test the updated recorder against the real transcript**

```bash
# Delete existing DB to test fresh schema
rm -f ~/.claude/breadcrumbs.db
echo '{"session_id": "2f77fc69-48d0-4c03-bd99-c96e83da1bc3", "cwd": "/home/david/workspace/sqClaude"}' | python3 session_recorder.py sync
# Verify usage_json is populated
sqlite3 ~/.claude/breadcrumbs.db "SELECT uuid, model, usage_json FROM messages WHERE usage_json IS NOT NULL LIMIT 3;"
```

Expected: rows with usage_json containing `{"input_tokens":...}` data.

- [ ] **Step 5: Commit**

```bash
git add session_recorder.py
git commit -m "feat: add usage_json and name columns to recorder schema"
```

---

### Task 2: Build server.py — HTTP server and API layer

**Files:**
- Create: `server.py`

- [ ] **Step 1: Create server.py with argument parsing, DB connection, and schema migration**

```python
#!/usr/bin/env python3
"""Breadcrumbs Viewer — browse Claude Code session history."""

import argparse
import json
import sqlite3
import webbrowser
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse, parse_qs

DB_PATH = Path.home() / ".claude" / "breadcrumbs.db"

MODEL_PRICING = {
    "claude-opus-4-6":   {"input": 15.0, "output": 75.0, "cache_write": 18.75, "cache_read": 1.50},
    "claude-sonnet-4-6": {"input": 3.0,  "output": 15.0, "cache_write": 3.75,  "cache_read": 0.30},
    "claude-haiku-4-5":  {"input": 0.8,  "output": 4.0,  "cache_write": 1.0,   "cache_read": 0.08},
}


def get_db():
    db = sqlite3.connect(str(DB_PATH), timeout=5)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA journal_mode=WAL")
    # Schema migrations — idempotent
    for col, table in [("name", "sessions"), ("usage_json", "messages")]:
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass  # column already exists
    return db


def compute_cost(model, usage):
    pricing = MODEL_PRICING.get(model)
    if not pricing or not usage:
        return None
    return (
        usage.get("input_tokens", 0) * pricing["input"]
        + usage.get("output_tokens", 0) * pricing["output"]
        + usage.get("cache_creation_input_tokens", 0) * pricing["cache_write"]
        + usage.get("cache_read_input_tokens", 0) * pricing["cache_read"]
    ) / 1_000_000


def get_sessions(db):
    rows = db.execute("""
        SELECT s.*,
            COUNT(m.uuid) as message_count,
            MIN(m.timestamp) as first_msg,
            MAX(m.timestamp) as last_msg
        FROM sessions s
        LEFT JOIN messages m ON m.session_id = s.session_id
        GROUP BY s.session_id
        ORDER BY s.started_at DESC
    """).fetchall()
    sessions = []
    for r in rows:
        # Compute token totals and cost from usage_json
        usage_rows = db.execute(
            "SELECT usage_json, model FROM messages WHERE session_id = ? AND usage_json IS NOT NULL",
            (r["session_id"],)
        ).fetchall()
        total_input = total_output = total_cache_write = total_cache_read = 0
        session_model = r["model"]
        for ur in usage_rows:
            u = json.loads(ur["usage_json"])
            total_input += u.get("input_tokens", 0)
            total_output += u.get("output_tokens", 0)
            total_cache_write += u.get("cache_creation_input_tokens", 0)
            total_cache_read += u.get("cache_read_input_tokens", 0)
        cost = compute_cost(session_model, {
            "input_tokens": total_input, "output_tokens": total_output,
            "cache_creation_input_tokens": total_cache_write,
            "cache_read_input_tokens": total_cache_read,
        })
        # Duration
        duration = None
        if r["first_msg"] and r["last_msg"]:
            try:
                from datetime import datetime
                fmt = "%Y-%m-%dT%H:%M:%S"
                t1 = datetime.fromisoformat(r["first_msg"].replace("Z", "+00:00"))
                t2 = datetime.fromisoformat(r["last_msg"].replace("Z", "+00:00"))
                duration = int((t2 - t1).total_seconds())
            except (ValueError, TypeError):
                pass
        # Project display name
        cwd = r["cwd"] or ""
        project_display = cwd.rstrip("/").rsplit("/", 1)[-1] if cwd else r["project"] or ""
        # Session name fallback
        name = r["name"]
        if not name:
            date_str = (r["started_at"] or "")[:10]
            name = f"{project_display} — {date_str}" if project_display else date_str
        sessions.append({
            "session_id": r["session_id"], "name": name,
            "project": project_display, "cwd": cwd,
            "model": session_model, "started_at": r["started_at"],
            "updated_at": r["updated_at"], "git_branch": r["git_branch"],
            "duration_seconds": duration, "message_count": r["message_count"],
            "total_input_tokens": total_input, "total_output_tokens": total_output,
            "total_cache_write_tokens": total_cache_write,
            "total_cache_read_tokens": total_cache_read,
            "estimated_cost_usd": round(cost, 4) if cost is not None else None,
        })
    return sessions


def get_messages(db, session_id):
    rows = db.execute(
        "SELECT * FROM messages WHERE session_id = ? ORDER BY sequence",
        (session_id,)
    ).fetchall()
    messages = []
    for r in rows:
        # Check for images
        img_rows = db.execute(
            "SELECT id FROM message_images WHERE message_uuid = ? ORDER BY image_index",
            (r["uuid"],)
        ).fetchall()
        messages.append({
            "uuid": r["uuid"], "type": r["type"], "role": r["role"],
            "content_text": r["content_text"], "tool_name": r["tool_name"],
            "tool_input": r["tool_input"], "tool_result": r["tool_result"],
            "model": r["model"], "timestamp": r["timestamp"],
            "sequence": r["sequence"], "usage_json": r["usage_json"],
            "has_images": len(img_rows) > 0,
            "image_ids": [ir["id"] for ir in img_rows],
        })
    return messages


def get_image(db, image_id):
    row = db.execute(
        "SELECT media_type, data FROM message_images WHERE id = ?", (image_id,)
    ).fetchone()
    return (row["media_type"], row["data"]) if row else (None, None)


def update_session_name(db, session_id, name):
    db.execute("UPDATE sessions SET name = ? WHERE session_id = ?", (name, session_id))
    db.commit()
```

- [ ] **Step 2: Add the request handler class**

Append to `server.py`:

```python
class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # silence request logging

    def send_json(self, data, status=200):
        body = json.dumps(data).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def send_error_json(self, status, message):
        self.send_json({"error": message}, status)

    def do_GET(self):
        path = urlparse(self.path).path
        if path == "/":
            body = HTML_PAGE.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        elif path == "/api/sessions":
            db = get_db()
            try:
                self.send_json(get_sessions(db))
            finally:
                db.close()
        elif path.startswith("/api/sessions/") and path.endswith("/messages"):
            session_id = path[len("/api/sessions/"):-len("/messages")]
            db = get_db()
            try:
                self.send_json(get_messages(db, session_id))
            finally:
                db.close()
        elif path.startswith("/api/images/"):
            image_id = path[len("/api/images/"):]
            try:
                image_id = int(image_id)
            except ValueError:
                self.send_error_json(400, "Invalid image ID")
                return
            db = get_db()
            try:
                media_type, data = get_image(db, image_id)
                if data is None:
                    self.send_error_json(404, "Image not found")
                else:
                    self.send_response(200)
                    self.send_header("Content-Type", media_type or "image/png")
                    self.send_header("Content-Length", len(data))
                    self.end_headers()
                    self.wfile.write(data)
            finally:
                db.close()
        else:
            self.send_error_json(404, "Not found")

    def do_PATCH(self):
        path = urlparse(self.path).path
        if path.startswith("/api/sessions/") and not path.endswith("/messages"):
            session_id = path[len("/api/sessions/"):]
            try:
                length = int(self.headers.get("Content-Length", 0))
                body = json.loads(self.rfile.read(length))
            except (ValueError, json.JSONDecodeError):
                self.send_error_json(400, "Invalid JSON")
                return
            name = body.get("name")
            if name is None:
                self.send_error_json(400, "Missing 'name' field")
                return
            db = get_db()
            try:
                update_session_name(db, session_id, name)
                self.send_json({"ok": True, "session_id": session_id, "name": name})
            finally:
                db.close()
        else:
            self.send_error_json(404, "Not found")
```

- [ ] **Step 3: Add main function with argparse and startup logic**

Append to `server.py`:

```python
def main():
    parser = argparse.ArgumentParser(description="Breadcrumbs Viewer")
    parser.add_argument("--port", type=int, default=8765, help="Port (default: 8765)")
    parser.add_argument("--no-open", action="store_true", help="Don't open browser")
    args = parser.parse_args()

    if not DB_PATH.exists():
        print(f"Database not found: {DB_PATH}")
        print("Run 'python3 install.py' first to set up breadcrumbs recording.")
        raise SystemExit(1)

    port = args.port
    while True:
        try:
            server = HTTPServer(("127.0.0.1", port), Handler)
            break
        except OSError:
            port += 1
            if port > args.port + 100:
                print("Could not find an open port")
                raise SystemExit(1)

    url = f"http://localhost:{port}"
    print(f"Breadcrumbs viewer: {url}")
    print("Press Ctrl+C to stop")

    if not args.no_open:
        webbrowser.open(url)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
        server.server_close()


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Add a placeholder HTML_PAGE constant (minimal, just to test API)**

Add before the Handler class:

```python
HTML_PAGE = """<!DOCTYPE html>
<html><head><title>Breadcrumbs</title></head>
<body><h1>Breadcrumbs</h1><p>Loading...</p>
<script>
fetch('/api/sessions').then(r=>r.json()).then(d=>{
    document.body.innerHTML='<h1>Breadcrumbs</h1><pre>'+JSON.stringify(d,null,2)+'</pre>';
});
</script></body></html>"""
```

- [ ] **Step 5: Test server starts and API returns data**

```bash
python3 server.py --no-open &
SERVER_PID=$!
sleep 1
curl -s http://localhost:8765/api/sessions | python3 -m json.tool | head -30
curl -s "http://localhost:8765/api/sessions/2f77fc69-48d0-4c03-bd99-c96e83da1bc3/messages" | python3 -c "import sys,json; msgs=json.load(sys.stdin); print(f'{len(msgs)} messages'); print(json.dumps(msgs[0], indent=2))"
kill $SERVER_PID
```

Expected: sessions list with cost data, messages list with usage_json.

- [ ] **Step 6: Commit**

```bash
git add server.py
git commit -m "feat: add server.py with REST API and placeholder UI"
```

---

### Task 3: Build the embedded SPA — HTML structure and CSS

**Files:**
- Modify: `server.py` (replace HTML_PAGE constant)

- [ ] **Step 1: Replace HTML_PAGE with the full SPA shell**

Replace the `HTML_PAGE = ...` constant in `server.py` with the complete HTML document containing:

1. **CSS** — dark theme, sidebar layout, message styling, collapsible sections
2. **HTML** — sidebar with search + session list, main panel with status bar + message list
3. **JS** — will be added in Task 4

The HTML_PAGE string should contain:

```python
HTML_PAGE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Breadcrumbs</title>
<style>
/* Reset and base */
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', system-ui, sans-serif;
       background: #0d1117; color: #c9d1d9; display: flex; height: 100vh; overflow: hidden; }

/* Sidebar */
.sidebar { width: 280px; min-width: 280px; background: #161b22; border-right: 1px solid #30363d;
           display: flex; flex-direction: column; }
.sidebar-header { padding: 12px; border-bottom: 1px solid #30363d; }
.sidebar-header h1 { font-size: 16px; color: #f0f6fc; margin-bottom: 8px; }
.search-box { width: 100%; padding: 6px 10px; background: #0d1117; border: 1px solid #30363d;
              border-radius: 6px; color: #c9d1d9; font-size: 13px; outline: none; }
.search-box:focus { border-color: #58a6ff; }
.session-list { flex: 1; overflow-y: auto; padding: 4px 0; }
.project-group { padding: 4px 12px 2px; }
.project-label { font-size: 11px; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px;
                 margin: 8px 0 4px; }
.session-item { padding: 8px 12px; cursor: pointer; border-left: 3px solid transparent;
                transition: background 0.1s; }
.session-item:hover { background: #1c2128; }
.session-item.active { background: #1c2128; border-left-color: #58a6ff; }
.session-name { font-size: 13px; color: #e6edf3; white-space: nowrap; overflow: hidden;
                text-overflow: ellipsis; }
.session-meta { font-size: 11px; color: #8b949e; margin-top: 2px; }

/* Main panel */
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.status-bar { padding: 8px 16px; background: #161b22; border-bottom: 1px solid #30363d;
              display: flex; align-items: center; gap: 16px; font-size: 12px; color: #8b949e;
              flex-wrap: wrap; }
.status-bar .session-title { color: #f0f6fc; font-size: 14px; font-weight: 600; cursor: pointer; }
.status-bar .session-title:hover { color: #58a6ff; }
.status-bar .edit-name { background: #0d1117; border: 1px solid #58a6ff; color: #f0f6fc;
                         font-size: 14px; padding: 2px 6px; border-radius: 4px; outline: none; }
.status-bar .stat { display: flex; align-items: center; gap: 4px; }
.status-bar .cost { color: #3fb950; }
.status-bar .toggle-btn { margin-left: auto; background: #21262d; border: 1px solid #30363d;
                          color: #8b949e; padding: 3px 8px; border-radius: 4px; cursor: pointer;
                          font-size: 11px; }
.status-bar .toggle-btn:hover { color: #c9d1d9; border-color: #8b949e; }
.messages { flex: 1; overflow-y: auto; padding: 16px; }
.empty-state { display: flex; align-items: center; justify-content: center; height: 100%;
               color: #8b949e; font-size: 14px; }

/* Messages */
.msg { margin-bottom: 12px; padding: 10px 14px; border-radius: 8px; max-width: 100%; }
.msg-user { background: #1c2128; border-left: 3px solid #58a6ff; }
.msg-assistant { background: #161b22; border-left: 3px solid #a371f7; }
.msg-system { background: #161b22; opacity: 0.5; font-size: 12px; }
.msg-tool { background: #1c1e24; border-left: 3px solid #f0883e; font-size: 12px; }
.msg-header { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.msg-role { font-size: 12px; font-weight: 600; }
.msg-role-user { color: #58a6ff; }
.msg-role-assistant { color: #a371f7; }
.msg-role-system { color: #8b949e; }
.msg-role-tool { color: #f0883e; }
.msg-time { font-size: 11px; color: #484f58; }
.msg-model { font-size: 11px; color: #484f58; font-style: italic; }
.msg-body { font-size: 13px; line-height: 1.5; white-space: pre-wrap; word-break: break-word; }

/* Collapsible */
.collapsible { cursor: pointer; user-select: none; }
.collapsible::before { content: '\\25B6'; display: inline-block; margin-right: 6px; font-size: 10px;
                       transition: transform 0.15s; }
.collapsible.open::before { transform: rotate(90deg); }
.collapsible-body { display: none; margin-top: 6px; }
.collapsible.open + .collapsible-body { display: block; }

/* Tool details */
.tool-detail { background: #0d1117; padding: 8px 10px; border-radius: 4px; margin-top: 4px;
               font-family: 'SF Mono', 'Fira Code', monospace; font-size: 12px;
               overflow-x: auto; white-space: pre-wrap; word-break: break-word;
               max-height: 400px; overflow-y: auto; }

/* Images */
.msg-image { max-width: 600px; border-radius: 4px; margin-top: 6px; }

/* Markdown in assistant messages */
.md h1, .md h2, .md h3 { color: #f0f6fc; margin: 10px 0 6px; }
.md h1 { font-size: 18px; } .md h2 { font-size: 16px; } .md h3 { font-size: 14px; }
.md p { margin: 4px 0; }
.md code { background: #1c2128; padding: 2px 5px; border-radius: 3px; font-size: 12px;
           font-family: 'SF Mono', 'Fira Code', monospace; }
.md pre { background: #0d1117; padding: 10px; border-radius: 6px; margin: 6px 0;
          overflow-x: auto; }
.md pre code { background: none; padding: 0; }
.md ul, .md ol { margin: 4px 0; padding-left: 24px; }
.md li { margin: 2px 0; }
.md a { color: #58a6ff; text-decoration: none; }
.md a:hover { text-decoration: underline; }
.md strong { color: #e6edf3; }
.md table { border-collapse: collapse; margin: 6px 0; }
.md th, .md td { border: 1px solid #30363d; padding: 4px 8px; font-size: 12px; }
.md th { background: #161b22; }
.md blockquote { border-left: 3px solid #30363d; padding-left: 12px; color: #8b949e; margin: 4px 0; }

/* Scrollbar */
::-webkit-scrollbar { width: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #30363d; border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: #484f58; }
</style>
</head>
<body>
<div class="sidebar">
  <div class="sidebar-header">
    <h1>Breadcrumbs</h1>
    <input type="text" class="search-box" id="search" placeholder="Search sessions... (/)">
  </div>
  <div class="session-list" id="sessionList"></div>
</div>
<div class="main">
  <div class="status-bar" id="statusBar" style="display:none;"></div>
  <div class="messages" id="messages">
    <div class="empty-state">Select a session to view</div>
  </div>
</div>
<script>
// JS will go here — see Task 4
</script>
</body>
</html>"""
```

- [ ] **Step 2: Verify the page loads with correct layout**

```bash
python3 server.py --no-open &
SERVER_PID=$!
sleep 1
curl -s http://localhost:8765/ | head -20
kill $SERVER_PID
```

Expected: HTML starting with `<!DOCTYPE html>` containing sidebar and main layout.

- [ ] **Step 3: Commit**

```bash
git add server.py
git commit -m "feat: add embedded SPA shell with CSS theme"
```

---

### Task 4: Build the embedded SPA — JavaScript

**Files:**
- Modify: `server.py` (replace the `// JS will go here` comment in HTML_PAGE)

- [ ] **Step 1: Add state management and API fetch functions**

Replace `// JS will go here — see Task 4` with:

```javascript
let sessions = [];
let currentSessionId = null;
let imagesExpanded = true;

async function fetchSessions() {
  const res = await fetch('/api/sessions');
  sessions = await res.json();
  renderSidebar();
}

async function fetchMessages(sessionId) {
  const res = await fetch(`/api/sessions/${sessionId}/messages`);
  return res.json();
}

async function renameSession(sessionId, name) {
  await fetch(`/api/sessions/${sessionId}`, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name})
  });
  await fetchSessions();
}
```

- [ ] **Step 2: Add sidebar rendering with search and project grouping**

```javascript
function renderSidebar(filter = '') {
  const list = document.getElementById('sessionList');
  const lc = filter.toLowerCase();
  const filtered = sessions.filter(s =>
    !lc || s.name.toLowerCase().includes(lc)
        || (s.project || '').toLowerCase().includes(lc)
        || (s.cwd || '').toLowerCase().includes(lc)
  );
  // Group by project
  const groups = {};
  for (const s of filtered) {
    const proj = s.project || 'unknown';
    if (!groups[proj]) groups[proj] = [];
    groups[proj].push(s);
  }
  let html = '';
  for (const [proj, items] of Object.entries(groups)) {
    html += `<div class="project-group"><div class="project-label">${esc(proj)}</div>`;
    for (const s of items) {
      const active = s.session_id === currentSessionId ? ' active' : '';
      const dur = formatDuration(s.duration_seconds);
      const cost = s.estimated_cost_usd != null ? `$${s.estimated_cost_usd.toFixed(2)}` : '';
      html += `<div class="session-item${active}" data-id="${esc(s.session_id)}" onclick="selectSession('${esc(s.session_id)}')">
        <div class="session-name">${esc(s.name)}</div>
        <div class="session-meta">${s.message_count} msgs · ${dur}${cost ? ' · ' + cost : ''}</div>
      </div>`;
    }
    html += '</div>';
  }
  list.innerHTML = html || '<div style="padding:12px;color:#8b949e;">No sessions found</div>';
}

document.getElementById('search').addEventListener('input', e => renderSidebar(e.target.value));
```

- [ ] **Step 3: Add session selection and status bar rendering**

```javascript
async function selectSession(sessionId) {
  currentSessionId = sessionId;
  const s = sessions.find(s => s.session_id === sessionId);
  if (!s) return;

  // Update sidebar active state
  document.querySelectorAll('.session-item').forEach(el => {
    el.classList.toggle('active', el.dataset.id === sessionId);
  });

  // Status bar
  const bar = document.getElementById('statusBar');
  bar.style.display = 'flex';
  const dur = formatDuration(s.duration_seconds);
  const started = s.started_at ? new Date(s.started_at).toLocaleString() : '';
  const tokens = `${fmtNum(s.total_input_tokens)} in / ${fmtNum(s.total_output_tokens)} out`;
  const cost = s.estimated_cost_usd != null ? `$${s.estimated_cost_usd.toFixed(2)}` : '?';
  bar.innerHTML = `
    <span class="session-title" id="sessionTitle" onclick="startEditName()">${esc(s.name)}</span>
    <span class="stat">${esc(s.model || '')}</span>
    <span class="stat">${dur}</span>
    <span class="stat">${started}</span>
    <span class="stat">${tokens}</span>
    <span class="stat cost">${cost}</span>
    <button class="toggle-btn" onclick="toggleImageDefault()">
      Images: ${imagesExpanded ? 'expanded' : 'collapsed'}
    </button>`;

  // Fetch and render messages
  const msgs = await fetchMessages(sessionId);
  renderMessages(msgs);
}

function startEditName() {
  const s = sessions.find(s => s.session_id === currentSessionId);
  if (!s) return;
  const el = document.getElementById('sessionTitle');
  const input = document.createElement('input');
  input.className = 'edit-name';
  input.value = s.name;
  input.onkeydown = async (e) => {
    if (e.key === 'Enter') { await renameSession(currentSessionId, input.value); selectSession(currentSessionId); }
    if (e.key === 'Escape') selectSession(currentSessionId);
  };
  input.onblur = () => selectSession(currentSessionId);
  el.replaceWith(input);
  input.focus();
  input.select();
}

function toggleImageDefault() {
  imagesExpanded = !imagesExpanded;
  if (currentSessionId) selectSession(currentSessionId);
}
```

- [ ] **Step 4: Add message rendering with collapsible tool calls and images**

```javascript
function renderMessages(msgs) {
  const container = document.getElementById('messages');
  let html = '';
  for (const m of msgs) {
    if (m.type === 'file-history-snapshot') continue;

    // Tool call message
    if (m.tool_name) {
      const preview = (m.tool_input || '').split('\\n')[0].slice(0, 80);
      html += `<div class="msg msg-tool">
        <div class="msg-header">
          <span class="collapsible msg-role msg-role-tool" onclick="toggleCollapse(this)">Tool: ${esc(m.tool_name)}</span>
          <span class="msg-time">${esc(preview)}</span>
        </div>
        <div class="collapsible-body">
          ${m.tool_input ? '<div class="tool-detail">' + esc(m.tool_input) + '</div>' : ''}
          ${m.tool_result ? '<div class="tool-detail" style="margin-top:4px;">' + esc(m.tool_result) + '</div>' : ''}
        </div>
      </div>`;
      continue;
    }

    // System message
    if (m.type === 'system') {
      html += `<div class="msg msg-system">
        <div class="collapsible msg-role msg-role-system" onclick="toggleCollapse(this)">System</div>
        <div class="collapsible-body"><div class="msg-body">${esc(m.content_text || '')}</div></div>
      </div>`;
      continue;
    }

    // User / Assistant
    const isUser = m.role === 'user';
    const cls = isUser ? 'msg-user' : 'msg-assistant';
    const roleCls = isUser ? 'msg-role-user' : 'msg-role-assistant';
    const label = isUser ? 'You' : 'Claude';
    const time = m.timestamp ? new Date(m.timestamp).toLocaleTimeString() : '';
    const modelTag = (!isUser && m.model) ? `<span class="msg-model">${esc(m.model)}</span>` : '';
    const body = isUser ? esc(m.content_text || '') : renderMarkdown(m.content_text || '');

    let imgHtml = '';
    if (m.has_images && m.image_ids) {
      for (const imgId of m.image_ids) {
        const expanded = imagesExpanded;
        imgHtml += `<div>
          <span class="collapsible${expanded ? ' open' : ''}" onclick="toggleCollapse(this)">Image</span>
          <div class="collapsible-body"${expanded ? ' style="display:block"' : ''}>
            <img class="msg-image" src="/api/images/${imgId}" loading="lazy">
          </div>
        </div>`;
      }
    }

    html += `<div class="msg ${cls}">
      <div class="msg-header">
        <span class="msg-role ${roleCls}">${label}</span>
        <span class="msg-time">${time}</span>
        ${modelTag}
      </div>
      <div class="msg-body${isUser ? '' : ' md'}">${body}</div>
      ${imgHtml}
    </div>`;
  }
  container.innerHTML = html || '<div class="empty-state">No messages</div>';
  container.scrollTop = 0;
}

function toggleCollapse(el) {
  el.classList.toggle('open');
  const body = el.closest('.msg')
    ? el.parentElement.querySelector('.collapsible-body') || el.nextElementSibling
    : el.nextElementSibling;
  if (body) body.style.display = body.style.display === 'block' ? 'none' : 'block';
}
```

- [ ] **Step 5: Add minimal markdown renderer and utility functions**

```javascript
function renderMarkdown(text) {
  if (!text) return '';
  let html = esc(text);
  // Code blocks
  html = html.replace(/```(\\w*)\\n([\\s\\S]*?)```/g, '<pre><code>$2</code></pre>');
  // Inline code
  html = html.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Headers
  html = html.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  html = html.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  html = html.replace(/^# (.+)$/gm, '<h1>$1</h1>');
  // Bold and italic
  html = html.replace(/\\*\\*(.+?)\\*\\*/g, '<strong>$1</strong>');
  html = html.replace(/\\*(.+?)\\*/g, '<em>$1</em>');
  // Links
  html = html.replace(/\\[([^\\]]+)\\]\\(([^)]+)\\)/g, '<a href="$2" target="_blank">$1</a>');
  // Unordered lists
  html = html.replace(/^[\\-\\*] (.+)$/gm, '<li>$1</li>');
  html = html.replace(/(<li>.*<\\/li>\\n?)+/g, '<ul>$&</ul>');
  // Ordered lists
  html = html.replace(/^\\d+\\. (.+)$/gm, '<li>$1</li>');
  // Blockquotes
  html = html.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');
  // Paragraphs (double newline)
  html = html.replace(/\\n\\n/g, '</p><p>');
  return '<p>' + html + '</p>';
}

function esc(s) {
  if (!s) return '';
  return s.replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function formatDuration(s) {
  if (s == null) return '';
  if (s < 60) return s + 's';
  if (s < 3600) return Math.floor(s / 60) + 'min';
  return Math.floor(s / 3600) + 'h ' + Math.floor((s % 3600) / 60) + 'min';
}

function fmtNum(n) {
  if (n == null) return '0';
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return n.toString();
}

// Keyboard shortcuts
document.addEventListener('keydown', e => {
  if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {
    e.preventDefault();
    document.getElementById('search').focus();
  }
  if (e.key === 'ArrowDown' || e.key === 'ArrowUp') {
    if (document.activeElement.tagName === 'INPUT') return;
    e.preventDefault();
    const idx = sessions.findIndex(s => s.session_id === currentSessionId);
    const next = e.key === 'ArrowDown' ? idx + 1 : idx - 1;
    if (next >= 0 && next < sessions.length) selectSession(sessions[next].session_id);
  }
  if (e.key === 'Escape') {
    document.getElementById('search').blur();
  }
});

// Boot
fetchSessions();
```

- [ ] **Step 6: Test full UI in browser**

```bash
python3 server.py &
SERVER_PID=$!
sleep 1
echo "Open http://localhost:8765 in browser, verify:"
echo "  - Sidebar shows sessions grouped by project"
echo "  - Clicking a session shows messages"
echo "  - Tool calls are collapsed with twisty"
echo "  - Status bar shows model, duration, cost"
echo "  - Session name click enables editing"
echo "  - '/' focuses search, Up/Down navigate sessions"
kill $SERVER_PID
```

- [ ] **Step 7: Commit**

```bash
git add server.py
git commit -m "feat: add full SPA with session browser, markdown, and keyboard nav"
```

---

### Task 5: Update README

**Files:**
- Modify: `README.md`

- [ ] **Step 1: Add viewer section to README**

After the "Querying" section, add:

```markdown
## Viewer

Browse sessions in your browser:

\```bash
python3 server.py                    # opens browser automatically
python3 server.py --port 9000        # custom port
python3 server.py --no-open          # don't auto-open browser
\```

Features:
- Session list grouped by project, with search and keyboard navigation
- Conversation view with user/assistant messages, collapsible tool calls
- Inline image display with expand/collapse toggle
- Session cost tracking (input, output, cache tokens)
- Editable session names (click the name in the status bar)
- Keyboard: `/` to search, Up/Down to navigate sessions, Escape to blur
```

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: add viewer usage to README"
```

---

## Self-Review

**Spec coverage check:**
- API endpoints (GET sessions, PATCH name, GET messages, GET images) — Task 2 ✓
- Schema changes (name, usage_json) — Task 1 ✓
- Sidebar layout with search and project grouping — Task 4 step 2 ✓
- Status bar with editable name, model, duration, cost, image toggle — Task 4 step 3 ✓
- Message rendering (user, assistant, tool, system, images) — Task 4 step 4 ✓
- Tool calls collapsed by default — Task 4 step 4 ✓
- Images expanded by default with toggle — Task 4 step 4 ✓
- System messages collapsed — Task 4 step 4 ✓
- Markdown rendering — Task 4 step 5 ✓
- Cost calculation — Task 2 step 1 ✓
- Keyboard shortcuts — Task 4 step 5 ✓
- Startup args (--port, --no-open) — Task 2 step 3 ✓
- Port fallback — Task 2 step 3 ✓
- README update — Task 5 ✓
- MCP endpoint — spec says "not built in v1" ✓ (excluded correctly)

**Placeholder scan:** No TBD/TODO. All code blocks complete.

**Type consistency:** `get_sessions` returns objects matching the spec JSON. `get_messages` returns objects with `image_ids` array. `renderMessages` consumes both correctly. `usage_json` column name consistent across recorder and server.
