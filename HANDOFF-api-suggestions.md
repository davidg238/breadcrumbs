# Breadcrumbs MCP — API suggestions from a real "where did we leave off?" query

**From:** nemt project session, 2026-04-13
**Context:** I (Claude Code) was asked "where are we up to?" at the start of a new session and used breadcrumbs MCP to recover end-of-session state from the prior day. The tools worked but had rough edges. These suggestions come from that concrete usage.

## The query pattern

Very common opening move for a returning session:

> User: "where are we up to?" / "what's the status?" / "check breadcrumbs yesterday"

The answer lives in roughly the **last 5–20 messages** of the most recent session: the final assistant summary, any trailing user decisions, what was committed, what's pending. Memory files often disagree or lag behind, and `git log` shows commits but not the decision context or the "what's next" that was discussed.

## What I actually did (and where it was clunky)

1. `list_sessions(project="nemt", since, until)` → got the session ID. **Worked well.**
2. `get_session_messages(session_id)` → **failed**: 223k chars, exceeded token limit. Saved to a tool-results file.
3. Tried `jq` on the dump, hit ripgrep/head issues, eventually `jq -r '.[0].text' | tail -c 20000` gave me the closing exchange.
4. Synthesized status from the last ~50 messages.

Total: ~5 tool calls and a file-wrangling detour for what is conceptually "show me the tail of yesterday's session."

## Suggested additions (ranked by leverage/simplicity)

### 1. `get_session_tail(session_id, n_messages=20)` — HIGHEST LEVERAGE
Return only the last N messages. Pure slice of existing data, no synthesis required.

- **Why it helps:** The "status" query never needs the whole transcript. It needs the closing exchange. This collapses 5 tool calls + file wrangling into 1 call.
- **Default N:** 20 is probably right. User + assistant messages only (same default as `get_session_messages`).
- **Implementation:** trivial — reverse-order SELECT with LIMIT, then reverse the result for display.

### 2. `list_sessions` with inline `last_user_message` / `last_assistant_message` previews
Add optional preview fields (say, first 500 chars of each) to the existing `list_sessions` response, behind a flag like `include_previews=true`.

- **Why it helps:** Lets me skim "what happened yesterday / this week" across multiple sessions without fetching any. Often the preview alone answers the status question.
- **Implementation:** one extra JOIN or subquery per session row. Gate behind flag so default response stays lean.

### 3. `get_session_summary(session_id)` — HIGHEST USEFULNESS, MOST DESIGN WORK
Server-side synthesized summary. Candidate fields:
- `first_user_prompt` (truncated)
- `last_user_prompt` (truncated)
- `last_assistant_message` (truncated)
- `tool_call_count` by tool name
- `files_modified` — unique paths from Edit/Write/Bash tool calls if detectable
- `git_commits` — commits made during the session if detectable from Bash output
- `duration_seconds`, `message_count` (already have these)

- **Why it helps:** Answers "what did we do in this session?" without shipping the transcript. Great for longer sessions (the 1064-message one I hit would have fit in a single response as a summary).
- **Tradeoff:** Requires design decisions about what counts as "files modified" and how to detect commits. Heuristics over Bash tool outputs are fragile. Could start simple (just the message previews + tool counts) and grow.

## Nice-to-haves (lower priority)

- **`get_session_messages` pagination** — `offset` + `limit` params so large sessions are readable without the save-to-file dance. The current save-to-file fallback works but is awkward.
- **Filter by tool name** in `get_session_messages` — e.g., `tool_names=["Bash"]` to see only shell commands run, or `types=["user"]` to see only user prompts (the `types` param exists but I'm not sure it filters tool-call messages).
- **`search_messages` with `session_id` filter** — search within a single session, not just across all.

## What to keep the same

- `list_sessions` response shape is good: session_id, name, project, model, timestamps, duration, message_count, cost. Don't change that.
- Date-range filtering (`since`/`until`) on `list_sessions` worked well.
- Project filtering worked well.

## Concrete ask

If you want to pick one: **implement `get_session_tail` first.** It's the smallest change that eliminates the biggest pain point, and it's composable — if I want more, I call it with larger N. The other two can come later.

## Reference: the actual session transcript sample

The session in question:
- `session_id`: 9eed6262-d660-454a-aab5-943a5e215ccc
- 1064 messages, 213 USD, 9h 52m
- The useful "status" info was entirely in the last ~15 exchanges (merge, DB purge, server restart, memory updates, final commit).
- Everything before that was implementation detail I didn't need at session-open time.

This is a representative case, not an edge case — long work sessions with a short meaningful tail are the norm for coding work.
