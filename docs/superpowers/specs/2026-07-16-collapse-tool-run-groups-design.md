# Collapse consecutive tool rows into groups

**Date:** 2026-07-16
**Status:** Approved
**Area:** Viewer (`server.py` → `HTML_PAGE` → `renderMessages`)

## Problem

Session transcripts give up a lot of vertical real estate to `Tool` / `Tool Result`
rows the reader rarely looks at. They break the reading flow between the user's
messages and Claude's prose.

## Goal

Fold a run of consecutive non-prose rows into a single collapsed summary line
(`› Nc · Nr · Ns`) with a twisty. Prose (user + assistant messages) stays exactly
as it renders today.

## Design

### Grouping

In `renderMessages`, walk the message list and accumulate a run of consecutive
**non-prose** rows. A row is non-prose if it renders today as `Tool` (has
`tool_name`), `Tool Result` (`type === 'tool_result'` or a `user` row carrying
`tool_result`), `System` (`role === 'system'`), or `system_injection`.

- A `user` or `assistant` message breaks the run and renders exactly as now.
- Skipped rows (`file-history-snapshot`, `progress`) are ignored for grouping —
  they neither render nor break a run.
- A run of length 1 renders as it does today (no wrapper).
- A run of length ≥ 2 renders as one **collapsed group**.

### Collapsed summary line

`› Nc · Nr · Ns`, zeros omitted:

- `c` = rows with `tool_name`
- `r` = tool-result rows
- `s` = `system` + `system_injection` rows

Examples: `› 4c · 4r`, `› 4c · 4r · 2s`, `› 2s`.

### Expanded

Clicking the group twisty reveals each child row rendered **exactly as today** —
its own `Tool` / `Tool Result` / `System` row with its own triangle, preview, and
collapsed details. Nested twisties; no behavior lost. Groups collapsed by default.

## Implementation

1. Extract the current per-branch HTML inside the `forEach` into `renderRow(msg)`
   returning the existing markup string (or `''` for skipped types).
2. Add `isProse(msg)` / `isSkipped(msg)` helpers.
3. Replace the `forEach` with a grouping pass: emit `renderRow` for prose and
   singleton runs; for runs ≥ 2 emit a `.message-group` wrapper whose
   `.collapsible-content` holds the child `renderRow`s, and a `.collapsible-header`
   summary line built from the c/r/s tally.
4. One new CSS rule `.message.group` (muted, matches existing collapsible look);
   reuse `.collapsible-header`, `.collapsible-content`, `.triangle`, `toggleCollapse`.

No server, DB, or dependency changes.

## Out of scope (YAGNI)

- Persisting expand/collapse state across reloads.
- Configurable grouping rules or per-tool filtering.
- Changing how individual rows or their previews render.
