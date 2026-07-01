# Mobile-friendly viewer — design

Date: 2026-07-01

## Context

The Breadcrumbs viewer is a desktop-oriented single-page app served as one raw
string constant (`HTML_PAGE`) from `GET /` in `server.py`. Its layout assumes a
wide screen: `body { display:flex; height:100vh; overflow:hidden }`, a fixed
280px sidebar (project filter + search + session list), a main area (status bar +
message feed), and a 9-column project summary table on the home view. There are
no media queries, so on a phone the sidebar and the wide table make the site
awkward to use.

We want a mobile-friendly experience: the home screen lists projects, tapping a
project drills into its sessions, and tapping a session opens the full
transcript. Desktop must stay exactly as it is today.

## Approach

Make the **same page responsive** — one codebase, no new route, no Python/routing
changes. All edits live inside the `HTML_PAGE` string. Add:

1. A single `@media (max-width: 768px)` CSS block.
2. Two new DOM elements that are `display:none` on desktop.
3. A small mobile view-state controller in the existing `<script>`.

Above the breakpoint nothing changes — the new elements are hidden and the
existing desktop boot flow runs untouched. Below it, the page becomes a
three-screen drill-down.

## The three mobile screens

Each screen maps onto a data level that already exists, and reuses the existing
render function for that level:

| Screen | Reuses | Content |
|---|---|---|
| **Projects** (home) | `renderUsageBanner()` + `computeProjectRows()` | Usage summary cards, then compact tappable project cards: name · last active · session count |
| **Sessions** | `renderSidebar()` (the existing `#sessionList`) | The selected project's sessions, grouped and searchable exactly as today |
| **Messages** | `selectSession()` (`#messages` + status bar) | Full transcript — markdown, images, tool details — unchanged |

Exactly one screen is visible at a time, selected by a body class.

## Navigation

- State: a `mobileScreen` variable — `'projects' | 'sessions' | 'messages'` —
  toggled by `setMobileScreen(name)`, which sets a body class
  (`m-projects` / `m-sessions` / `m-messages`).
- A slim **mobile top bar** (`.mobile-topbar`, hidden on desktop) holds a back
  chevron and a title. The title reflects the current screen (e.g. "Breadcrumbs"
  / project name / session name).
- Flow:
  - Tap a project card → set project filter + `selectedProject`, call
    `renderSidebar()`, `setMobileScreen('sessions')`.
  - Tap a session → `selectSession(id)` (existing) → advance to `'messages'`.
  - Back button: Messages → Sessions → Projects. On the Projects screen the back
    button is hidden.

## New pieces (all inside `HTML_PAGE`)

**CSS** — one `@media (max-width: 768px)` block:
- `body` switches `flex` → `block`, drops `height:100vh`/`overflow:hidden` so the
  page scrolls normally.
- `.sidebar` loses its fixed `280px` / `min-width` and goes full-width.
- Screens show/hide by body class: `.mobile-projects`, `.sidebar`, `.main` each
  shown only for their matching `m-*` body class.
- Touch targets (`.session-item`, project cards, back button) bumped to ≥44px.
- `.mobile-topbar` and `.mobile-projects` default to `display:none` outside the
  media query (keeps desktop untouched).
- The redundant project-filter `<select>` is hidden on mobile; the search box
  stays (useful on the Sessions screen).

**HTML** — two elements added to `<body>`:
- `.mobile-topbar` with a back chevron button and a title span.
- `#mobileProjects` container (holds the usage banner + project cards).

**JS** — added to the existing `<script>`:
- `renderMobileProjects()` — renders a usage-banner container (`id="usageBanner"`)
  plus project cards built from `computeProjectRows()`; wires each card's tap to
  open its project. Calls `renderUsageBanner()` to fill the banner.
- `setMobileScreen(name)` — set state, toggle body class, update top-bar title
  and back-button visibility.
- `openProjectMobile(name)` — set filter + `selectedProject`, `renderSidebar()`,
  go to Sessions.
- Back-button handler — step up one screen.
- A one-line hook in `selectSession()` — when on mobile, advance to the Messages
  screen after the session loads.
- Boot: detect mobile via `window.matchMedia('(max-width: 768px)').matches`. If
  mobile, render the mobile home and default to the Projects screen and **skip**
  the desktop `renderProjectSummary()` (avoids a duplicate `usageBanner` id and
  the wide table). If desktop, run the existing flow unchanged.

## Reused, unchanged

`computeProjectRows()`, `renderSidebar()`, `selectSession()`,
`renderUsageBanner()`, `renderMessages()`, `renderMarkdown()`, image/tool
rendering — all reused as-is. The mobile layer is additive.

## Out of scope (YAGNI)

- No separate mobile route or template.
- No live switch when a desktop browser is merely resized across the breakpoint
  (chosen at boot via `matchMedia`; reload to switch). Acceptable for a personal
  single-user tool.
- No sort controls on the mobile home.
- Desktop layout and behavior are not modified.

## Verification

Breadcrumbs has no UI test harness (tests are shell-only by design), so verify by
observation:

1. Run the server (systemd user unit `breadcrumbs`, or run `server.py` directly).
2. Open the viewer in Chrome at ~390px width (device emulation or a narrow
   window), or on a real phone via `--tailscale`.
3. Confirm the Projects home shows the usage summary cards then the project
   cards; tap a project → its sessions; tap a session → full transcript; back
   button walks back up each level.
4. Widen the window past 768px and confirm the desktop layout (sidebar + table)
   is unchanged.

The Chrome MCP tools can drive steps 2–4 and screenshot each screen.
