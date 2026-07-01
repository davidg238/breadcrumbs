# Mobile-friendly viewer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Breadcrumbs viewer usable on a phone via a responsive drill-down (Projects → Sessions → Messages), without changing the desktop layout.

**Architecture:** Add a `@media (max-width: 768px)` CSS block, two hidden-on-desktop DOM elements (a mobile top bar and a mobile projects container), and a small mobile view-state controller — all inside the single `HTML_PAGE` string in `server.py`. Below the breakpoint the page shows one "screen" at a time by body class; above it, everything new is `display:none` and the existing desktop flow runs untouched.

**Tech Stack:** Plain HTML/CSS/vanilla JS embedded in `server.py` (`HTML_PAGE`). No build step, no dependencies.

## Global Constraints

- All edits live inside the `HTML_PAGE` raw string in `/home/david/workspace/breadcrumbs/server.py`. No route/Python-handler changes.
- Desktop layout and behavior (width > 768px) must remain identical.
- Reuse existing JS: `computeProjectRows()` (server.py:677), `renderSidebar()` (:493), `selectSession()` (:542), `renderUsageBanner()` (:657), `renderProjectSummary()` (:784). Do not duplicate their logic.
- No UI test harness exists — verification is by browser observation at a narrow viewport (~390px) and a wide viewport (>768px), drivable via Chrome MCP.
- Breakpoint value is `768px`, used identically in CSS and in every `matchMedia` call.

---

### Task 1: Mobile CSS + hidden DOM scaffolding

Adds the media query, the top bar, and the projects container. After this task the desktop is unchanged and the new elements are present but inert (no JS drives them yet).

**Files:**
- Modify: `server.py` — CSS just before `</style>` (server.py:369); HTML just after `<body>` (server.py:371).

**Interfaces:**
- Produces: DOM ids `mobileTopbar`, `mobileBack`, `mobileTitle`, `mobileProjects`; CSS classes `.mobile-topbar`, `.mobile-projects`, `.mproj-list`, `.mproj-card`, `.mproj-name`, `.mproj-meta`; body classes `m-projects` / `m-sessions` / `m-messages`.

- [ ] **Step 1: Add the CSS block** immediately before the closing `</style>` at server.py:369:

```css
/* Mobile drill-down — hidden on desktop */
.mobile-topbar { display: none; }
.mobile-projects { display: none; }

@media (max-width: 768px) {
  body { display: block; height: auto; overflow: auto; }

  .mobile-topbar {
    display: flex; align-items: center; gap: 8px;
    position: sticky; top: 0; z-index: 10;
    background: #161b22; border-bottom: 1px solid #30363d;
    padding: 8px 12px; min-height: 48px;
  }
  .mobile-topbar .m-back {
    background: none; border: none; color: #58a6ff;
    font-size: 22px; line-height: 1; padding: 6px 10px;
    min-width: 44px; min-height: 44px; cursor: pointer;
  }
  .mobile-topbar .m-title {
    font-size: 15px; font-weight: 600; color: #e6edf3;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis;
  }

  /* Layout: full width, page scrolls, only one screen visible */
  .sidebar { width: 100%; min-width: 0; border-right: none; height: auto; }
  .sidebar-header .project-filter { display: none; }
  .session-list { flex: none; overflow: visible; }
  .main { min-width: 0; height: auto; }
  .messages { flex: none; overflow: visible; padding: 12px; }

  .mobile-projects, .sidebar, .main { display: none; }
  body.m-projects .mobile-projects { display: block; }
  body.m-sessions .sidebar { display: flex; }
  body.m-messages .main { display: flex; }

  .mobile-projects { padding: 12px; }
  .mproj-list { display: flex; flex-direction: column; gap: 8px; }
  .mproj-card {
    background: #161b22; border: 1px solid #30363d; border-radius: 8px;
    padding: 14px 16px; min-height: 44px; cursor: pointer;
  }
  .mproj-card:active { background: #1c2128; }
  .mproj-name { font-size: 15px; font-weight: 600; color: #58a6ff; }
  .mproj-meta { font-size: 12px; color: #8b949e; margin-top: 4px; }

  .session-item { padding: 14px 16px; min-height: 44px; }
}
```

- [ ] **Step 2: Add the DOM elements** immediately after `<body>` (server.py:371), before `<div class="sidebar">`:

```html
<div class="mobile-topbar" id="mobileTopbar">
  <button class="m-back" id="mobileBack" onclick="mobileBack()">&#8249;</button>
  <span class="m-title" id="mobileTitle">Breadcrumbs</span>
</div>
<div class="mobile-projects" id="mobileProjects"></div>
```

- [ ] **Step 3: Restart the server and verify desktop is unchanged.**

Run the server (see Verification section). Load at a wide window (>768px). Expected: sidebar + project summary table look exactly as before; the top bar and mobile cards are not visible.

- [ ] **Step 4: Verify the elements exist but are hidden.** In DevTools console: `getComputedStyle(document.getElementById('mobileTopbar')).display` → `"none"` at desktop width.

- [ ] **Step 5: Commit**

```bash
git add server.py
git commit -m "feat(ui): mobile CSS media query + top bar/projects scaffolding"
```

---

### Task 2: Mobile home screen (usage summary + project cards)

Renders the Projects screen and makes the page boot into it on phones. After this task, a narrow viewport shows the usage cards then tappable project cards; desktop still boots into the table.

**Files:**
- Modify: `server.py` — add JS functions inside `<script>` (near the other render functions, e.g. after `renderProjectSummary` at server.py:836); change the boot line at server.py:938.

**Interfaces:**
- Consumes: `computeProjectRows()` → array of rows with fields `name`, `last` (ISO string or null), `sess_total` (number); `renderUsageBanner()` (fills the element with id `usageBanner`); `esc()`.
- Produces: `renderMobileProjects()`, and a boot branch keyed on `matchMedia('(max-width: 768px)')`.

- [ ] **Step 1: Add `renderMobileProjects()`** after `renderProjectSummary()` (server.py:836):

```javascript
function renderMobileProjects() {
  var rows = computeProjectRows();
  var html = '<div id="usageBanner" class="usage-banner">Loading usage&#8230;</div>';
  html += '<div class="mproj-list">';
  rows.forEach(function(pr) {
    var meta = (pr.last ? 'Last active ' + pr.last.substring(0, 10) : 'No activity')
      + ' · ' + pr.sess_total + ' session' + (pr.sess_total === 1 ? '' : 's');
    html += '<div class="mproj-card" data-project="' + esc(pr.name) + '">'
      + '<div class="mproj-name">' + esc(pr.name) + '</div>'
      + '<div class="mproj-meta">' + meta + '</div>'
      + '</div>';
  });
  html += '</div>';
  document.getElementById('mobileProjects').innerHTML = html;
  renderUsageBanner();
}
```

- [ ] **Step 2: Change the boot line** at server.py:938 from:

```javascript
fetchSessions().then(function() { renderProjectSummary(); });
```

to:

```javascript
fetchSessions().then(function() {
  if (window.matchMedia('(max-width: 768px)').matches) {
    renderMobileProjects();
    document.body.classList.add('m-projects');
  } else {
    renderProjectSummary();
  }
});
```

- [ ] **Step 3: Restart the server; verify the mobile home at ~390px.** Expected: usage summary cards (stacked) followed by one card per project, each showing name · last-active date · session count. No wide table.

- [ ] **Step 4: Verify desktop still boots into the table** at >768px width. Expected: unchanged project summary table, no cards.

- [ ] **Step 5: Commit**

```bash
git add server.py
git commit -m "feat(ui): mobile projects home with usage summary and cards"
```

---

### Task 3: Drill-down navigation (screens + back button)

Wires tapping a project → its sessions → a session's transcript, with a back button that walks up. Completes the feature.

**Files:**
- Modify: `server.py` — add JS controller functions (after `renderMobileProjects`); add a delegated click listener and a hook in `selectSession()` (server.py:542-572).

**Interfaces:**
- Consumes: `renderSidebar()`, `selectSession()`, `selectedProject` (global), `sessions` (global), element id `projectFilter`, and the ids/classes from Task 1 & 2.
- Produces: `mobileScreen` (global string), `setMobileScreen(name, title?)`, `openProjectMobile(name)`, `mobileBack()`.

- [ ] **Step 1: Add the controller functions** after `renderMobileProjects()`:

```javascript
var mobileScreen = 'projects';

function setMobileScreen(name, title) {
  mobileScreen = name;
  document.body.classList.remove('m-projects', 'm-sessions', 'm-messages');
  document.body.classList.add('m-' + name);
  var back = document.getElementById('mobileBack');
  var titleEl = document.getElementById('mobileTitle');
  back.style.visibility = (name === 'projects') ? 'hidden' : 'visible';
  if (title != null) titleEl.textContent = title;
  else if (name === 'projects') titleEl.textContent = 'Breadcrumbs';
  else if (name === 'sessions') titleEl.textContent = selectedProject || 'Sessions';
  window.scrollTo(0, 0);
}

function openProjectMobile(name) {
  selectedProject = name;
  var pf = document.getElementById('projectFilter');
  if (pf) pf.value = name;
  renderSidebar();
  setMobileScreen('sessions');
}

function mobileBack() {
  if (mobileScreen === 'messages') setMobileScreen('sessions');
  else if (mobileScreen === 'sessions') setMobileScreen('projects');
}

document.getElementById('mobileProjects').addEventListener('click', function(e) {
  var card = e.target.closest('.mproj-card');
  if (card) openProjectMobile(card.dataset.project);
});
```

- [ ] **Step 2: Replace the boot branch's class-add with `setMobileScreen`.** In the boot line edited in Task 2, change `document.body.classList.add('m-projects');` to `setMobileScreen('projects');`. (Ensures the top-bar title/back state initialize correctly.)

- [ ] **Step 3: Hook `selectSession()`** — add at the very end of `selectSession()` (after the `try/catch`, before the closing `}` at server.py:572):

```javascript
  if (window.matchMedia('(max-width: 768px)').matches) {
    setMobileScreen('messages', session.name || 'Session');
  }
```

- [ ] **Step 4: Restart the server; verify the full drill-down at ~390px.** Expected sequence: home cards → tap a project → that project's session list (top bar shows project name, back arrow visible) → tap a session → full transcript (top bar shows session name) → back → sessions → back → home. Each `window.scrollTo(0,0)` puts you at the top.

- [ ] **Step 5: Verify desktop unaffected** at >768px: clicking projects/sessions behaves exactly as before; no screen-switching, no top bar.

- [ ] **Step 6: Commit**

```bash
git add server.py
git commit -m "feat(ui): mobile drill-down navigation with back button"
```

---

## Verification

Breadcrumbs runs as the systemd user unit `breadcrumbs` (see project memory `feedback_dev_loop`). After editing `server.py`:

```bash
systemctl --user restart breadcrumbs
systemctl --user status breadcrumbs --no-pager | head -5
```

(Or run directly for a scratch instance: `python3 server.py --port 8899` and open that port.)

Then, using the Chrome MCP tools:
1. Open the viewer URL in a new tab.
2. Resize the window to ~390px wide (phone) and walk Projects → Sessions → Messages → back up; screenshot each screen.
3. Resize to >768px and confirm the desktop sidebar + project table are unchanged.

## Self-Review Notes

- **Spec coverage:** Projects/Sessions/Messages screens (Tasks 1–3), usage summary on home (Task 2, `renderMobileProjects` + `renderUsageBanner`), top-bar back navigation (Task 3), desktop untouched (hidden-by-default CSS, `matchMedia` boot branch), no route changes (all in `HTML_PAGE`). Covered.
- **Duplicate `usageBanner` id:** avoided — on mobile boot `renderProjectSummary()` (the only other producer of that id) is not called.
- **Naming consistency:** `setMobileScreen` / `openProjectMobile` / `mobileBack` / `mobileScreen` / `renderMobileProjects` used identically across tasks; ids `mobileTopbar`/`mobileBack`/`mobileTitle`/`mobileProjects` consistent between Task 1 DOM and Task 3 JS.
