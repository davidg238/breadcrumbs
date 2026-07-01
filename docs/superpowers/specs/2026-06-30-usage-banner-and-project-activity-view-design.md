# Usage Banner + Activity-Grouped, Sortable Projects — Design

**Date:** 2026-06-30
**Status:** Approved (pending spec review)
**Scope:** Additions to the Breadcrumbs local web UI (`server.py`) and a new read-only API endpoint. No schema changes, stdlib-only, device-local.

## Motivation

Claude Code's `/usage` screen (session/weekly usage + reset times) is computed locally on each machine and has no public API. Breadcrumbs already ingests the same raw material: `messages.usage_json` (input/output/cache tokens) with an indexed `timestamp` and `model` per message. So Breadcrumbs can surface a usage view directly, and — since remote-driven sessions still flow through this machine — a device-local view is sufficient.

This feature adds:
1. A **usage banner** at the top of the project-summary view (native token totals per rolling window + reset countdowns, plus a best-effort, user-calibratable `/usage`-style percentage).
2. **Activity-based grouping** of the projects table (active in last 5h → this week → older).
3. **Grouped session-count columns** (5h / week / total) at the right of each project row.
4. **Click-to-sort** on every column header, with a reset back to the default grouped view.

## Non-Goals

- Reproducing Anthropic's exact plan percentages from first principles. The plan budgets are undocumented, model-weighted, and change over time. The percentage overlay is an explicitly-labeled *estimate* calibrated by the user, not an authoritative figure.
- Cross-device / account-wide aggregation. This is device-local by design.
- Any change to the DB schema, hooks, or `session_recorder.py`.

## Terminology

- **Window** — a trailing (rolling) time range ending "now".
  - **Current session** window = trailing **5 hours** (maps to `/usage`'s session bar).
  - **All models** window = trailing **7 days** (maps to `/usage`'s weekly bar).
- **Reset time** — approximated as *(earliest message timestamp within the window) + window length*, with a live countdown. This is a rolling approximation; Anthropic's real blocks are anchored slightly differently, so the native reset is labeled approximate.
- **Last activity** of a session — its latest message timestamp (`last_msg`, falling back to `updated_at`). "Seen in last 5h / this week" is measured against last activity, not session start.

## Architecture Overview

Two independent pieces, matching the existing separation in `server.py`:

| Piece | Where | Data source |
|---|---|---|
| Usage banner | New `GET /api/usage` endpoint + new `renderUsageBanner()` in the inline UI JS | `messages` table, aggregated server-side by window |
| Projects table changes | Rewrite of `renderProjectSummary()` in the inline UI JS | Existing `/api/sessions` payload (already client-side) |

The projects-table work is almost entirely client-side because `/api/sessions` (`get_sessions`) already returns per-session `started_at`, `updated_at`, and token totals. One tiny server change is needed: `get_sessions` already computes `last_msg` (MAX message timestamp) in its SQL but does not surface it in the returned dict — add `"last_msg": r["last_msg"]` so the client has a precise last-activity value (falling back to `updated_at`/`started_at`). Only the banner needs substantive new server work, because accurate rolling-window token sums require message-level timestamps within the window.

## Component 1: Usage Banner

### Server: `GET /api/usage`

Add a `get_usage(db)` function and a route branch in `do_GET` (following the `/api/sessions` pattern). It returns JSON with one object per window:

```json
{
  "generated_at": "2026-06-30T22:40:00Z",
  "windows": {
    "session": {
      "length_seconds": 18000,
      "window_start": "2026-06-30T19:05:00Z",
      "reset_at": "2026-07-01T00:05:00Z",
      "tokens": { "input": 0, "output": 0, "cache_write": 0, "cache_read": 0 },
      "weighted_tokens": 0,
      "by_model": { "claude-opus-4-8": { "input": 0, "output": 0, "cache_write": 0, "cache_read": 0 } },
      "budget": 0,
      "percent": null
    },
    "weekly": { "length_seconds": 604800, "…": "same shape" }
  }
}
```

Computation, per window:
- `SELECT usage_json, model, timestamp FROM messages WHERE usage_json IS NOT NULL AND timestamp >= :cutoff` where `:cutoff` = now − window length. Uses `idx_messages_timestamp`.
- Sum `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens` across **all projects**.
- `window_start` = MIN(timestamp) among the rows in the window (the anchor for `reset_at`); if no rows, `window_start`/`reset_at` are null.
- `weighted_tokens` = Σ over rows of `weight(model) × billable_tokens(row)`, where `billable_tokens` and `weight` come from config (below).
- `percent` = `weighted_tokens / budget × 100` if `budget > 0`, else `null`.

"Now" is derived from the server clock (`datetime.now(timezone.utc)`) — this is production server code, not a workflow sandbox, so wall-clock is available and correct here.

### Config: `~/.claude/breadcrumbs_usage.json` (optional)

Defaults live in `server.py` as a constant; the file, if present, shallow-overrides them. Missing file ⇒ native view only, percentage hidden.

```json
{
  "session_budget": 0,
  "weekly_budget": 0,
  "model_weights": { "default": 1.0, "claude-opus-4-8": 5.0, "claude-sonnet-5": 1.0, "claude-haiku-4-5": 0.25 },
  "billable": "output_plus_input"
}
```

- `session_budget` / `weekly_budget` — weighted-token budgets. `0` (default) hides the percentage.
- `model_weights` — per-model multiplier; `default` used for unlisted models. Lets the user calibrate against what `/usage` actually shows.
- `billable` — which token components feed the weighted total: `"output_only"` | `"output_plus_input"` (default) | `"all"` (adds cache read/write). Kept simple and documented so calibration has one obvious knob to turn first (budget), then a second (weights).

Config is loaded per request (file is tiny) so edits take effect without a server restart.

### UI: `renderUsageBanner()`

Rendered above the projects table inside `renderProjectSummary()` (fetch `/api/usage` when entering the summary view). Two side-by-side cards, "Current session" (5h) and "All models" (7d). Each card shows:
- Headline token count for the window (formatted with `fmtNum`) and an in / out / cache breakdown line.
- **Reset:** `reset_at` as a live countdown (e.g. "resets in 2h 41m"), with the approximate nature noted via tooltip/subtext. Hidden if the window has no messages.
- **Percentage (only if `percent != null`):** a slim progress bar + "≈NN% (est.)" label, with a tooltip explaining it's calibrated locally, not from Anthropic.

Failure handling: if `/api/usage` errors or returns empty, the banner renders a muted "usage unavailable" line and the projects table still renders. The banner never blocks the table.

## Component 2: Projects Table (rewrite of `renderProjectSummary`)

### Per-project aggregation

Extend the existing aggregation loop to also compute, using each session's last-activity timestamp `la = s.last_msg || s.updated_at || s.started_at`:
- `sessions_5h` — count of sessions with `la` within trailing 5h.
- `sessions_week` — count within trailing 7d.
- `sessions_total` — existing count.
- `last_activity` — max `la` across the project's sessions (drives bucketing/sort).

"Now" for the client uses browser `Date.now()` (the served UI runs in a real browser; no sandbox restriction).

### Column order (session counts grouped at the right, total moved to end)

```
Project | First | Last | Tokens In | Cached | Tokens Out | Sess 5h | Sess wk | Sess total
```

The `<tfoot>` totals row is updated to the same order (Sess 5h / Sess wk / Sess total summed; First/Last blank).

### Default view: activity buckets

Three buckets by `last_activity`:
1. **Active last 5h**
2. **Active this week** (last 7d, not already in bucket 1)
3. **Older**

Rendered in that order; within each bucket, rows sorted by `last_activity` descending. Light group-header rows (or a subtle divider + label) separate the buckets. Empty buckets are omitted.

### Click-to-sort (Option 1: header-click flattens to a full sort)

- Clicking any column header switches the table to a **flat** sort (buckets removed) keyed on that column.
- Clicking the same header again toggles ascending/descending.
- The active column shows a ▲/▼ indicator.
- Sort keys: Project = case-insensitive string; First/Last = ISO string (lexical = chronological); the six numeric columns = numeric.
- A **"Group by activity"** reset control (a small link/button near the table header, shown only when a flat sort is active) returns to the default bucketed view and clears the active-sort indicator.
- Sort/group state is view-local JS state (no persistence, no URL params) — it resets when leaving/re-entering the summary view.

## Data Flow

1. User opens the project-summary view.
2. UI fetches `/api/sessions` (existing) and `/api/usage` (new) in parallel.
3. `renderUsageBanner()` draws the two window cards from `/api/usage`.
4. `renderProjectSummary()` aggregates the sessions client-side, then renders either the bucketed default or the active flat sort.
5. Clicking a header re-renders the table from the already-loaded data (no refetch). Clicking "Group by activity" restores the default.

## Error Handling & Edge Cases

- **No messages in a window** → native totals show 0, reset line hidden, percentage hidden.
- **No config file / zero budget** → percentage hidden; native view unaffected.
- **Malformed `usage_json` rows** → skipped (wrapped in try/except like existing token summation).
- **Unknown model in weights** → uses `default` weight.
- **Clock/timezone** → all timestamps handled as UTC ISO (matching existing `fromisoformat(... .replace("Z","+00:00"))` usage); countdown computed against `Date.now()` in the browser.
- **`/api/usage` failure** → banner degrades to a muted message; table unaffected.
- **Empty database** → banner shows zeros/unavailable; table shows existing empty state.

## Testing

Add to `tests/` (stdlib `unittest`, following existing style):

Server / `get_usage`:
- Seed an in-memory (or temp-file) DB with messages at known timestamps across two projects and models; assert window sums include only in-window rows and exclude out-of-window rows.
- Assert `window_start` = earliest in-window timestamp and `reset_at` = `window_start + length`.
- Assert `weighted_tokens` respects `model_weights` and the `billable` setting.
- Assert `percent` is `null` when budget is 0 and correct when budget > 0.
- Config override: absent file ⇒ defaults; present file ⇒ shallow-merged.

Client logic (extract pure helpers so they're testable without a browser, or cover via a lightweight DOM-free unit on the bucketing/sort functions):
- Bucketing: a session with last-activity 3h ago → bucket 1; 3 days ago → bucket 2; 30 days ago → bucket 3.
- `sessions_5h` / `sessions_week` / `sessions_total` counts correct for a mixed project.
- Flat-sort comparator: numeric vs string ordering, asc/desc toggle.

Manual verification checklist:
- Banner numbers are in the right ballpark vs. the real `/usage` screen after calibration.
- Bucket ordering and column layout render correctly; totals footer matches.
- Header click sorts and toggles; "Group by activity" restores the default.

## Files Touched

- `server.py` — surface `last_msg` in the `get_sessions` dict; add `get_usage()` + `/api/usage` route; add `USAGE_CONFIG_DEFAULTS` constant + loader; rewrite `renderProjectSummary()` and add `renderUsageBanner()` + sort/bucket helpers in the inline UI JS.
- `tests/` — new test module for `get_usage` and the extracted client helpers.
- `README.md` — document the usage banner, the new columns/sorting, and the optional `~/.claude/breadcrumbs_usage.json` config (including how to calibrate the percentage).
- Optional: `docs/` note on calibration.
