# Claude Work Hours Analyzer

A sophisticated web application that analyzes Claude conversation logs to estimate daily work hours and visualize work patterns. Uses intelligent algorithms to automatically detect breaks and estimate actual time spent working based on conversation data.

## Quick Start

1. Open `work_hours_analyzer.html` in any modern browser
2. Drag and drop your Claude conversation JSONL files (found in `~/.claude/projects/`)
3. Add multiple files across multiple drag operations
4. Click "Analyze Files" when ready
5. View your work patterns across three interactive visualizations

## Architecture Overview

### Single HTML File Design
- **Self-contained**: Everything in one HTML file with embedded CSS/JavaScript
- **ESM Modules**: Uses modern ES module imports for Chart.js
- **TypeScript-ready**: Full JSDoc typing for `tsc --allowJs --checkJs` validation
- **No build process**: Runs directly in browser with CDN dependencies

### Data Flow
```
JSONL Files → Parse Messages → Gap Analysis → Session Grouping → Daily Aggregation → Visualizations
```

## Core Algorithms

### 1. Statistical Gap Analysis (Break Detection)

**Purpose**: Automatically determine when you took breaks vs. active work time.

**Algorithm**:
```javascript
// Collect all gaps between consecutive messages
gaps = messages.map((msg, i) => timestamp(i+1) - timestamp(i))

// Statistical analysis
p75 = percentile(gaps, 75)
breakThreshold = max(p75, 15_minutes)
```

**Key Insights**:
- Uses 75th percentile of all message gaps as break threshold
- Minimum 15-minute threshold prevents false positives
- Self-adapting to personal work patterns
- Filters out gaps > 24 hours (different days)

### 2. Multi-Factor Reading Time Estimation

**Purpose**: Account for time spent reading/processing Claude's responses.

**Algorithm**:
```javascript
baseReadingTime = wordCount / 200_WPM * 60_seconds

// Content type multipliers
if (hasToolUsage) baseReadingTime *= 1.5    // Decision/processing time
if (hasCodeBlocks) baseReadingTime *= 2.0   // Code comprehension

// Bounds
readingTime = clamp(baseReadingTime, 10_seconds, 5_minutes)
```

**Rationale**:
- 200 WPM = industry standard reading speed
- Tool usage requires decision-making time
- Code blocks need deeper comprehension
- Bounds prevent outliers from skewing results

### 3. Work Session Clustering

**Purpose**: Group related conversations into work sessions.

**Algorithm**:
```javascript
sessions = []
currentSession = [firstMessage]

for (message in messages) {
    gap = message.timestamp - previousMessage.timestamp
    
    if (gap > breakThreshold) {
        sessions.push(createSession(currentSession))
        currentSession = [message]
    } else {
        currentSession.push(message)
    }
}
```

**Session Time Calculation**:
```javascript
sessionTime = (lastMessage - firstMessage) + sum(readingTimes)
estimatedHours = max(sessionTime, 5_minutes) / 3600_seconds
```

### 4. Work Day Logic (5am Boundary)

**Purpose**: Handle late-night work sessions that cross midnight.

**Algorithm**:
```javascript
function getWorkDay(timestamp) {
    workDay = new Date(timestamp)
    if (workDay.getHours() < 5) {
        workDay.setDate(workDay.getDate() - 1)  // Previous day
    }
    return workDay.toISOString().split('T')[0]
}
```

**Benefits**:
- Late night coding (12am-4am) counts toward previous day
- More accurate representation of actual work sessions
- Handles developers' irregular schedules

### 5. Heatmap Interval Mapping

**Purpose**: Visualize work patterns in 15-minute increments.

**Algorithm**:
```javascript
// 96 intervals = 24 hours * 4 quarters
intervals = new Array(96).fill(0)

// Map session to intervals (5am = hour 0)
startMinutes = (startHour - 5 + 24) % 24 * 60 + startMinutes
endMinutes = (endHour - 5 + 24) % 24 * 60 + endMinutes

startInterval = floor(startMinutes / 15)
endInterval = floor(endMinutes / 15)

// Mark all intervals during session
for (i = startInterval; i <= endInterval; i++) {
    intervals[i] += 1  // Count overlapping sessions
}
```

## Data Structures

### Core Types
```typescript
interface ConversationMessage {
    timestamp: string;           // ISO timestamp
    type: 'user' | 'assistant' | 'summary';
    sessionId: string;          // Conversation identifier
    message?: object;           // Message content
    durationMs?: number;        // Response generation time
    costUSD?: number;          // API cost
}

interface WorkSession {
    startTime: Date;
    endTime: Date;
    estimatedHours: number;     // Calculated work time
    messages: ConversationMessage[];
    sessionId: string;
}

interface DailyStats {
    date: string;              // YYYY-MM-DD (work day format)
    totalHours: number;        // Capped at 16 hours max
    sessions: WorkSession[];
    messageCount: number;
}

interface IntervalData {
    date: string;
    intervals: number[];       // 96 intervals (15-min each)
    sessions: WorkSession[];
}
```

## File Processing

### JSONL Format
Claude stores conversations as JSON Lines:
```json
{"timestamp":"2025-06-22T10:30:00.000Z","type":"user","sessionId":"abc123",...}
{"timestamp":"2025-06-22T10:30:15.000Z","type":"assistant","sessionId":"abc123",...}
```

### File Accumulation
- Drag multiple files across multiple operations
- Deduplicates by filename
- Shows file count and preview
- Manual "Analyze" trigger for control

### Error Handling
- Skips malformed JSON lines
- Validates file extensions (.jsonl)
- Graceful degradation for missing fields

## Visualizations

### 1. Daily Hours Bar Chart
- X-axis: Dates in d/m format (compact)
- Y-axis: Estimated work hours
- Responsive design for mobile/desktop

### 2. Gap Distribution Histogram
- Shows message gap patterns
- Helps validate break threshold selection
- 20 bins, capped at 2 hours max

### 3. Work Pattern Heatmap
- Rows: Each work day (including empty days)
- Columns: 15-minute intervals (5am-4am)
- Color intensity: Session overlap count
- Weekend highlighting (amber background)
- Sticky headers for easy navigation
- Tooltips show exact times and session counts

## User Interface

### File Upload
```html
<div class="upload-zone">
    <!-- Drag & drop or click to select -->
    <button id="uploadButton">Select Files</button>
    <button id="analyzeButton">Analyze Files</button>
    <div id="filesList"><!-- File preview --></div>
</div>
```

### Statistics Dashboard
- Total days, hours, average
- Maximum day, break threshold
- Session count

### Responsive Design
- Mobile-first CSS Grid
- Horizontal scroll for heatmap
- Compact layouts for small screens

## Development

### Type Checking
```bash
# Validate JSDoc types
tsc --allowJs --checkJs work_hours_analyzer.html
```

### Browser Compatibility
- Modern browsers with ES module support
- No build process required
- CDN dependencies for offline capability

### Debugging
- Console warnings for malformed data
- Error boundaries with user feedback
- Loading states for better UX

## Algorithm Validation

### Assumptions
- 200 WPM reading speed is reasonable average
- 75th percentile gap threshold works for most users
- 5am work day boundary fits developer schedules
- 16-hour daily cap prevents data errors

### Limitations
- Cannot detect non-Claude work time
- Assumes reading time correlates with complexity
- May overestimate for skimming behaviors
- Break detection depends on consistent patterns

### Tuning Parameters
Key constants that can be adjusted:
```javascript
const READING_SPEED_WPM = 200;
const GAP_PERCENTILE = 75;
const MIN_BREAK_MINUTES = 15;
const WORK_DAY_START_HOUR = 5;
const MAX_DAILY_HOURS = 16;
const MIN_SESSION_MINUTES = 5;
```

## Future Enhancements

### Potential Features
- Export data to CSV/JSON
- Date range filtering
- Multiple break threshold testing
- Work pattern analytics (most productive hours)
- Integration with other time tracking tools
- Team usage aggregation

### Algorithm Improvements
- Machine learning for personalized reading speeds
- Context-aware break detection (project switching)
- Productivity scoring based on conversation quality
- Seasonal pattern analysis

## Troubleshooting

### Common Issues
1. **No data showing**: Check JSONL file format and timestamps
2. **Unrealistic hours**: Verify 5am work day logic fits your schedule
3. **Missing sessions**: Lower break threshold if gaps are too small
4. **Performance issues**: Browser may struggle with >1000 days of data

### Data Quality
- Expects chronologically ordered messages
- Handles missing fields gracefully
- Validates timestamp formats
- Filters out obvious outliers (>24h gaps)