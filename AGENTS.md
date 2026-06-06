# AGENTS.md

This file is the canonical guidance for Codex agents working in this repository.

## Repository Overview

This is a single-file web application for analyzing Claude conversation logs to estimate daily work hours. The entire application is contained in `index.html` with embedded CSS and JavaScript.

## Architecture

### Single HTML File Design
- **Self-contained**: Everything in one HTML file with embedded CSS/JavaScript
- **ESM Modules**: Uses modern ES module imports for Chart.js via CDN
- **TypeScript-ready**: Full JSDoc typing for `tsc --allowJs --checkJs` validation
- **No build process**: Runs directly in browser

### Data Flow
```
JSONL Files -> Parse Messages -> Gap Analysis -> Session Grouping -> Daily Aggregation -> Visualizations
```

## Development Commands

Since this is a single HTML file with no build process:

```bash
# Run type checking (if TypeScript is installed)
tsc --allowJs --checkJs index.html

# Serve locally for testing
python3 -m http.server 8000
# or
npx serve .
```

## Core Implementation Details

### Key Algorithms

1. **Statistical Gap Analysis**: Uses 75th percentile of message gaps to automatically detect breaks
2. **Reading Time Estimation**: Accounts for code comprehension and tool usage with multipliers
3. **Work Day Logic**: 5am boundary handles late-night sessions (counts toward previous day)
4. **Session Clustering**: Groups related conversations based on calculated break threshold

### Important Constants (in index.html)
- `READING_SPEED_WPM = 200`
- `GAP_PERCENTILE = 75`
- `MIN_BREAK_MINUTES = 15`
- `WORK_DAY_START_HOUR = 5`
- `MAX_DAILY_HOURS = 16`

### File Structure
The Claude conversation logs are expected to be in JSONL format from `~/.claude/projects/` with this structure:

```json
{"timestamp":"2025-06-22T10:30:00.000Z","type":"user","sessionId":"abc123",...}
{"timestamp":"2025-06-22T10:30:15.000Z","type":"assistant","sessionId":"abc123",...}
```

## Key Functions to Know

- `analyzeWorkHours()`: Main analysis entry point
- `analyzeGaps()`: Calculates break threshold using percentiles
- `groupIntoSessions()`: Clusters messages into work sessions
- `estimateReadingTime()`: Calculates reading time with content-aware multipliers
- `getWorkDay()`: Handles 5am work day boundary logic
- `createWorkPatternHeatmap()`: Generates 15-minute interval visualization

## Testing Approach

Since this is a client-side only application:
1. Test with real JSONL files from `~/.claude/projects/`
2. Verify visualizations render correctly in browser
3. Check console for parsing errors or warnings
4. Validate type safety with `tsc --allowJs --checkJs`

## Browser Compatibility

Requires modern browser with:
- ES module support
- CSS Grid
- Drag & drop API
- FileReader API
