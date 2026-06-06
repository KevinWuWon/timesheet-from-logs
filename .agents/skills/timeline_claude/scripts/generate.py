#!/usr/bin/env python3
# /// script
# requires-python = ">=3.11"
# dependencies = [
#     "jinja2>=3.1.0",
# ]
# ///
"""Generate a weekly HTML timeline from Claude Code session logs.

See ../SKILL.md for usage.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import webbrowser
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from hashlib import sha1
from pathlib import Path
from typing import Iterable
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader

sys.path.insert(0, str(Path(__file__).parent))
from project_resolver import Resolver  # noqa: E402
from summarizer import summarize_sessions, summarize_session_days, rollup_day_summaries  # noqa: E402

SESSIONS_DIR = Path.home() / ".claude" / "projects"
TEMPLATE_DIR = Path(__file__).parent.parent / "templates"
DEFAULT_TZ = "Australia/Sydney"
HALFHOURS_PER_DAY = 48
TEN_MINUTES_PER_HALFHOUR = 3
MIN_ACTIVE_TEN_MINUTES_PER_HALFHOUR = 2


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class Session:
    session_id: str
    file_path: Path
    project: str = "unknown"
    cwd: str | None = None
    first_user_text: str = ""
    user_texts: list[str] = field(default_factory=list)
    # Per-local-date (YYYY-MM-DD) user texts, used for per-day summarization
    user_texts_by_date: dict[str, list[str]] = field(default_factory=dict)
    # Verbatim user messages with local timestamps: [{"ts", "date", "date_iso", "text"}]
    user_messages: list[dict] = field(default_factory=list)
    # Per-day summaries (date_iso -> summary text), populated by the summarizer
    day_summaries: dict[str, str] = field(default_factory=dict)
    summary: str = ""
    user_count: int = 0
    assistant_count: int = 0
    first_local: datetime | None = None
    last_local: datetime | None = None
    # Local-time 10-minute buckets touched: set of (iso_year, iso_week, weekday, tenminute)
    user_buckets: set[tuple[int, int, int, int]] = field(default_factory=set)
    assistant_buckets: set[tuple[int, int, int, int]] = field(default_factory=set)

    @property
    def title(self) -> str:
        if self.summary:
            return self.summary
        if self.first_user_text:
            t = self.first_user_text.strip().splitlines()[0]
            return t[:80] + ("…" if len(t) > 80 else "")
        return "(untitled session)"


@dataclass
class BucketStats:
    project_user: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    project_assistant: dict[str, int] = field(default_factory=lambda: defaultdict(int))
    project_user_segments: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))
    project_assistant_segments: dict[str, set[int]] = field(default_factory=lambda: defaultdict(set))
    session_ids: set[str] = field(default_factory=set)

    @property
    def user_total(self) -> int:
        return sum(self.project_user.values())

    @property
    def assistant_total(self) -> int:
        return sum(self.project_assistant.values())

    @property
    def qualifying_user_projects(self) -> set[str]:
        return {
            project
            for project, segments in self.project_user_segments.items()
            if len(segments) >= MIN_ACTIVE_TEN_MINUTES_PER_HALFHOUR
        }

    @property
    def qualifying_assistant_projects(self) -> set[str]:
        return {
            project
            for project, segments in self.project_assistant_segments.items()
            if len(segments) >= MIN_ACTIVE_TEN_MINUTES_PER_HALFHOUR
        }

    @property
    def is_recorded(self) -> bool:
        return self.is_hands_on or self.is_autonomous

    @property
    def is_hands_on(self) -> bool:
        return bool(self.qualifying_user_projects)

    @property
    def is_autonomous(self) -> bool:
        return (
            not self.is_hands_on
            and bool(self.qualifying_assistant_projects)
        )

    @property
    def dominant_project(self) -> str | None:
        if self.is_hands_on:
            return max(
                self.qualifying_user_projects,
                key=lambda p: (self.project_user[p], self.project_assistant[p]),
            )
        if self.is_autonomous:
            return max(
                self.qualifying_assistant_projects,
                key=lambda p: self.project_assistant[p],
            )
        combined: dict[str, int] = defaultdict(int)
        for p, c in self.project_user.items():
            combined[p] += c * 10  # weight hands-on heavily
        for p, c in self.project_assistant.items():
            combined[p] += c
        if not combined:
            return None
        return max(combined.items(), key=lambda kv: kv[1])[0]


# ---------------------------------------------------------------------------
# Parsing
# ---------------------------------------------------------------------------

# System-injected XML blocks that Claude Code inserts into user messages.
# These look like user content but were not typed by the user.
_SYSTEM_TAGS = (
    "task-notification",
    "system-reminder",
    "local-command-stdout",
    "local-command-stderr",
    "bash-stdout",
    "bash-stderr",
    "bash-input",
    "tool_use_error",
    "user-prompt-submit-hook",
    "ide_selection",
    "ide_opened_file",
)
_SYSTEM_TAG_RE = re.compile(
    r"<(" + "|".join(_SYSTEM_TAGS) + r")\b[^>]*>.*?</\1>",
    re.DOTALL | re.IGNORECASE,
)

_INTERNAL_SUMMARY_PROMPT_PREFIXES = (
    "You are writing a one-line title for a coding session",
    "You are writing a one-line summary of work done on",
    "You are consolidating a list of micro-summaries",
)


def _strip_system_content(text: str) -> str:
    """Remove system-injected XML blocks. Returns the user-typed remainder."""
    return _SYSTEM_TAG_RE.sub("", text).strip()


def _is_real_user_message(rec: dict) -> bool:
    """True if this is a human-typed user message (not a tool_result or
    system-injected notification)."""
    if rec.get("type") != "user":
        return False
    if rec.get("isMeta"):
        return False
    if rec.get("isCompactSummary"):
        return False
    if rec.get("isVisibleInTranscriptOnly"):
        return False
    msg = rec.get("message")
    if not isinstance(msg, dict):
        return False
    content = msg.get("content")
    if isinstance(content, str):
        return bool(_strip_system_content(content))
    if isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_result":
                return False
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                if _strip_system_content(block.get("text", "")):
                    return True
        return False
    return False


def _user_text(rec: dict) -> str:
    msg = rec.get("message", {})
    content = msg.get("content")
    raw = ""
    if isinstance(content, str):
        raw = content
    elif isinstance(content, list):
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                raw = block.get("text", "")
                break
    return _strip_system_content(raw)


def _is_internal_summary_session(sess: Session) -> bool:
    text = sess.first_user_text.lstrip()
    return any(text.startswith(prefix) for prefix in _INTERNAL_SUMMARY_PROMPT_PREFIXES)


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _current_workspace_roots() -> list[Path]:
    roots: list[Path] = []
    cwd = Path.cwd().resolve()
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        result = None

    if result and result.returncode == 0:
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                try:
                    roots.append(Path(line.removeprefix("worktree ")).expanduser().resolve())
                except OSError:
                    continue

    if cwd not in roots:
        roots.append(cwd)
    return roots


def _is_current_workspace_session(sess: Session, roots: list[Path]) -> bool:
    if not sess.cwd:
        return False
    try:
        cwd = Path(sess.cwd).expanduser().resolve()
    except OSError:
        return False
    return any(_is_relative_to(cwd, root) for root in roots)


def _parse_ts(ts: str) -> datetime | None:
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return None


def parse_session(file_path: Path, tz: ZoneInfo) -> Session | None:
    sess = Session(session_id=file_path.stem, file_path=file_path)
    try:
        with file_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue

                rtype = rec.get("type")

                if rtype == "summary" and rec.get("summary"):
                    sess.summary = rec["summary"]
                    continue

                if rtype not in ("user", "assistant"):
                    continue

                ts_utc = _parse_ts(rec.get("timestamp", ""))
                if not ts_utc:
                    continue
                ts_local = ts_utc.astimezone(tz)

                cwd = rec.get("cwd")
                if cwd and not sess.cwd:
                    sess.cwd = cwd

                if rtype == "user":
                    if not _is_real_user_message(rec):
                        continue
                    # Skip subagent/sidechain user messages (agent-to-agent)
                    if rec.get("isSidechain"):
                        continue
                    sess.user_count += 1
                    text = _user_text(rec)
                    if not sess.first_user_text:
                        sess.first_user_text = text
                    # Collect a few user messages (cap length) for summarization context
                    if len(sess.user_texts) < 8 and text.strip():
                        sess.user_texts.append(text[:1000])
                    date_iso = ts_local.date().isoformat()
                    if text.strip():
                        sess.user_texts_by_date.setdefault(date_iso, []).append(text[:1500])
                    # Verbatim user messages with timestamps
                    if text.strip() and len(sess.user_messages) < 200:
                        sess.user_messages.append({
                            "ts": ts_local.strftime("%H:%M:%S"),
                            "date": ts_local.strftime("%a %b %d"),
                            "date_iso": date_iso,
                            "text": text,
                        })
                    key = bucket_key(ts_local)
                    sess.user_buckets.add(key)
                elif rtype == "assistant":
                    sess.assistant_count += 1
                    key = bucket_key(ts_local)
                    sess.assistant_buckets.add(key)

                if sess.first_local is None or ts_local < sess.first_local:
                    sess.first_local = ts_local
                if sess.last_local is None or ts_local > sess.last_local:
                    sess.last_local = ts_local
    except OSError:
        return None

    if sess.user_count == 0 and sess.assistant_count == 0:
        return None
    return sess


def bucket_key(local_dt: datetime) -> tuple[int, int, int, int]:
    iso = local_dt.isocalendar()
    weekday = iso.weekday - 1  # 0=Mon..6=Sun
    tenminute = local_dt.hour * 6 + (local_dt.minute // 10)
    return (iso.year, iso.week, weekday, tenminute)


# ---------------------------------------------------------------------------
# Discovery & filtering
# ---------------------------------------------------------------------------

def discover_session_files(window_start_utc: datetime, window_end_utc: datetime) -> Iterable[Path]:
    if not SESSIONS_DIR.exists():
        return
    # mtime buffer: 1 day on each side so we don't miss late-flushed files.
    mtime_min = (window_start_utc - timedelta(days=1)).timestamp()
    mtime_max = (window_end_utc + timedelta(days=1)).timestamp()
    for d in SESSIONS_DIR.iterdir():
        if not d.is_dir():
            continue
        for f in d.glob("*.jsonl"):
            try:
                m = f.stat().st_mtime
            except OSError:
                continue
            if mtime_min <= m <= mtime_max:
                yield f


# ---------------------------------------------------------------------------
# Aggregation
# ---------------------------------------------------------------------------

@dataclass
class WeekData:
    iso_year: int
    iso_week: int
    start_date: date  # Monday
    end_date: date    # Sunday
    tz_name: str
    project_colors: dict[str, str]
    project_hands_on_hours: dict[str, float]
    project_autonomous_hours: dict[str, float]
    daily_hands_on: list[float]      # 7
    daily_autonomous: list[float]    # 7
    total_hands_on: float
    total_autonomous: float
    # grid[day][halfhour] = {"state": "hands_on"|"autonomous"|"idle", "project": str|None, ...}
    grid: list[list[dict]]
    sessions: list[dict]
    # Per-weekday list of {project, session_id, text} for the daily-summary row.
    daily_summary_chunks: list[list[dict]]
    generated_at: str
    session_count: int


PALETTE = [
    "#2563eb",  # blue
    "#16a34a",  # green
    "#dc2626",  # red
    "#9333ea",  # purple
    "#ea580c",  # orange
    "#0d9488",  # teal
    "#a16207",  # amber
    "#db2777",  # pink
]


def project_color(name: str, index_map: dict[str, int]) -> str:
    if name in index_map:
        return PALETTE[index_map[name] % len(PALETTE)]
    # Stable hash for projects beyond palette length
    h = int(sha1(name.encode()).hexdigest()[:6], 16) % 360
    return f"hsl({h} 60% 45%)"


def build_week(iso_year: int, iso_week: int, sessions: list[Session], tz: ZoneInfo,
               rollup_map: dict[tuple[str, str], str] | None = None) -> WeekData:
    # ISO week → Monday date
    monday = date.fromisocalendar(iso_year, iso_week, 1)
    sunday = monday + timedelta(days=6)

    # Initialize empty grid
    bucket_stats: dict[tuple[int, int], BucketStats] = defaultdict(BucketStats)

    week_sessions: list[Session] = []
    for s in sessions:
        in_week = False
        for key in s.user_buckets | s.assistant_buckets:
            y, w, day, tenminute = key
            if y == iso_year and w == iso_week:
                in_week = True
                hh = tenminute // TEN_MINUTES_PER_HALFHOUR
                segment = tenminute % TEN_MINUTES_PER_HALFHOUR
                bs = bucket_stats[(day, hh)]
                bs.session_ids.add(s.session_id)
                # Count this session's user vs assistant per project for this
                # half-hour, and track which 10-minute segments were active.
                if key in s.user_buckets:
                    bs.project_user_segments[s.project].add(segment)
                    bs.project_user[s.project] += 1
                if key in s.assistant_buckets:
                    bs.project_assistant_segments[s.project].add(segment)
                    bs.project_assistant[s.project] += 1
        if in_week:
            week_sessions.append(s)

    # Project color assignment
    projects_by_hours: dict[str, float] = defaultdict(float)
    for (day, hh), bs in bucket_stats.items():
        if bs.is_hands_on and bs.dominant_project:
            projects_by_hours[bs.dominant_project] += 0.5
    sorted_projects = sorted(projects_by_hours.items(), key=lambda kv: -kv[1])
    index_map = {name: i for i, (name, _) in enumerate(sorted_projects)}
    colors = {name: project_color(name, index_map) for name in projects_by_hours.keys()}
    for s in week_sessions:
        if s.project not in colors:
            colors[s.project] = project_color(s.project, index_map)

    # Per-project totals (hands-on and autonomous)
    hands_on = defaultdict(float)
    autonomous = defaultdict(float)
    for (day, hh), bs in bucket_stats.items():
        if bs.is_hands_on:
            hands_on[bs.dominant_project] += 0.5
        elif bs.is_autonomous:
            autonomous[bs.dominant_project] += 0.5

    # Daily totals
    daily_h = [0.0] * 7
    daily_a = [0.0] * 7
    for (day, hh), bs in bucket_stats.items():
        if bs.is_hands_on:
            daily_h[day] += 0.5
        elif bs.is_autonomous:
            daily_a[day] += 0.5

    # Build grid
    now_local = datetime.now(tz)
    today_key = (now_local.isocalendar().year, now_local.isocalendar().week)
    today_weekday = now_local.isocalendar().weekday - 1
    cur_halfhour = now_local.hour * 2 + (1 if now_local.minute >= 30 else 0)
    is_current_week = (today_key == (iso_year, iso_week))

    grid: list[list[dict]] = []
    for day in range(7):
        row = []
        for hh in range(HALFHOURS_PER_DAY):
            bs = bucket_stats.get((day, hh))
            cell: dict = {"day": day, "hh": hh}
            future = is_current_week and (day > today_weekday or (day == today_weekday and hh > cur_halfhour))
            if bs is None or not bs.is_recorded:
                cell["state"] = "future" if future else "idle"
                cell["project"] = None
                cell["session_ids"] = []
                cell["user_count"] = 0
                cell["assistant_count"] = 0
            else:
                cell["state"] = "hands_on" if bs.is_hands_on else "autonomous"
                cell["project"] = bs.dominant_project
                cell["session_ids"] = sorted(bs.session_ids)
                cell["user_count"] = bs.user_total
                cell["assistant_count"] = bs.assistant_total
                cell["color"] = colors.get(bs.dominant_project, "#777")
            row.append(cell)
        grid.append(row)

    # Session strip rows (only sessions with messages in this week).
    # Labels reflect activity within this week, not the session's lifetime.
    sessions_out = []
    # Build per-session ordered list of in-week timestamps.
    week_msgs_by_sess: dict[str, list[datetime]] = defaultdict(list)
    for s in week_sessions:
        for m in s.user_messages:
            # Reconstruct local datetime from the displayed strings is awkward;
            # instead derive from buckets — pick any bucket in this week.
            pass

    def earliest_in_week(s: Session) -> datetime | None:
        # Reconstruct candidate datetimes from 10-minute buckets. Buckets give
        # us only (year, week, weekday, tenminute). Pick the earliest in week.
        keys = [k for k in (s.user_buckets | s.assistant_buckets)
                if k[0] == iso_year and k[1] == iso_week]
        if not keys:
            return None
        y, w, day, tenminute = min(keys)
        d = date.fromisocalendar(y, w, day + 1)
        minute_of_day = tenminute * 10
        return datetime(d.year, d.month, d.day, minute_of_day // 60, minute_of_day % 60, tzinfo=tz)

    # Build per-weekday summary chunks. Prefer rolled-up text (one entry per
    # project per day); fall back to per-session bullets if rollup is missing.
    daily_chunks: list[list[dict]] = [[] for _ in range(7)]
    in_week_dates = {(monday + timedelta(days=i)).isoformat(): i for i in range(7)}
    rollup_map = rollup_map or {}

    # Group per-session day_summaries by (project, date) so we can decide
    # whether to use rollup or raw.
    sessions_by_pd: dict[tuple[str, str], list[str]] = defaultdict(list)
    for s in week_sessions:
        for date_iso, summary in s.day_summaries.items():
            if not summary:
                continue
            sessions_by_pd[(s.project, date_iso)].append(summary)

    for (project, date_iso), bullets in sessions_by_pd.items():
        wd = in_week_dates.get(date_iso)
        if wd is None:
            continue
        text = rollup_map.get((project, date_iso))
        if not text:
            # No rollup (single bullet, rollup disabled, or rollup failed)
            text = " ".join("• " + b for b in dict.fromkeys(bullets))
        daily_chunks[wd].append({
            "project": project,
            "text": text,
        })

    for s in sorted(week_sessions, key=lambda s: earliest_in_week(s) or datetime.max.replace(tzinfo=tz)):
        if not s.first_local or not s.last_local:
            continue
        in_week_start = earliest_in_week(s)
        # Determine in-week last timestamp from buckets too
        keys = [k for k in (s.user_buckets | s.assistant_buckets)
                if k[0] == iso_year and k[1] == iso_week]
        y, w, day, tenminute = max(keys)
        d = date.fromisocalendar(y, w, day + 1)
        minute_of_day = tenminute * 10
        in_week_end = datetime(d.year, d.month, d.day, minute_of_day // 60, minute_of_day % 60, tzinfo=tz)
        if in_week_start is None:
            continue
        # Choose representative day for the daily summary: earliest bucket weekday
        wd = in_week_start.isocalendar().weekday - 1
        dur_hours = (in_week_end - in_week_start).total_seconds() / 3600
        # Filter the visible verbatim messages to those in this week
        msgs_in_week = []
        for m in s.user_messages:
            try:
                # m["date"] is "Mon May 04" — compare against in-week range
                msg_dt = datetime.strptime(
                    f"{in_week_start.year} {m['date']} {m['ts']}",
                    "%Y %a %b %d %H:%M:%S",
                ).replace(tzinfo=tz)
            except ValueError:
                msgs_in_week.append(m)
                continue
            if monday <= msg_dt.date() <= sunday:
                msgs_in_week.append(m)
        # Prefer the per-day summary for the in-week start date; fall back to
        # the whole-session summary, then to first user message.
        day_iso = in_week_start.date().isoformat()
        row_title = s.day_summaries.get(day_iso) or s.title
        sessions_out.append({
            "id": s.session_id,
            "project": s.project,
            "color": colors.get(s.project, "#777"),
            "day_label": in_week_start.strftime("%a %b %d"),
            "weekday": wd,
            "time_range": f"{in_week_start.strftime('%H:%M')}–{in_week_end.strftime('%H:%M')}",
            "duration_label": format_duration(dur_hours),
            "user_count": s.user_count,
            "assistant_count": s.assistant_count,
            "title": row_title,
            "messages": msgs_in_week,
        })

    total_h = sum(hands_on.values())
    total_a = sum(autonomous.values())

    return WeekData(
        iso_year=iso_year,
        iso_week=iso_week,
        start_date=monday,
        end_date=sunday,
        tz_name=str(tz),
        project_colors=colors,
        project_hands_on_hours=dict(hands_on),
        project_autonomous_hours=dict(autonomous),
        daily_hands_on=daily_h,
        daily_autonomous=daily_a,
        total_hands_on=total_h,
        total_autonomous=total_a,
        grid=grid,
        sessions=sessions_out,
        daily_summary_chunks=daily_chunks,
        generated_at=datetime.now(tz).strftime("%Y-%m-%d %H:%M %Z"),
        session_count=len(week_sessions),
    )


def format_duration(hours: float) -> str:
    if hours < 0.05:
        return "<5m"
    h = int(hours)
    m = int(round((hours - h) * 60))
    if m == 60:
        h += 1
        m = 0
    if h == 0:
        return f"{m}m"
    if m == 0:
        return f"{h}h"
    return f"{h}h {m}m"


# ---------------------------------------------------------------------------
# Week resolution
# ---------------------------------------------------------------------------

def resolve_week(arg: str | None, tz: ZoneInfo) -> tuple[int, int]:
    now = datetime.now(tz).date()
    if arg in (None, "current"):
        iso = now.isocalendar()
        return iso.year, iso.week
    if arg == "last":
        ref = now - timedelta(days=7)
        iso = ref.isocalendar()
        return iso.year, iso.week
    # Try parsing as YYYY-MM-DD
    try:
        d = date.fromisoformat(arg)
    except ValueError as e:
        raise SystemExit(f"Invalid --week value: {arg!r}. Use 'current', 'last', or YYYY-MM-DD.") from e
    iso = d.isocalendar()
    return iso.year, iso.week


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def render(week: WeekData, out_path: Path, prev_link: str | None, next_link: str | None) -> None:
    env = Environment(
        loader=FileSystemLoader(str(TEMPLATE_DIR)),
        autoescape=True,
    )
    env.filters["fmtdur"] = format_duration
    env.filters["hours"] = lambda h: f"{h:.1f}"
    tpl = env.get_template("report.html.j2")
    html = tpl.render(
        week=week,
        prev_link=prev_link,
        next_link=next_link,
        day_labels=["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        halfhours=list(range(HALFHOURS_PER_DAY)),
        hour_marks=list(range(0, 24, 3)),
        projects=sorted(
            week.project_hands_on_hours.keys() | week.project_autonomous_hours.keys(),
            key=lambda p: -(week.project_hands_on_hours.get(p, 0) + week.project_autonomous_hours.get(p, 0)),
        ),
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(html)


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    g = parser.add_mutually_exclusive_group()
    g.add_argument("--week", default=None, help="'current' (default), 'last', or YYYY-MM-DD")
    g.add_argument("--from", dest="from_date", help="Start date YYYY-MM-DD (use with --to)")
    g.add_argument("--last-4-weeks", action="store_true", help="Generate 4 sibling HTMLs with prev/next links")
    parser.add_argument("--to", dest="to_date", help="End date YYYY-MM-DD (use with --from)")
    parser.add_argument("--tz", default=DEFAULT_TZ, help=f"Timezone (default: {DEFAULT_TZ})")
    parser.add_argument("--out", default=None, help="Output HTML path")
    parser.add_argument("--no-cache", action="store_true", help="Bypass project-resolver cache")
    parser.add_argument("--summary-workers", type=int, default=4, help="Parallel `codex exec` workers")
    parser.add_argument("--open", dest="open_browser", action="store_true", help="Open result in browser")
    args = parser.parse_args()

    try:
        tz = ZoneInfo(args.tz)
    except Exception:
        print(f"Unknown timezone: {args.tz}", file=sys.stderr)
        return 2

    resolver = Resolver(use_cache=not args.no_cache)

    # Determine weeks to render
    weeks: list[tuple[int, int]] = []
    if args.last_4_weeks:
        ref = datetime.now(tz).date()
        for i in range(4):
            iso = (ref - timedelta(days=7 * i)).isocalendar()
            weeks.append((iso.year, iso.week))
        weeks.reverse()
    elif args.from_date:
        if not args.to_date:
            print("--from requires --to", file=sys.stderr)
            return 2
        start = date.fromisoformat(args.from_date)
        end = date.fromisoformat(args.to_date)
        d = start
        seen: set[tuple[int, int]] = set()
        while d <= end:
            iso = d.isocalendar()
            key = (iso.year, iso.week)
            if key not in seen:
                seen.add(key)
                weeks.append(key)
            d += timedelta(days=7)
    else:
        weeks.append(resolve_week(args.week, tz))

    # Compute UTC window covering all weeks
    earliest_monday = min(date.fromisocalendar(y, w, 1) for y, w in weeks)
    latest_sunday = max(date.fromisocalendar(y, w, 1) for y, w in weeks) + timedelta(days=6)
    window_start = datetime.combine(earliest_monday, datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)
    window_end = datetime.combine(latest_sunday + timedelta(days=1), datetime.min.time(), tzinfo=tz).astimezone(timezone.utc)

    # Discover & parse
    session_files = list(discover_session_files(window_start, window_end))
    ignored_workspace_roots = _current_workspace_roots()
    sessions: list[Session] = []
    for f in session_files:
        s = parse_session(f, tz)
        if s is None:
            continue
        if _is_internal_summary_session(s):
            continue
        if _is_current_workspace_session(s, ignored_workspace_roots):
            continue
        s.project = resolver.resolve(s.cwd)
        if s.first_local and s.last_local:
            sessions.append(s)
    resolver.flush()

    # Filter to sessions that touch any requested week
    week_set = set(weeks)
    rel_sessions: list[Session] = []
    for s in sessions:
        ws = {(y, w) for (y, w, _, _) in (s.user_buckets | s.assistant_buckets)}
        if ws & week_set:
            rel_sessions.append(s)

    # Summarize sessions (cached).
    if rel_sessions:
        # Whole-session summary (used for session-strip titles)
        summarize_sessions(rel_sessions, max_workers=args.summary_workers,
                           use_cache=not args.no_cache)
        # Per-day summaries (used for the daily-summary row)
        in_week_dates = set()
        for y, w in weeks:
            monday = date.fromisocalendar(y, w, 1)
            for i in range(7):
                in_week_dates.add((monday + timedelta(days=i)).isoformat())
        summarize_session_days(rel_sessions, in_week_dates,
                               max_workers=args.summary_workers,
                               use_cache=not args.no_cache)

    # Build (project, date_iso) → list of per-session summaries, then roll up
    rollup_map: dict[tuple[str, str], str] = {}
    if rel_sessions:
        groups: dict[tuple[str, str], list[str]] = defaultdict(list)
        for s in rel_sessions:
            for date_iso, summary in s.day_summaries.items():
                if not summary:
                    continue
                groups[(s.project, date_iso)].append(summary)
        items = [(p, d, bullets) for (p, d), bullets in groups.items()]
        rollup_map = rollup_day_summaries(items, max_workers=args.summary_workers,
                                           use_cache=not args.no_cache)

    # Determine output paths up front (for prev/next linking)
    def out_path_for(y: int, w: int) -> Path:
        if args.out and len(weeks) == 1:
            return Path(args.out).expanduser().resolve()
        return Path.cwd() / "outputs" / f"timeline-claude-{y}-W{w:02d}.html"

    week_paths = [(y, w, out_path_for(y, w)) for (y, w) in weeks]

    for i, (y, w, out) in enumerate(week_paths):
        week = build_week(y, w, rel_sessions, tz, rollup_map=rollup_map)
        prev_link = week_paths[i - 1][2].name if i > 0 else None
        next_link = week_paths[i + 1][2].name if i + 1 < len(week_paths) else None
        render(week, out, prev_link, next_link)
        print(str(out))

    if args.open_browser and week_paths:
        webbrowser.open(week_paths[-1][2].as_uri())

    return 0


if __name__ == "__main__":
    sys.exit(main())
