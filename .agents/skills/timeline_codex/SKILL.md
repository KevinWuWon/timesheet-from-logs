---
name: timeline_codex
description: Generate weekly HTML timelines from Codex session logs in ~/.codex/sessions and ~/.codex/archived_sessions. Use when user asks "what did I work on this week", wants to fill out a timesheet, needs hours-per-project breakdown, or asks to visualize/summarize Codex session activity. Groups by project (collapsing worktrees), shows half-hour grid in local timezone, distinguishes hands-on time from autonomous Codex runs.
---

# Codex Timeline Generator

Generates a self-contained weekly HTML timeline from Codex JSONL logs in `~/.codex/sessions/` and `~/.codex/archived_sessions/`, grouped by project in a half-hour grid. It distinguishes hands-on user-message time from autonomous Codex-only activity and uses `codex exec --model gpt-5.4-mini` for session summaries.

The generator reads `session_meta.payload.cwd` first and returns before parsing transcript messages when the recorded `cwd` is the current working directory or another git worktree of the same repository. When run from this repository, that specifically excludes `claude_work_hours` sessions and its Codex worktrees.

For efficiency, summaries and visible transcript snippets are built only from real user messages. Assistant/tool records are scanned through a timestamp-only fast path so autonomous-time cells still work without decoding large response, reasoning, or tool payloads.

## Counting Rule

The day is divided into 10-minute activity segments. Each visible half-hour grid cell contains three 10-minute segments. Segment counts are tracked separately per project: a cell counts as hands-on only when the same project has user activity in at least two of those three segments. If hands-on does not qualify, the cell counts as autonomous only when the same project has assistant activity in at least two of the three segments.

## Usage

Run from the user's current directory (output goes to `./outputs/` unless `--out` is set):

**Current week (default):**
```bash
command uv run scripts/generate.py
```

**Specific week (any date in that ISO week):**
```bash
command uv run scripts/generate.py --week 2026-05-08
```

**Last week:**
```bash
command uv run scripts/generate.py --week last
```

**Arbitrary date range:**
```bash
command uv run scripts/generate.py --from 2026-04-27 --to 2026-05-10
```

**Last N weeks (emits sibling files with prev/next links):**
```bash
command uv run scripts/generate.py --last-4-weeks
```

**Options:**
- `--tz TIMEZONE` — local timezone (default: `Australia/Sydney`).
- `--out PATH` — output HTML path (default: `./outputs/timeline-codex-YYYY-Www.html`).
- `--no-cache` — bypass the project-resolver + summary caches.
- `--summary-workers N` — parallel `codex exec` workers (default 4).
- `--open` — open the result in the default browser after writing.

## Preflight

- `command -v uv` (required).
- `~/.codex/sessions/` exists (created automatically by Codex on first use).
