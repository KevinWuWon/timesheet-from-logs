# AGENTS.md

This file is the canonical guidance for agents working in this repository. `CLAUDE.md` should remain a symlink to this file so Claude Code and Codex read the same instructions.

## Repository Overview

This repository is now a small toolkit for turning local agent session logs into work-hour and timeline reports. It is no longer Claude-specific.

There are two main surfaces:

- `index.html`: a self-contained browser app for dropping JSONL conversation logs and estimating daily work hours.
- `.agents/skills/`: local agent skills that generate weekly HTML timelines from real session stores.

Current skills:

- `timeline_claude`: reads Claude Code logs from `~/.claude/projects/`.
- `timeline_codex`: reads Codex logs from `~/.codex/sessions/` and `~/.codex/archived_sessions/`.

## Architecture

### Browser Analyzer

- **Single file**: `index.html` contains the HTML, CSS, and JavaScript.
- **No build process**: runs directly in a modern browser.
- **ESM modules**: imports Chart.js from a CDN.
- **Client-side only**: reads user-selected JSONL files with the browser FileReader API.

Data flow:

```text
JSONL files -> parse messages -> gap analysis -> session grouping -> daily aggregation -> visualizations
```

### Timeline Skills

Each timeline skill contains:

- `SKILL.md`: trigger guidance and usage notes.
- `scripts/generate.py`: CLI entry point for building reports.
- `scripts/project_resolver.py`: maps session working directories to canonical project names and collapses worktrees.
- `scripts/summarizer.py`: creates cached summaries via `codex exec`.
- `templates/report.html.j2`: self-contained HTML report template.

Skill data flow:

```text
Agent session logs -> parse sessions -> resolve projects -> bucket activity -> summarize -> render weekly HTML
```

## Development Commands

Serve the browser analyzer:

```bash
python3 -m http.server 8000
```

Generate Codex timelines from the repository root:

```bash
uv run .agents/skills/timeline_codex/scripts/generate.py
uv run .agents/skills/timeline_codex/scripts/generate.py --week last
uv run .agents/skills/timeline_codex/scripts/generate.py --last-4-weeks
```

Generate Claude timelines from the repository root:

```bash
uv run .agents/skills/timeline_claude/scripts/generate.py
uv run .agents/skills/timeline_claude/scripts/generate.py --week last
uv run .agents/skills/timeline_claude/scripts/generate.py --last-4-weeks
```

Useful options for both generators:

- `--tz TIMEZONE`: local timezone, default `Australia/Sydney`.
- `--out PATH`: output HTML path, default `./outputs/timeline-<source>-YYYY-Www.html`.
- `--no-cache`: bypass project resolver and summary caches.
- `--summary-workers N`: number of parallel `codex exec` summarization workers.
- `--open`: open the generated report in the default browser.

## Core Implementation Details

### Browser Analyzer Algorithms

1. **Statistical gap analysis**: uses the 75th percentile of message gaps to detect breaks.
2. **Reading time estimation**: accounts for code comprehension and tool usage with multipliers.
3. **Work day logic**: 5am boundary counts late-night sessions toward the previous work day.
4. **Session clustering**: groups nearby messages into work sessions based on the calculated break threshold.
5. **Heatmap mapping**: renders 15-minute intervals for daily work patterns.

Important constants in `index.html`:

- `READING_SPEED_WPM = 200`
- `GAP_PERCENTILE = 75`
- `MIN_BREAK_MINUTES = 15`
- `WORK_DAY_START_HOUR = 5`
- `MAX_DAILY_HOURS = 16`

### Timeline Skill Counting Rule

The timeline generators divide each day into 10-minute activity segments. Each visible half-hour grid cell contains three 10-minute segments.

A project counts as hands-on in a half-hour cell when that same project has user activity in at least two of the three 10-minute segments. If hands-on activity does not qualify, the cell counts as autonomous only when the same project has assistant activity in at least two segments.

This keeps totals tied to observed activity while separating direct user interaction from agent-only runtime.

## Data Sources

Supported log sources:

- Claude Code JSONL logs in `~/.claude/projects/`.
- Codex JSONL logs in `~/.codex/sessions/`.
- Codex archived JSONL logs in `~/.codex/archived_sessions/`.
- Manually selected JSONL files in the browser analyzer.

Expected records include an ISO timestamp, a message role/type, and a session identifier when available. The parsers handle source-specific shapes internally.

## Testing Approach

For `index.html`:

1. Serve the repo locally and open `http://localhost:8000/`.
2. Test with representative JSONL files from supported session stores.
3. Verify charts and heatmaps render correctly.
4. Check the browser console for parsing warnings or runtime errors.

For timeline skills:

1. Run the generator for `--week last` or a known active week.
2. Confirm the output HTML appears under `outputs/`.
3. Inspect project grouping, hands-on/autonomous totals, and daily summaries.
4. Re-run with `--no-cache` when validating parser or summarizer changes.

## Browser Compatibility

`index.html` requires a modern browser with:

- ES module support
- CSS Grid
- Drag and drop API
- FileReader API
