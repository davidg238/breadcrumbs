#!/usr/bin/env python3
"""Breadcrumbs Viewer — browse Claude Code session history."""

import argparse
import json
import sqlite3
import webbrowser
from datetime import datetime
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

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
    for col, table in [("name", "sessions"), ("usage_json", "messages")]:
        try:
            db.execute(f"ALTER TABLE {table} ADD COLUMN {col} TEXT")
        except sqlite3.OperationalError:
            pass
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
        SELECT s.*, COUNT(m.uuid) as message_count,
            MIN(m.timestamp) as first_msg, MAX(m.timestamp) as last_msg
        FROM sessions s
        LEFT JOIN messages m ON m.session_id = s.session_id
        GROUP BY s.session_id ORDER BY s.started_at DESC
    """).fetchall()
    sessions = []
    for r in rows:
        usage_rows = db.execute(
            "SELECT usage_json, model FROM messages WHERE session_id = ? AND usage_json IS NOT NULL",
            (r["session_id"],)).fetchall()
        total_input = total_output = total_cache_write = total_cache_read = 0
        session_model = r["model"]
        for ur in usage_rows:
            if not session_model and ur["model"]:
                session_model = ur["model"]
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
        duration = None
        if r["first_msg"] and r["last_msg"]:
            try:
                t1 = datetime.fromisoformat(r["first_msg"].replace("Z", "+00:00"))
                t2 = datetime.fromisoformat(r["last_msg"].replace("Z", "+00:00"))
                duration = int((t2 - t1).total_seconds())
            except (ValueError, TypeError):
                pass
        cwd = r["cwd"] or ""
        project_display = cwd.rstrip("/").rsplit("/", 1)[-1] if cwd else r["project"] or ""
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
        "SELECT * FROM messages WHERE session_id = ? ORDER BY sequence", (session_id,)).fetchall()
    messages = []
    for r in rows:
        img_rows = db.execute(
            "SELECT id FROM message_images WHERE message_uuid = ? ORDER BY image_index",
            (r["uuid"],)).fetchall()
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
    row = db.execute("SELECT media_type, data FROM message_images WHERE id = ?", (image_id,)).fetchone()
    return (row["media_type"], row["data"]) if row else (None, None)


def update_session_name(db, session_id, name):
    db.execute("UPDATE sessions SET name = ? WHERE session_id = ?", (name, session_id))
    db.commit()


HTML_PAGE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Breadcrumbs</title>
<style>
*, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
body { background: #0d1117; color: #e6edf3; font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Helvetica, Arial, sans-serif; display: flex; height: 100vh; overflow: hidden; }
a { color: #58a6ff; text-decoration: none; }
a:hover { text-decoration: underline; }

/* Sidebar */
.sidebar { width: 280px; min-width: 280px; background: #161b22; border-right: 1px solid #30363d; display: flex; flex-direction: column; overflow: hidden; }
.sidebar-header { padding: 16px; border-bottom: 1px solid #30363d; }
.sidebar-header h1 { font-size: 16px; font-weight: 600; margin-bottom: 10px; color: #e6edf3; }
.project-filter { width: 100%; padding: 6px 10px; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; color: #e6edf3; font-size: 13px; outline: none; margin-bottom: 6px; cursor: pointer; }
.project-filter:focus { border-color: #58a6ff; }
.search-box { width: 100%; padding: 6px 10px; background: #0d1117; border: 1px solid #30363d; border-radius: 6px; color: #e6edf3; font-size: 13px; outline: none; }
.search-box:focus { border-color: #58a6ff; box-shadow: 0 0 0 2px rgba(88,166,255,0.15); }
.search-box::placeholder { color: #484f58; }

.session-list { flex: 1; overflow-y: auto; }
.session-group-label { padding: 8px 16px 4px; font-size: 11px; font-weight: 600; color: #8b949e; text-transform: uppercase; letter-spacing: 0.5px; }
.session-item { padding: 8px 16px; cursor: pointer; border-left: 3px solid transparent; transition: background 0.1s; }
.session-item:hover { background: #1c2128; }
.session-item.active { border-left-color: #58a6ff; background: #1c2128; }
.session-item .session-name { font-size: 13px; font-weight: 500; color: #e6edf3; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.session-item .session-meta { font-size: 11px; color: #8b949e; margin-top: 2px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }

/* Main area */
.main { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
.status-bar { background: #161b22; border-bottom: 1px solid #30363d; padding: 8px 16px; display: flex; align-items: center; gap: 16px; font-size: 12px; color: #8b949e; flex-wrap: wrap; min-height: 40px; }
.status-bar .session-title { font-weight: 600; color: #e6edf3; font-size: 14px; cursor: pointer; }
.status-bar .session-title:hover { color: #58a6ff; }
.status-bar .stat { display: flex; align-items: center; gap: 4px; }
.status-bar .stat-label { color: #484f58; }
.status-bar .btn { background: #21262d; border: 1px solid #30363d; color: #8b949e; padding: 2px 8px; border-radius: 4px; cursor: pointer; font-size: 11px; }
.status-bar .btn:hover { background: #30363d; color: #e6edf3; }
.status-bar .btn.active { background: #1f6feb; border-color: #58a6ff; color: #fff; }
.edit-name-input { background: #0d1117; border: 1px solid #58a6ff; color: #e6edf3; font-size: 14px; font-weight: 600; padding: 1px 6px; border-radius: 4px; outline: none; width: 300px; }

.messages { flex: 1; overflow-y: auto; padding: 16px; }
.empty-state { display: flex; align-items: center; justify-content: center; height: 100%; color: #484f58; font-size: 16px; }

/* Message bubbles */
.message { margin-bottom: 12px; padding: 10px 14px; border-radius: 8px; border-left: 3px solid transparent; }
.message.user { background: #1c2128; border-left-color: #58a6ff; }
.message.assistant { background: #161b22; border-left-color: #a371f7; }
.message.tool { background: #1c1e24; border-left-color: #f0883e; }
.message.system { background: #161b22; border-left-color: #484f58; opacity: 0.7; }
.message-label { font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px; margin-bottom: 6px; }
.message.user .message-label { color: #58a6ff; }
.message.assistant .message-label { color: #a371f7; }
.message.tool .message-label { color: #f0883e; }
.message.system .message-label { color: #484f58; }
.message-body { font-size: 14px; line-height: 1.6; word-wrap: break-word; overflow-wrap: break-word; }

/* Collapsible */
.collapsible-header { cursor: pointer; display: flex; align-items: center; gap: 6px; user-select: none; }
.collapsible-header .triangle { display: inline-block; transition: transform 0.15s; font-size: 10px; color: #8b949e; }
.collapsible-header.open .triangle { transform: rotate(90deg); }
.collapsible-content { display: none; margin-top: 8px; }
.collapsible-header.open + .collapsible-content { display: block; }

/* Tool details */
.tool-detail { background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 10px; margin-top: 6px; font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace; font-size: 12px; max-height: 400px; overflow: auto; white-space: pre-wrap; word-break: break-all; color: #8b949e; }
.tool-preview { color: #8b949e; font-size: 12px; margin-left: 4px; }

/* Images */
.message-images { margin-top: 8px; }
.message-images img { max-width: 600px; border-radius: 6px; border: 1px solid #30363d; margin: 4px 0; display: block; }

/* Markdown */
.md h1 { font-size: 20px; font-weight: 600; margin: 16px 0 8px; padding-bottom: 4px; border-bottom: 1px solid #30363d; }
.md h2 { font-size: 17px; font-weight: 600; margin: 14px 0 6px; }
.md h3 { font-size: 15px; font-weight: 600; margin: 12px 0 4px; }
.md p { margin: 6px 0; }
.md pre { background: #0d1117; border: 1px solid #30363d; border-radius: 6px; padding: 12px; overflow-x: auto; margin: 8px 0; }
.md pre code { background: none; padding: 0; font-size: 13px; color: #e6edf3; }
.md code { background: #1c2128; padding: 2px 6px; border-radius: 4px; font-size: 13px; font-family: 'SFMono-Regular', Consolas, 'Liberation Mono', Menlo, monospace; color: #e6edf3; }
.md ul, .md ol { margin: 6px 0 6px 20px; }
.md li { margin: 2px 0; }
.md blockquote { border-left: 3px solid #30363d; padding-left: 12px; color: #8b949e; margin: 8px 0; }
.md strong { font-weight: 600; }
.md em { font-style: italic; }
.md table { border-collapse: collapse; margin: 8px 0; }
.md th, .md td { border: 1px solid #30363d; padding: 6px 12px; text-align: left; }
.md th { background: #161b22; font-weight: 600; }

/* Scrollbar */
::-webkit-scrollbar { width: 8px; height: 8px; }
::-webkit-scrollbar-track { background: transparent; }
::-webkit-scrollbar-thumb { background: #30363d; border-radius: 4px; }
::-webkit-scrollbar-thumb:hover { background: #484f58; }
</style>
</head>
<body>
<div class="sidebar">
  <div class="sidebar-header">
    <h1>Breadcrumbs</h1>
    <select class="project-filter" id="projectFilter"><option value="">All projects</option></select>
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
let sessions = [];
let currentSessionId = null;
let imagesExpanded = true;
let selectedProject = '';

function esc(s) {
  if (!s) return '';
  return String(s).replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/"/g,'&quot;');
}

function formatDuration(s) {
  if (s == null) return '';
  if (s < 60) return s + 's';
  var m = Math.floor(s / 60);
  if (m < 60) return m + 'min';
  var h = Math.floor(m / 60);
  var rm = m % 60;
  return rm > 0 ? h + 'h ' + rm + 'min' : h + 'h';
}

function fmtNum(n) {
  if (n == null) return '0';
  if (n >= 1000000) return (n / 1000000).toFixed(1) + 'M';
  if (n >= 1000) return (n / 1000).toFixed(1) + 'K';
  return String(n);
}

function renderMarkdown(text) {
  if (!text) return '';
  var s = esc(text);
  // Code blocks
  s = s.replace(/```(\w*)\n([\s\S]*?)```/g, function(m, lang, code) {
    return '<pre><code>' + code + '</code></pre>';
  });
  // Inline code
  s = s.replace(/`([^`]+)`/g, '<code>$1</code>');
  // Headers
  s = s.replace(/^### (.+)$/gm, '<h3>$1</h3>');
  s = s.replace(/^## (.+)$/gm, '<h2>$1</h2>');
  s = s.replace(/^# (.+)$/gm, '<h1>$1</h1>');
  // Bold and italic
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/\*(.+?)\*/g, '<em>$1</em>');
  // Links
  s = s.replace(/\[([^\]]+)\]\(([^)]+)\)/g, '<a href="$2" target="_blank">$1</a>');
  // Blockquotes
  s = s.replace(/^&gt; (.+)$/gm, '<blockquote>$1</blockquote>');
  // Unordered lists
  s = s.replace(/^[\-\*] (.+)$/gm, '<li>$1</li>');
  s = s.replace(/((?:<li>.*<\/li>\n?)+)/g, '<ul>$1</ul>');
  // Ordered lists
  s = s.replace(/^\d+\. (.+)$/gm, '<li>$1</li>');
  // Tables
  s = s.replace(/^(\|.+\|)$/gm, function(row) {
    if (row.match(/^\|\s*[-:]+/)) return '';
    var cells = row.split('|').filter(function(c) { return c.trim() !== ''; });
    var tds = cells.map(function(c) { return '<td>' + c.trim() + '</td>'; }).join('');
    return '<tr>' + tds + '</tr>';
  });
  s = s.replace(/((?:<tr>.*<\/tr>\n?)+)/g, '<table>$1</table>');
  // Paragraphs - wrap remaining loose lines
  s = s.replace(/^(?!<[a-z])((?:(?!<[\/a-z]).)+)$/gm, '<p>$1</p>');
  // Clean up empty paragraphs
  s = s.replace(/<p>\s*<\/p>/g, '');
  return s;
}

async function fetchSessions() {
  try {
    var res = await fetch('/api/sessions');
    sessions = await res.json();
    populateProjectFilter();
    renderSidebar();
  } catch(e) {
    console.error('Failed to fetch sessions:', e);
  }
}

function populateProjectFilter() {
  var projects = [];
  sessions.forEach(function(s) {
    if (s.project && projects.indexOf(s.project) === -1) projects.push(s.project);
  });
  projects.sort();
  var sel = document.getElementById('projectFilter');
  var current = sel.value;
  sel.innerHTML = '<option value="">All projects (' + sessions.length + ')</option>';
  projects.forEach(function(p) {
    var count = sessions.filter(function(s) { return s.project === p; }).length;
    sel.innerHTML += '<option value="' + esc(p) + '"' + (p === current ? ' selected' : '') + '>' + esc(p) + ' (' + count + ')</option>';
  });
}

document.getElementById('projectFilter').addEventListener('change', function(e) {
  selectedProject = e.target.value;
  renderSidebar();
});

function renderSidebar(filter) {
  filter = (filter || '').toLowerCase();
  var list = sessions.filter(function(s) {
    if (selectedProject && s.project !== selectedProject) return false;
    if (!filter) return true;
    return (s.name || '').toLowerCase().includes(filter) ||
           (s.project || '').toLowerCase().includes(filter) ||
           (s.cwd || '').toLowerCase().includes(filter);
  });

  // Group by project
  var groups = {};
  list.forEach(function(s) {
    var key = s.project || 'Other';
    if (!groups[key]) groups[key] = [];
    groups[key].push(s);
  });

  var html = '';
  Object.keys(groups).forEach(function(project) {
    html += '<div class="session-group-label">' + esc(project) + '</div>';
    groups[project].forEach(function(s) {
      var active = s.session_id === currentSessionId ? ' active' : '';
      var meta = [];
      if (s.started_at) meta.push(s.started_at.substring(0, 10));
      if (s.message_count) meta.push(s.message_count + ' msgs');
      if (s.estimated_cost_usd != null) meta.push('$' + s.estimated_cost_usd.toFixed(2));
      html += '<div class="session-item' + active + '" data-id="' + esc(s.session_id) + '" onclick="selectSession(\'' + esc(s.session_id) + '\')">';
      html += '<div class="session-name">' + esc(s.name) + '</div>';
      html += '<div class="session-meta">' + esc(meta.join(' \u00b7 ')) + '</div>';
      html += '</div>';
    });
  });
  document.getElementById('sessionList').innerHTML = html;
}

async function fetchMessages(sessionId) {
  var res = await fetch('/api/sessions/' + sessionId + '/messages');
  return await res.json();
}

async function renameSession(sessionId, name) {
  await fetch('/api/sessions/' + sessionId, {
    method: 'PATCH',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({name: name})
  });
  await fetchSessions();
}

async function selectSession(sessionId) {
  currentSessionId = sessionId;
  renderSidebar(document.getElementById('search').value);

  var session = sessions.find(function(s) { return s.session_id === sessionId; });
  if (!session) return;

  // Status bar
  var bar = document.getElementById('statusBar');
  bar.style.display = 'flex';
  var parts = [];
  parts.push('<span class="session-title" onclick="startEditName()">' + esc(session.name) + '</span>');
  if (session.model) parts.push('<span class="stat"><span class="stat-label">Model:</span> ' + esc(session.model) + '</span>');
  if (session.duration_seconds != null) parts.push('<span class="stat"><span class="stat-label">Duration:</span> ' + formatDuration(session.duration_seconds) + '</span>');
  if (session.started_at) parts.push('<span class="stat"><span class="stat-label">Started:</span> ' + esc(session.started_at.replace('T', ' ').substring(0, 19)) + '</span>');
  var totalTokens = (session.total_input_tokens || 0) + (session.total_output_tokens || 0);
  if (totalTokens > 0) parts.push('<span class="stat"><span class="stat-label">Tokens:</span> ' + fmtNum(session.total_input_tokens) + ' in / ' + fmtNum(session.total_output_tokens) + ' out</span>');
  if (session.estimated_cost_usd != null) parts.push('<span class="stat"><span class="stat-label">Cost:</span> $' + session.estimated_cost_usd.toFixed(4) + '</span>');
  var imgBtnClass = imagesExpanded ? ' active' : '';
  parts.push('<span class="btn' + imgBtnClass + '" onclick="toggleImageDefault()">Images</span>');
  bar.innerHTML = parts.join('');

  // Messages
  var msgDiv = document.getElementById('messages');
  msgDiv.innerHTML = '<div class="empty-state">Loading...</div>';
  try {
    var msgs = await fetchMessages(sessionId);
    renderMessages(msgs);
  } catch(e) {
    msgDiv.innerHTML = '<div class="empty-state">Failed to load messages</div>';
  }
}

function startEditName() {
  var session = sessions.find(function(s) { return s.session_id === currentSessionId; });
  if (!session) return;
  var bar = document.getElementById('statusBar');
  var titleEl = bar.querySelector('.session-title');
  if (!titleEl) return;
  var input = document.createElement('input');
  input.className = 'edit-name-input';
  input.value = session.name || '';
  input.onkeydown = async function(e) {
    if (e.key === 'Enter') {
      await renameSession(currentSessionId, input.value);
      selectSession(currentSessionId);
    } else if (e.key === 'Escape') {
      selectSession(currentSessionId);
    }
  };
  input.onblur = function() { selectSession(currentSessionId); };
  titleEl.replaceWith(input);
  input.focus();
  input.select();
}

function toggleImageDefault() {
  imagesExpanded = !imagesExpanded;
  if (currentSessionId) selectSession(currentSessionId);
}

function toggleCollapse(el) {
  el.classList.toggle('open');
  var content = el.nextElementSibling;
  if (content && content.classList.contains('collapsible-content')) {
    content.style.display = el.classList.contains('open') ? 'block' : 'none';
  }
}

function renderMessages(msgs) {
  var container = document.getElementById('messages');
  var html = '';

  msgs.forEach(function(msg) {
    if (msg.type === 'file-history-snapshot' || msg.type === 'progress') return;

    if (msg.tool_name) {
      // Tool call
      var preview = (msg.content_text || msg.tool_input || '').substring(0, 80).replace(/\n/g, ' ');
      html += '<div class="message tool">';
      html += '<div class="collapsible-header" onclick="toggleCollapse(this)">';
      html += '<span class="triangle">&#9654;</span>';
      html += '<span class="message-label">Tool: ' + esc(msg.tool_name) + '</span>';
      html += '<span class="tool-preview">' + esc(preview) + '</span>';
      html += '</div>';
      html += '<div class="collapsible-content">';
      if (msg.tool_input) html += '<div class="tool-detail">' + esc(msg.tool_input) + '</div>';
      if (msg.tool_result) html += '<div class="tool-detail">' + esc(msg.tool_result) + '</div>';
      html += '</div>';
      html += '</div>';
    } else if (msg.role === 'system') {
      html += '<div class="message system">';
      html += '<div class="collapsible-header" onclick="toggleCollapse(this)">';
      html += '<span class="triangle">&#9654;</span>';
      html += '<span class="message-label">System</span>';
      html += '</div>';
      html += '<div class="collapsible-content"><div class="message-body">' + esc(msg.content_text || '') + '</div></div>';
      html += '</div>';
    } else if (msg.type === 'tool_result' || (msg.role === 'user' && msg.tool_result)) {
      // Tool result
      var trPreview = (msg.content_text || msg.tool_result || '').substring(0, 80).replace(/\\n/g, ' ');
      html += '<div class="message tool">';
      html += '<div class="collapsible-header" onclick="toggleCollapse(this)">';
      html += '<span class="triangle">&#9654;</span>';
      html += '<span class="message-label">Tool Result</span>';
      html += '<span class="tool-preview">' + esc(trPreview) + '</span>';
      html += '</div>';
      html += '<div class="collapsible-content">';
      html += '<div class="tool-detail">' + esc(msg.content_text || msg.tool_result || '') + '</div>';
      html += '</div>';
      html += '</div>';
    } else if (msg.type === 'system_injection') {
      // System-injected content (skill loads, system reminders, etc.)
      html += '<div class="message system">';
      html += '<div class="collapsible-header" onclick="toggleCollapse(this)">';
      html += '<span class="triangle">&#9654;</span>';
      html += '<span class="message-label">System</span>';
      html += '</div>';
      html += '<div class="collapsible-content"><div class="message-body">' + esc(msg.content_text || '') + '</div></div>';
      html += '</div>';
    } else if (msg.role === 'user') {
      html += '<div class="message user">';
      html += '<div class="message-label">You</div>';
      html += '<div class="message-body">' + esc(msg.content_text || '') + '</div>';
      if (msg.has_images && msg.image_ids && msg.image_ids.length > 0) {
        var imgOpen = imagesExpanded;
        html += '<div class="message-images">';
        html += '<div class="collapsible-header' + (imgOpen ? ' open' : '') + '" onclick="toggleCollapse(this)">';
        html += '<span class="triangle">&#9654;</span> ' + msg.image_ids.length + ' image(s)';
        html += '</div>';
        html += '<div class="collapsible-content" style="display:' + (imgOpen ? 'block' : 'none') + ';">';
        msg.image_ids.forEach(function(id) {
          html += '<img src="/api/images/' + id + '" loading="lazy">';
        });
        html += '</div></div>';
      }
      html += '</div>';
    } else if (msg.role === 'assistant') {
      html += '<div class="message assistant">';
      html += '<div class="message-label">Claude</div>';
      html += '<div class="message-body md">' + renderMarkdown(msg.content_text || '') + '</div>';
      if (msg.has_images && msg.image_ids && msg.image_ids.length > 0) {
        var imgOpen2 = imagesExpanded;
        html += '<div class="message-images">';
        html += '<div class="collapsible-header' + (imgOpen2 ? ' open' : '') + '" onclick="toggleCollapse(this)">';
        html += '<span class="triangle">&#9654;</span> ' + msg.image_ids.length + ' image(s)';
        html += '</div>';
        html += '<div class="collapsible-content" style="display:' + (imgOpen2 ? 'block' : 'none') + ';">';
        msg.image_ids.forEach(function(id) {
          html += '<img src="/api/images/' + id + '" loading="lazy">';
        });
        html += '</div></div>';
      }
      html += '</div>';
    }
  });

  container.innerHTML = html || '<div class="empty-state">No messages in this session</div>';
}

// Keyboard shortcuts
document.addEventListener('keydown', function(e) {
  if (e.key === '/' && document.activeElement.tagName !== 'INPUT') {
    e.preventDefault();
    document.getElementById('search').focus();
    return;
  }
  if (e.key === 'Escape') {
    document.activeElement.blur();
    return;
  }
  if ((e.key === 'ArrowDown' || e.key === 'ArrowUp') && document.activeElement.tagName !== 'INPUT') {
    e.preventDefault();
    var items = document.querySelectorAll('.session-item');
    if (items.length === 0) return;
    var idx = -1;
    items.forEach(function(el, i) { if (el.classList.contains('active')) idx = i; });
    if (e.key === 'ArrowDown') idx = Math.min(idx + 1, items.length - 1);
    else idx = Math.max(idx - 1, 0);
    var id = items[idx].getAttribute('data-id');
    if (id) selectSession(id);
  }
});

document.getElementById('search').addEventListener('input', function(e) {
  renderSidebar(e.target.value);
});

// Boot
fetchSessions();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

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


def main():
    parser = argparse.ArgumentParser(description="Breadcrumbs Viewer")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-open", action="store_true")
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
