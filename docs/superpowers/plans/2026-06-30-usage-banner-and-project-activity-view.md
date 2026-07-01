# Usage Banner + Activity-Grouped, Sortable Projects — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a device-local usage banner (rolling-window token totals, reset countdowns, calibratable `/usage`-style %) and rework the projects table with activity grouping, grouped session-count columns, and click-to-sort.

**Architecture:** New pure Python `get_usage()` + config loader in `server.py`, exposed via a `GET /api/usage` route, unit-tested against a seeded in-memory SQLite DB. The projects-table changes are client-side rewrites of the inline UI JS in `server.py`, fed by the existing `/api/sessions` payload (plus one surfaced `last_msg` field). No DB schema changes.

**Tech Stack:** Python 3.6+ stdlib only (`sqlite3`, `datetime`, `json`, `http.server`), vanilla inline JS/HTML in `server.py`, `unittest` for Python logic, existing bash+curl smoke tests for endpoints.

## Global Constraints

- Python **stdlib only** — no third-party packages, ever.
- **No DB schema changes** and no changes to `session_recorder.py` or the hooks.
- **Device-local**: all computation from the local `~/.claude/breadcrumbs.db`; no network calls.
- Timestamps are ISO-8601 UTC strings; normalize with `.replace("Z", "+00:00")` before `datetime.fromisoformat`, exactly as existing code does.
- The `/usage` percentage is an explicitly-labeled **estimate**; it is hidden entirely when no budget is configured.
- Rolling windows: **session = 5 hours (18000s)**, **weekly = 7 days (604800s)**.
- Follow existing patterns in `server.py`: path dispatch in `do_GET`, `self.send_json(...)`, `get_db()` returning `sqlite3.Row` rows.

---

## File Structure

- `server.py` (modify)
  - Add imports `timezone, timedelta`.
  - Add module constants: `USAGE_CONFIG_PATH`, `USAGE_CONFIG_DEFAULTS`, `WINDOW_LENGTHS`.
  - Add pure helpers: `_parse_ts`, `billable_tokens`, `model_weight`, `load_usage_config`, `get_usage`.
  - Surface `last_msg` in `get_sessions`.
  - Add `/api/usage` branch in `do_GET`.
  - Inline UI JS: add `renderUsageBanner()`, rewrite `renderProjectSummary()`, add bucketing + sort helpers.
- `tests/test_usage.py` (create) — Python `unittest` for `load_usage_config` and `get_usage`.
- `tests/test_api.sh` (modify) — smoke checks for `/api/usage` and `last_msg`.
- `README.md` (modify) — document banner, columns/sorting, and the optional config + calibration.

---

### Task 1: Surface `last_msg` in the sessions payload

**Files:**
- Modify: `server.py` (`get_sessions`, the appended dict ~lines 66-75)
- Test: `tests/test_api.sh`

**Interfaces:**
- Produces: each `/api/sessions` element gains a `"last_msg"` string field (max message timestamp, may be `null` for a session with no messages).

- [ ] **Step 1: Add the field to the returned dict**

In `get_sessions`, the row `r` already selects `MAX(m.timestamp) as last_msg`. Add it to the appended dict:

```python
        sessions.append({
            "session_id": r["session_id"], "name": name,
            "project": project_display, "cwd": cwd,
            "model": session_model, "started_at": r["started_at"],
            "updated_at": r["updated_at"], "last_msg": r["last_msg"], "git_branch": r["git_branch"],
            "duration_seconds": duration, "message_count": r["message_count"],
            "total_input_tokens": total_input, "total_output_tokens": total_output,
            "total_cache_write_tokens": total_cache_write,
            "total_cache_read_tokens": total_cache_read,
        })
```

- [ ] **Step 2: Add a smoke check**

In `tests/test_api.sh`, inside the "Sessions API" section, extend the field list check to include `last_msg`. Replace the existing `fields = [...]` line in the `has_fields` python block:

```bash
has_fields=$(echo "$result" | python3 -c "
import sys,json
s = json.load(sys.stdin)[0]
fields = ['session_id','name','project','model','started_at','message_count','last_msg']
print('true' if all(f in s for f in fields) else 'false')
")
```

- [ ] **Step 3: Run the smoke test against a running server**

Run (in one shell start the server, in another run the test):
```bash
python3 server.py --port 8765 &
sleep 1
./tests/test_api.sh 8765
```
Expected: `PASS: sessions have expected fields`. Then `kill %1`.

- [ ] **Step 4: Commit**

```bash
git add server.py tests/test_api.sh
git commit -m "feat(api): surface last_msg in sessions payload"
```

---

### Task 2: Usage config defaults + loader

**Files:**
- Modify: `server.py` (imports + new constants/helper near the top, after `IMAGES_DIR`)
- Test: `tests/test_usage.py` (create)

**Interfaces:**
- Produces:
  - `USAGE_CONFIG_PATH: Path`
  - `USAGE_CONFIG_DEFAULTS: dict`
  - `load_usage_config(path=USAGE_CONFIG_PATH) -> dict` with keys `session_budget:int`, `weekly_budget:int`, `model_weights:dict[str,float]`, `billable:str`.

- [ ] **Step 1: Write the failing test**

Create `tests/test_usage.py`:

```python
import json
import os
import sqlite3
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import server  # noqa: E402
import session_recorder  # noqa: E402


class LoadUsageConfigTests(unittest.TestCase):
    def test_missing_file_returns_defaults(self):
        cfg = server.load_usage_config(path="/nonexistent/breadcrumbs_usage.json")
        self.assertEqual(cfg["session_budget"], 0)
        self.assertEqual(cfg["weekly_budget"], 0)
        self.assertEqual(cfg["billable"], "output_plus_input")
        self.assertEqual(cfg["model_weights"], {"default": 1.0})

    def test_file_shallow_overrides_defaults(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"session_budget": 1000, "model_weights": {"claude-opus-4-8": 5.0}}, f)
            path = f.name
        try:
            cfg = server.load_usage_config(path=path)
            self.assertEqual(cfg["session_budget"], 1000)
            self.assertEqual(cfg["weekly_budget"], 0)           # untouched default
            self.assertEqual(cfg["model_weights"]["claude-opus-4-8"], 5.0)
            self.assertEqual(cfg["model_weights"]["default"], 1.0)  # default preserved
        finally:
            os.unlink(path)

    def test_malformed_file_returns_defaults(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            f.write("{not valid json")
            path = f.name
        try:
            cfg = server.load_usage_config(path=path)
            self.assertEqual(cfg["session_budget"], 0)
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_usage.LoadUsageConfigTests -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'load_usage_config'`.

- [ ] **Step 3: Implement the config loader**

In `server.py`, after the `IMAGES_DIR = ...` line, add:

```python
USAGE_CONFIG_PATH = Path.home() / ".claude" / "breadcrumbs_usage.json"

USAGE_CONFIG_DEFAULTS = {
    "session_budget": 0,
    "weekly_budget": 0,
    "model_weights": {"default": 1.0},
    "billable": "output_plus_input",
}

WINDOW_LENGTHS = {"session": 5 * 3600, "weekly": 7 * 24 * 3600}


def load_usage_config(path=USAGE_CONFIG_PATH):
    config = {
        "session_budget": USAGE_CONFIG_DEFAULTS["session_budget"],
        "weekly_budget": USAGE_CONFIG_DEFAULTS["weekly_budget"],
        "model_weights": dict(USAGE_CONFIG_DEFAULTS["model_weights"]),
        "billable": USAGE_CONFIG_DEFAULTS["billable"],
    }
    try:
        with open(path) as f:
            overrides = json.load(f)
    except (FileNotFoundError, ValueError, OSError):
        return config
    if not isinstance(overrides, dict):
        return config
    for key in ("session_budget", "weekly_budget", "billable"):
        if key in overrides:
            config[key] = overrides[key]
    if isinstance(overrides.get("model_weights"), dict):
        config["model_weights"].update(overrides["model_weights"])
    return config
```

Also update the import line:

```python
from datetime import datetime, timezone, timedelta
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_usage.LoadUsageConfigTests -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_usage.py
git commit -m "feat(usage): add usage config defaults and loader"
```

---

### Task 3: `get_usage` window computation

**Files:**
- Modify: `server.py` (add helpers + `get_usage` after `load_usage_config`)
- Test: `tests/test_usage.py` (add a test class)

**Interfaces:**
- Consumes: `load_usage_config`, `WINDOW_LENGTHS`.
- Produces:
  - `_parse_ts(ts) -> datetime | None`
  - `billable_tokens(usage:dict, billable:str) -> int`
  - `model_weight(model:str, weights:dict) -> float`
  - `get_usage(db, now=None, config=None) -> dict` shaped:
    ```
    {"generated_at": str,
     "windows": {"session": W, "weekly": W}}
    W = {"length_seconds": int, "window_start": str|None, "reset_at": str|None,
         "tokens": {"input","output","cache_write","cache_read"},
         "by_model": {model: {...same four...}},
         "weighted_tokens": float, "budget": int, "percent": float|None}
    ```

- [ ] **Step 1: Write the failing test**

Append to `tests/test_usage.py`:

```python
def _seed_db():
    db = sqlite3.connect(":memory:")
    db.row_factory = sqlite3.Row
    db.executescript(session_recorder.SCHEMA)
    return db


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.000Z")


def _insert_msg(db, uuid, ts, model, usage):
    db.execute(
        "INSERT INTO messages (uuid, session_id, type, model, timestamp, usage_json) "
        "VALUES (?, 's1', 'assistant', ?, ?, ?)",
        (uuid, model, ts, json.dumps(usage) if usage is not None else None),
    )
    db.commit()


class GetUsageTests(unittest.TestCase):
    def setUp(self):
        self.db = _seed_db()
        self.now = datetime(2026, 6, 30, 12, 0, 0, tzinfo=timezone.utc)

    def tearDown(self):
        self.db.close()

    def test_only_in_window_rows_counted(self):
        # 2h ago -> inside 5h session window; 6h ago -> outside session, inside weekly
        _insert_msg(self.db, "a", _iso(self.now - timedelta(hours=2)),
                    "claude-sonnet-5", {"input_tokens": 100, "output_tokens": 10})
        _insert_msg(self.db, "b", _iso(self.now - timedelta(hours=6)),
                    "claude-sonnet-5", {"input_tokens": 200, "output_tokens": 20})
        u = server.get_usage(self.db, now=self.now)
        self.assertEqual(u["windows"]["session"]["tokens"]["input"], 100)
        self.assertEqual(u["windows"]["session"]["tokens"]["output"], 10)
        self.assertEqual(u["windows"]["weekly"]["tokens"]["input"], 300)
        self.assertEqual(u["windows"]["weekly"]["tokens"]["output"], 30)

    def test_window_start_and_reset(self):
        first = self.now - timedelta(hours=3)
        _insert_msg(self.db, "a", _iso(first), "claude-sonnet-5",
                    {"input_tokens": 5, "output_tokens": 5})
        _insert_msg(self.db, "b", _iso(self.now - timedelta(hours=1)),
                    "claude-sonnet-5", {"input_tokens": 5, "output_tokens": 5})
        w = server.get_usage(self.db, now=self.now)["windows"]["session"]
        self.assertEqual(w["window_start"], _iso(first))
        expected_reset = _iso(first + timedelta(seconds=5 * 3600))
        self.assertEqual(w["reset_at"], expected_reset)

    def test_empty_window_has_null_start_and_zero_tokens(self):
        w = server.get_usage(self.db, now=self.now)["windows"]["session"]
        self.assertIsNone(w["window_start"])
        self.assertIsNone(w["reset_at"])
        self.assertEqual(w["tokens"]["input"], 0)
        self.assertIsNone(w["percent"])

    def test_weighting_and_percent(self):
        cfg = {"session_budget": 100, "weekly_budget": 0,
               "model_weights": {"default": 1.0, "claude-opus-4-8": 5.0},
               "billable": "output_only"}
        _insert_msg(self.db, "a", _iso(self.now - timedelta(hours=1)),
                    "claude-opus-4-8", {"input_tokens": 999, "output_tokens": 10})
        w = server.get_usage(self.db, now=self.now, config=cfg)["windows"]["session"]
        # billable=output_only -> 10 output * weight 5 = 50 weighted
        self.assertEqual(w["weighted_tokens"], 50.0)
        self.assertEqual(w["percent"], 50.0)   # 50 / 100 * 100
        # weekly budget 0 -> percent hidden
        self.assertIsNone(server.get_usage(self.db, now=self.now, config=cfg)["windows"]["weekly"]["percent"])

    def test_malformed_usage_json_skipped(self):
        _insert_msg(self.db, "a", _iso(self.now - timedelta(hours=1)), "claude-sonnet-5", None)
        self.db.execute("UPDATE messages SET usage_json='{bad' WHERE uuid='a'")
        self.db.commit()
        w = server.get_usage(self.db, now=self.now)["windows"]["session"]
        self.assertEqual(w["tokens"]["input"], 0)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m unittest tests.test_usage.GetUsageTests -v`
Expected: FAIL — `AttributeError: module 'server' has no attribute 'get_usage'`.

- [ ] **Step 3: Implement the helpers and `get_usage`**

In `server.py`, after `load_usage_config`, add:

```python
def _parse_ts(ts):
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except (ValueError, AttributeError):
        return None


def billable_tokens(usage, billable):
    inp = usage.get("input_tokens", 0)
    out = usage.get("output_tokens", 0)
    cw = usage.get("cache_creation_input_tokens", 0)
    cr = usage.get("cache_read_input_tokens", 0)
    if billable == "output_only":
        return out
    if billable == "all":
        return inp + out + cw + cr
    return inp + out  # "output_plus_input" default


def model_weight(model, weights):
    return weights.get(model, weights.get("default", 1.0))


def get_usage(db, now=None, config=None):
    if now is None:
        now = datetime.now(timezone.utc)
    if config is None:
        config = load_usage_config()
    weights = config.get("model_weights", {"default": 1.0})
    billable = config.get("billable", "output_plus_input")
    budgets = {"session": config.get("session_budget", 0),
               "weekly": config.get("weekly_budget", 0)}
    windows = {}
    for name, length in WINDOW_LENGTHS.items():
        cutoff = now - timedelta(seconds=length)
        cutoff_iso = cutoff.strftime("%Y-%m-%dT%H:%M:%S.000Z")
        rows = db.execute(
            "SELECT usage_json, model, timestamp FROM messages "
            "WHERE usage_json IS NOT NULL AND timestamp >= ? ORDER BY timestamp",
            (cutoff_iso,)).fetchall()
        totals = {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0}
        by_model = {}
        weighted = 0.0
        window_start = None
        for r in rows:
            ts = _parse_ts(r["timestamp"])
            if ts is None or ts < cutoff:
                continue
            try:
                u = json.loads(r["usage_json"])
            except (ValueError, TypeError):
                continue
            if window_start is None or r["timestamp"] < window_start:
                window_start = r["timestamp"]
            model = r["model"] or "unknown"
            inp = u.get("input_tokens", 0)
            out = u.get("output_tokens", 0)
            cw = u.get("cache_creation_input_tokens", 0)
            cr = u.get("cache_read_input_tokens", 0)
            totals["input"] += inp
            totals["output"] += out
            totals["cache_write"] += cw
            totals["cache_read"] += cr
            m = by_model.setdefault(
                model, {"input": 0, "output": 0, "cache_write": 0, "cache_read": 0})
            m["input"] += inp
            m["output"] += out
            m["cache_write"] += cw
            m["cache_read"] += cr
            weighted += model_weight(model, weights) * billable_tokens(u, billable)
        budget = budgets[name]
        percent = round(weighted / budget * 100, 1) if budget and budget > 0 else None
        reset_at = None
        if window_start is not None:
            ws = _parse_ts(window_start)
            if ws is not None:
                reset_at = (ws + timedelta(seconds=length)).strftime("%Y-%m-%dT%H:%M:%S.000Z")
        windows[name] = {
            "length_seconds": length,
            "window_start": window_start,
            "reset_at": reset_at,
            "tokens": totals,
            "by_model": by_model,
            "weighted_tokens": round(weighted, 2),
            "budget": budget,
            "percent": percent,
        }
    return {"generated_at": now.strftime("%Y-%m-%dT%H:%M:%S.000Z"), "windows": windows}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m unittest tests.test_usage -v`
Expected: PASS (all `LoadUsageConfigTests` + `GetUsageTests`).

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_usage.py
git commit -m "feat(usage): compute rolling-window token usage"
```

---

### Task 4: `/api/usage` route

**Files:**
- Modify: `server.py` (`do_GET`, add a branch after `/api/sessions`)
- Test: `tests/test_api.sh`

**Interfaces:**
- Consumes: `get_usage`, `get_db`, `self.send_json`.
- Produces: `GET /api/usage` → `get_usage(db)` JSON.

- [ ] **Step 1: Add the route**

In `do_GET`, after the `elif path == "/api/sessions":` block, add:

```python
        elif path == "/api/usage":
            db = get_db()
            try:
                self.send_json(get_usage(db))
            finally:
                db.close()
```

- [ ] **Step 2: Add smoke checks**

In `tests/test_api.sh`, after the Sessions API section, add:

```bash
echo "Usage API:"

result=$(curl -s -o /dev/null -w "%{http_code}" "$URL/api/usage")
check "GET /api/usage returns 200" "200" "$result"

result=$(curl -s "$URL/api/usage")
has_windows=$(echo "$result" | python3 -c "
import sys,json
u = json.load(sys.stdin)
w = u.get('windows', {})
print('true' if 'session' in w and 'weekly' in w and 'reset_at' in w['session'] else 'false')
")
check "usage has session and weekly windows" "true" "$has_windows"

echo
```

- [ ] **Step 3: Run the smoke test**

Run:
```bash
python3 server.py --port 8765 &
sleep 1
./tests/test_api.sh 8765
kill %1
```
Expected: `PASS: GET /api/usage returns 200` and `PASS: usage has session and weekly windows`.

- [ ] **Step 4: Commit**

```bash
git add server.py tests/test_api.sh
git commit -m "feat(api): expose /api/usage endpoint"
```

---

### Task 5: Usage banner UI

**Files:**
- Modify: `server.py` (inline `HTML_PAGE` JS — add `renderUsageBanner`, call it from `renderProjectSummary`; add a `<div id="usageBanner">` mount)
- Test: manual (browser) + `tests/test_api.sh` HTML marker check

**Interfaces:**
- Consumes: `GET /api/usage`.
- Produces: `renderUsageBanner()` JS function that fetches `/api/usage` and fills `#usageBanner`.

- [ ] **Step 1: Add a mount point and banner renderer**

At the very start of `renderProjectSummary()` (which builds into `#messages`), prepend a banner container to the generated `html` string, before `'<div class="summary-wrap">'`:

```javascript
  var html = '<div id="usageBanner" class="usage-banner">Loading usage…</div>';
  html += '<div class="summary-wrap">';
```

Then, at the end of `renderProjectSummary()` (after `container.innerHTML = html;`), call:

```javascript
  renderUsageBanner();
```

Add the `renderUsageBanner` function next to `renderProjectSummary`:

```javascript
function fmtCountdown(resetIso) {
  if (!resetIso) return '';
  var ms = new Date(resetIso).getTime() - Date.now();
  if (ms <= 0) return 'resets now';
  var mins = Math.floor(ms / 60000);
  var h = Math.floor(mins / 60);
  var m = mins % 60;
  return 'resets in ' + (h > 0 ? h + 'h ' : '') + m + 'm';
}

function usageCard(title, w) {
  var t = w.tokens || {};
  var total = (t.input || 0) + (t.output || 0);
  var html = '<div class="usage-card">';
  html += '<div class="usage-card-title">' + esc(title) + '</div>';
  html += '<div class="usage-card-total">' + fmtNum(total) + ' tokens</div>';
  html += '<div class="usage-card-breakdown">' + fmtNum(t.input || 0) + ' in / '
        + fmtNum(t.output || 0) + ' out / ' + fmtNum((t.cache_write || 0) + (t.cache_read || 0)) + ' cache</div>';
  if (w.reset_at) {
    html += '<div class="usage-card-reset" title="Approximate: rolling window from first message in range">'
          + esc(fmtCountdown(w.reset_at)) + '</div>';
  }
  if (w.percent !== null && w.percent !== undefined) {
    var pct = Math.min(100, w.percent);
    html += '<div class="usage-bar"><div class="usage-bar-fill" style="width:' + pct + '%"></div></div>';
    html += '<div class="usage-card-pct" title="Estimate calibrated locally via breadcrumbs_usage.json — not from Anthropic">≈'
          + w.percent + '% (est.)</div>';
  }
  html += '</div>';
  return html;
}

function renderUsageBanner() {
  var el = document.getElementById('usageBanner');
  if (!el) return;
  fetch('/api/usage').then(function(r) { return r.json(); }).then(function(u) {
    var w = (u && u.windows) || {};
    if (!w.session && !w.weekly) { el.style.display = 'none'; return; }
    el.innerHTML = usageCard('Current session (5h)', w.session || {tokens:{}})
                 + usageCard('All models (7d)', w.weekly || {tokens:{}});
  }).catch(function() {
    el.innerHTML = '<div class="usage-unavailable">usage unavailable</div>';
  });
}
```

- [ ] **Step 2: Add minimal CSS**

In the `<style>` block of `HTML_PAGE`, add:

```css
.usage-banner { display:flex; gap:12px; margin-bottom:16px; flex-wrap:wrap; }
.usage-card { background:#161b22; border:1px solid #30363d; border-radius:8px; padding:12px 16px; min-width:220px; }
.usage-card-title { font-size:12px; color:#8b949e; text-transform:uppercase; letter-spacing:.04em; }
.usage-card-total { font-size:20px; font-weight:600; margin-top:2px; }
.usage-card-breakdown { font-size:12px; color:#8b949e; margin-top:2px; }
.usage-card-reset { font-size:12px; color:#58a6ff; margin-top:6px; }
.usage-bar { height:6px; background:#30363d; border-radius:3px; margin-top:8px; overflow:hidden; }
.usage-bar-fill { height:100%; background:#3fb950; }
.usage-card-pct { font-size:12px; color:#8b949e; margin-top:4px; }
.usage-unavailable, .usage-banner .usage-unavailable { color:#8b949e; font-size:12px; }
```

- [ ] **Step 3: Add an HTML marker smoke check**

In `tests/test_api.sh`, in the HTML section, add:

```bash
result=$(curl -s "$URL/")
check "page includes usage banner renderer" "renderUsageBanner" "$result"
```

- [ ] **Step 4: Manual browser verification**

Run:
```bash
python3 server.py --port 8765
```
Open `http://localhost:8765`, land on the project summary view. Verify:
- Two cards appear: "Current session (5h)" and "All models (7d)".
- Token totals look plausible; the breakdown line shows in/out/cache.
- A "resets in Xh Ym" line appears when there is recent activity.
- No percentage bar appears (no `~/.claude/breadcrumbs_usage.json` yet).
Then create `~/.claude/breadcrumbs_usage.json` with `{"session_budget": 100000, "weekly_budget": 2000000}`, reload, and verify a green bar + "≈N% (est.)" now shows.

- [ ] **Step 5: Commit**

```bash
git add server.py tests/test_api.sh
git commit -m "feat(ui): add usage banner to project summary"
```

---

### Task 6: Projects table — aggregation, columns, and activity buckets

**Files:**
- Modify: `server.py` (inline JS `renderProjectSummary` — aggregation, columns, default bucketed render; add helpers `projectBucket`, `bucketLabel`)
- Test: manual (browser) + `tests/test_api.sh` HTML marker check

**Interfaces:**
- Consumes: `sessions` global (now includes `last_msg`), `fmtNum`, `esc`.
- Produces: rewritten `renderProjectSummary` default view; module-scope `sortState` object (used by Task 7); helper `computeProjectRows()` returning an array of `{name, first, last, toks_in, toks_cached, toks_out, sess_5h, sess_week, sess_total, last_activity}`.

- [ ] **Step 1: Extend aggregation to compute session buckets**

Replace the aggregation loop and `sorted` computation in `renderProjectSummary` with a reusable `computeProjectRows()` plus bucket helpers. Add above `renderProjectSummary`:

```javascript
var FIVE_H_MS = 5 * 3600 * 1000;
var WEEK_MS = 7 * 24 * 3600 * 1000;

function lastActivity(s) {
  return s.last_msg || s.updated_at || s.started_at || '';
}

function computeProjectRows() {
  var now = Date.now();
  var projects = {};
  sessions.forEach(function(s) {
    var p = s.project || 'Other';
    if (!projects[p]) projects[p] = {
      name: p, sessions: 0, first: null, last: null,
      toks_in: 0, toks_cached: 0, toks_out: 0,
      sess_5h: 0, sess_week: 0, last_activity: '' };
    var pr = projects[p];
    pr.sessions++;
    if (s.started_at && (!pr.first || s.started_at < pr.first)) pr.first = s.started_at;
    if (s.started_at && (!pr.last || s.started_at > pr.last)) pr.last = s.started_at;
    pr.toks_in += s.total_input_tokens || 0;
    pr.toks_cached += (s.total_cache_write_tokens || 0) + (s.total_cache_read_tokens || 0);
    pr.toks_out += s.total_output_tokens || 0;
    var la = lastActivity(s);
    if (la > pr.last_activity) pr.last_activity = la;
    var age = la ? (now - new Date(la).getTime()) : Infinity;
    if (age <= FIVE_H_MS) pr.sess_5h++;
    if (age <= WEEK_MS) pr.sess_week++;
  });
  return Object.keys(projects).map(function(k) {
    var pr = projects[k];
    pr.sess_total = pr.sessions;
    return pr;
  });
}

function projectBucket(pr) {
  var age = pr.last_activity ? (Date.now() - new Date(pr.last_activity).getTime()) : Infinity;
  if (age <= FIVE_H_MS) return 0;
  if (age <= WEEK_MS) return 1;
  return 2;
}

var BUCKET_LABELS = ['Active last 5h', 'Active this week', 'Older'];
```

- [ ] **Step 2: Rewrite the table render (default bucketed view)**

Replace the body of `renderProjectSummary` (from where it builds `html` for the summary-wrap onward) with a version that renders bucket group-header rows and the new column order. Keep the banner mount from Task 5 at the top.

```javascript
function renderProjectSummary() {
  document.getElementById('statusBar').style.display = 'none';
  var container = document.getElementById('messages');
  var rows = computeProjectRows();

  var html = '<div id="usageBanner" class="usage-banner">Loading usage…</div>';
  html += '<div class="summary-wrap">';
  if (sortState.column) {
    html += '<div class="sort-reset"><a href="#" onclick="resetProjectSort();return false;">Group by activity</a></div>';
  }
  html += '<table class="summary-table" id="projectsTable">';
  html += renderProjectHead();
  html += '<tbody>';

  var totals = { toks_in: 0, toks_cached: 0, toks_out: 0, sess_5h: 0, sess_week: 0, sess_total: 0 };
  function accumulate(pr) {
    totals.toks_in += pr.toks_in; totals.toks_cached += pr.toks_cached; totals.toks_out += pr.toks_out;
    totals.sess_5h += pr.sess_5h; totals.sess_week += pr.sess_week; totals.sess_total += pr.sess_total;
  }

  if (sortState.column) {
    var flat = rows.slice().sort(makeSortComparator(sortState.column, sortState.dir));
    flat.forEach(function(pr) { accumulate(pr); html += renderProjectRow(pr); });
  } else {
    // default: bucket, then last_activity desc within bucket
    var buckets = [[], [], []];
    rows.forEach(function(pr) { buckets[projectBucket(pr)].push(pr); });
    buckets.forEach(function(group, bi) {
      if (!group.length) return;
      group.sort(function(a, b) { return a.last_activity < b.last_activity ? 1 : -1; });
      html += '<tr class="bucket-header"><td colspan="9">' + esc(BUCKET_LABELS[bi]) + '</td></tr>';
      group.forEach(function(pr) { accumulate(pr); html += renderProjectRow(pr); });
    });
  }

  html += '</tbody>';
  html += '<tfoot><tr style="border-top:2px solid #30363d;font-weight:600;">';
  html += '<td>Total</td><td></td><td></td>';
  html += '<td class="num">' + fmtNum(totals.toks_in) + '</td>';
  html += '<td class="num">' + fmtNum(totals.toks_cached) + '</td>';
  html += '<td class="num">' + fmtNum(totals.toks_out) + '</td>';
  html += '<td class="num">' + totals.sess_5h + '</td>';
  html += '<td class="num">' + totals.sess_week + '</td>';
  html += '<td class="num">' + totals.sess_total + '</td>';
  html += '</tr></tfoot>';
  html += '</table>';
  html += '<div style="margin-top:12px;padding:8px 12px;background:#161b22;border-radius:6px;font-size:12px;color:#8b949e;">';
  html += 'MCP endpoint: <code style="color:#58a6ff;cursor:pointer;user-select:all;">http://localhost:' + location.port + '/mcp</code>';
  html += '</div></div>';

  container.innerHTML = html;
  renderUsageBanner();
}

function renderProjectRow(pr) {
  var h = '<tr>';
  h += '<td class="project-name" onclick="document.getElementById(\'projectFilter\').value=\'' + esc(pr.name) + '\';selectedProject=\'' + esc(pr.name) + '\';renderSidebar();document.getElementById(\'statusBar\').style.display=\'none\';document.getElementById(\'messages\').innerHTML=\'<div class=\\\'empty-state\\\'>Select a session to view</div>\';">' + esc(pr.name) + '</td>';
  h += '<td>' + (pr.first ? pr.first.substring(0, 10) : '') + '</td>';
  h += '<td>' + (pr.last ? pr.last.substring(0, 10) : '') + '</td>';
  h += '<td class="num">' + fmtNum(pr.toks_in) + '</td>';
  h += '<td class="num">' + fmtNum(pr.toks_cached) + '</td>';
  h += '<td class="num">' + fmtNum(pr.toks_out) + '</td>';
  h += '<td class="num">' + pr.sess_5h + '</td>';
  h += '<td class="num">' + pr.sess_week + '</td>';
  h += '<td class="num">' + pr.sess_total + '</td>';
  h += '</tr>';
  return h;
}
```

- [ ] **Step 2b: Add the header renderer (columns) — placeholder sort hook**

Add `renderProjectHead` (Task 7 wires the click handlers/indicators; here it renders the labels and the new order). Also declare `sortState` now so this task runs standalone:

```javascript
var sortState = { column: null, dir: 1 };

var PROJECT_COLUMNS = [
  { key: 'name',        label: 'Project',    cls: '' },
  { key: 'first',       label: 'First',      cls: '' },
  { key: 'last',        label: 'Last',       cls: '' },
  { key: 'toks_in',     label: 'Tokens In',  cls: 'num' },
  { key: 'toks_cached', label: 'Cached',     cls: 'num' },
  { key: 'toks_out',    label: 'Tokens Out', cls: 'num' },
  { key: 'sess_5h',     label: 'Sess 5h',    cls: 'num' },
  { key: 'sess_week',   label: 'Sess wk',    cls: 'num' },
  { key: 'sess_total',  label: 'Sess total', cls: 'num' }
];

function renderProjectHead() {
  var h = '<thead><tr>';
  PROJECT_COLUMNS.forEach(function(c) {
    var arrow = '';
    if (sortState.column === c.key) arrow = sortState.dir === 1 ? ' ▲' : ' ▼';
    h += '<th class="' + c.cls + ' sortable" onclick="sortProjects(\'' + c.key + '\')">' + esc(c.label) + arrow + '</th>';
  });
  h += '</tr></thead>';
  return h;
}

function makeSortComparator(key, dir) {
  var numeric = ['toks_in','toks_cached','toks_out','sess_5h','sess_week','sess_total'].indexOf(key) !== -1;
  return function(a, b) {
    var av = a[key], bv = b[key];
    if (numeric) { av = av || 0; bv = bv || 0; return (av - bv) * dir; }
    av = (av || '').toString().toLowerCase();
    bv = (bv || '').toString().toLowerCase();
    if (av < bv) return -1 * dir;
    if (av > bv) return 1 * dir;
    return 0;
  };
}
```

(`sortProjects` / `resetProjectSort` are added in Task 7; the default view never calls them, so this task renders and verifies the bucketed layout on its own. Clicking a header before Task 7 will error in the console — that is expected and resolved by Task 7.)

- [ ] **Step 3: Add CSS for buckets/sort affordances**

In `<style>`, add:

```css
.bucket-header td { background:#0d1117; color:#8b949e; font-size:11px; text-transform:uppercase; letter-spacing:.04em; padding-top:10px; }
.summary-table th.sortable { cursor:pointer; user-select:none; }
.summary-table th.sortable:hover { color:#58a6ff; }
.sort-reset { margin-bottom:8px; font-size:12px; }
.sort-reset a { color:#58a6ff; }
```

- [ ] **Step 4: Add an HTML marker smoke check**

In `tests/test_api.sh` HTML section:

```bash
result=$(curl -s "$URL/")
check "page includes grouped session columns" "Sess total" "$result"
```

- [ ] **Step 5: Manual browser verification**

Run `python3 server.py --port 8765`, open the summary view. Verify:
- Columns read: Project | First | Last | Tokens In | Cached | Tokens Out | Sess 5h | Sess wk | Sess total.
- Rows are grouped under "Active last 5h" / "Active this week" / "Older" headers, most-recently-active first within each group.
- Totals footer aligns under the correct columns and the three session totals sum correctly.

- [ ] **Step 6: Commit**

```bash
git add server.py tests/test_api.sh
git commit -m "feat(ui): activity-grouped projects with grouped session columns"
```

---

### Task 7: Click-to-sort with reset

**Files:**
- Modify: `server.py` (inline JS — add `sortProjects`, `resetProjectSort`)
- Test: manual (browser)

**Interfaces:**
- Consumes: `sortState`, `renderProjectSummary`, `makeSortComparator` (from Task 6).
- Produces: `sortProjects(key)` and `resetProjectSort()` JS functions.

- [ ] **Step 1: Implement sort + reset handlers**

Add near `renderProjectSummary`:

```javascript
function sortProjects(key) {
  if (sortState.column === key) {
    sortState.dir = -sortState.dir;
  } else {
    sortState.column = key;
    sortState.dir = 1;
  }
  renderProjectSummary();
}

function resetProjectSort() {
  sortState.column = null;
  sortState.dir = 1;
  renderProjectSummary();
}
```

- [ ] **Step 2: Manual browser verification**

Run `python3 server.py --port 8765`, open the summary view. Verify:
- Clicking a column header removes the bucket headers and sorts the whole table by that column; a ▲ appears on the active header.
- Clicking the same header again toggles to ▼ (descending) and reverses the order.
- Numeric columns (tokens, session counts) sort numerically; Project/First/Last sort as expected.
- A "Group by activity" link appears while sorted; clicking it restores the bucketed default view and clears the arrow.

- [ ] **Step 3: Commit**

```bash
git add server.py
git commit -m "feat(ui): click-to-sort projects table with activity reset"
```

---

### Task 8: Documentation

**Files:**
- Modify: `README.md`

**Interfaces:** none (docs only).

- [ ] **Step 1: Document the feature**

In `README.md`, add a "Usage" section covering:
- The banner: two rolling windows (Current session = 5h, All models = 7d), native token totals + approximate reset countdown, all computed locally from `~/.claude/breadcrumbs.db`.
- The optional `~/.claude/breadcrumbs_usage.json` config, with the exact schema and a calibration note:

````markdown
## Usage banner

The project summary shows a device-local usage banner with two rolling windows —
**Current session** (5 hours) and **All models** (7 days) — computed from the token
counts already stored in `~/.claude/breadcrumbs.db`. Totals and the approximate reset
countdown are always exact for this machine.

To also show an approximate `/usage`-style percentage, create
`~/.claude/breadcrumbs_usage.json`:

```json
{
  "session_budget": 100000,
  "weekly_budget": 2000000,
  "model_weights": { "default": 1.0, "claude-opus-4-8": 5.0 },
  "billable": "output_plus_input"
}
```

- `session_budget` / `weekly_budget` — weighted-token budgets. `0` (default) hides the percentage.
- `model_weights` — per-model multiplier (`default` applies to unlisted models).
- `billable` — `output_only` | `output_plus_input` (default) | `all`.

The percentage is a **local estimate** — Anthropic's real plan limits are undocumented.
Calibrate by nudging the budgets until the percentage matches what Claude Code's
`/usage` shows, then it will track.
````

- Also add "Sess 5h / Sess wk / Sess total" columns, activity grouping, and click-to-sort (with "Group by activity" reset) to the Viewer section.

- [ ] **Step 2: Commit**

```bash
git add README.md
git commit -m "docs: document usage banner, config, and project table changes"
```

---

## Self-Review Notes

- **Spec coverage:** banner windows + native totals + reset (Tasks 3–5); calibratable % + config (Tasks 2, 5, 8); `last_msg` surfacing (Task 1); activity buckets + grouped session columns + moved total (Task 6); click-to-sort Option 1 + reset (Task 7); tests (Tasks 2–4 automated, 5–7 manual per repo convention); docs (Task 8). All spec sections map to a task.
- **Type consistency:** `sortState`, `makeSortComparator`, `computeProjectRows`, `renderProjectHead`, `renderProjectRow`, `renderUsageBanner`, `get_usage` names are used identically across tasks. The `windows` JSON shape in Task 3 matches the fields read in Task 5.
- **Testing reality:** the repo has no JS test harness; UI tasks use precise manual checklists plus cheap HTML-marker smoke checks, consistent with existing `tests/*.sh`. Python logic (`get_usage`, config) gets real `unittest` coverage with an injectable `now` for determinism.
