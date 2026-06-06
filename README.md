# Agent Timeline and Work Hours Toolkit

Tools for turning local AI coding-session logs into work-hour estimates and weekly timeline reports. The project started as a Claude conversation analyzer, but now supports both Claude Code and Codex workflows.

## What Is Included

- `index.html`: a standalone browser app for dropping JSONL conversation logs and visualizing estimated daily work hours.
- `.agents/skills/timeline_claude`: a local skill that generates weekly HTML timelines from Claude Code logs in `~/.claude/projects/`.
- `.agents/skills/timeline_codex`: a local skill that generates weekly HTML timelines from Codex logs in `~/.codex/sessions/` and `~/.codex/archived_sessions/`.
- `outputs/`: generated timeline reports.

## Quick Start

### Browser Analyzer

```bash
python3 -m http.server 8000
```

Open `http://localhost:8000/`, drop JSONL files into the page, and click **Analyze Files**.

The browser app is fully client-side. It does not upload logs anywhere.

### Codex Timeline

```bash
uv run .agents/skills/timeline_codex/scripts/generate.py --week last
```

### Claude Timeline

```bash
uv run .agents/skills/timeline_claude/scripts/generate.py --week last
```

Generated reports are written to `outputs/` by default.

## Timeline Generator Usage

Both timeline generators support the same common options:

```bash
# Current ISO week
uv run .agents/skills/timeline_codex/scripts/generate.py

# A specific ISO week, using any date in that week
uv run .agents/skills/timeline_codex/scripts/generate.py --week 2026-05-08

# Last week
uv run .agents/skills/timeline_codex/scripts/generate.py --week last

# Date range
uv run .agents/skills/timeline_codex/scripts/generate.py --from 2026-04-27 --to 2026-05-10

# Multiple recent weeks with prev/next links
uv run .agents/skills/timeline_codex/scripts/generate.py --last-4-weeks
```

Replace `timeline_codex` with `timeline_claude` to generate from Claude Code logs.

Options:

- `--tz TIMEZONE`: local timezone, default `Australia/Sydney`.
- `--out PATH`: output HTML path.
- `--no-cache`: bypass project resolver and summary caches.
- `--summary-workers N`: parallel `codex exec` workers for summaries.
- `--open`: open the generated report in the default browser.

## How The Timelines Work

The generators parse local session logs, resolve each session to a canonical project, and render a self-contained weekly HTML report.

Key behavior:

- Groups activity by project, including common worktree layouts.
- Shows a half-hour grid in local time.
- Distinguishes hands-on user activity from autonomous assistant activity.
- Summarizes sessions and day/project workstreams with cached `codex exec` calls.
- Excludes timeline-generation sessions from this repository so reports do not count their own maintenance work.

Counting rule:

```text
day -> 10-minute activity segments -> half-hour grid cells
```

Each half-hour cell contains three 10-minute segments. A project counts as hands-on when user activity appears in at least two segments for that project. If hands-on activity does not qualify, the cell can count as autonomous when assistant activity appears in at least two segments for that project.

## Browser Analyzer

The browser app in `index.html` keeps the original exploratory workflow: upload JSONL files manually and inspect daily work estimates, gap distribution, and work-pattern heatmaps.

Data flow:

```text
JSONL files -> parse messages -> gap analysis -> session grouping -> daily aggregation -> visualizations
```

Core algorithms:

- **Statistical gap analysis**: uses the 75th percentile of message gaps as the break threshold, with a minimum 15-minute break.
- **Reading time estimation**: adds estimated reading/comprehension time for assistant responses, with extra weight for code and tool usage.
- **Session clustering**: groups nearby messages into work sessions.
- **Work day boundary**: treats activity before 5am as part of the previous work day.
- **Heatmap mapping**: visualizes activity in 15-minute intervals.

Important constants in `index.html`:

```javascript
const READING_SPEED_WPM = 200;
const GAP_PERCENTILE = 75;
const MIN_BREAK_MINUTES = 15;
const WORK_DAY_START_HOUR = 5;
const MAX_DAILY_HOURS = 16;
```

## Data Sources

Supported sources:

- Claude Code: `~/.claude/projects/`
- Codex: `~/.codex/sessions/`
- Codex archived sessions: `~/.codex/archived_sessions/`
- Manual JSONL file selection in the browser

The skill parsers handle source-specific message shapes. The browser analyzer expects JSONL records with timestamps and user/assistant-style message roles.

## Development

There is no build step for `index.html`; serve the repository and test in a modern browser.

For skill changes, run a generator against a known active week:

```bash
uv run .agents/skills/timeline_codex/scripts/generate.py --week last --no-cache
uv run .agents/skills/timeline_claude/scripts/generate.py --week last --no-cache
```

Check the generated HTML under `outputs/` for project grouping, hands-on/autonomous totals, summaries, and navigation links.

## Requirements

- Python 3.11+
- `uv`
- `jinja2` (installed by `uv` from the script metadata)
- `codex` CLI for summary generation
- A modern browser for `index.html`

## Troubleshooting

- **No timeline data**: confirm the source log directory exists and contains sessions for the requested week.
- **Unexpected project names**: check `scripts/project_resolver.py` in the relevant skill.
- **Stale summaries**: rerun with `--no-cache`.
- **Browser analyzer shows no data**: verify the selected files are JSONL and include valid timestamps.
- **Unrealistic browser estimates**: adjust the constants in `index.html` for your working style.
