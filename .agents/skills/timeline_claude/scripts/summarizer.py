"""Generate short summaries for Claude Code sessions.

Uses the Codex CLI with gpt-5.4-mini for one-shot summarization. Results cached
in ~/.claude/timeline-claude-summaries.json keyed by session_id (sessions are
append-only so once summarized, the result is stable).
"""
from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

CACHE_PATH = Path.home() / ".claude" / "timeline-claude-summaries.json"

PROMPT_TEMPLATE = """\
You are writing a one-line title for a coding session, for inclusion in a \
weekly timesheet. The session is on the "{project}" project.

Below are the first user messages (verbatim, in order). Treat them as the \
ground truth of what the user asked Claude to do. Ignore meta-instructions \
about skills, hooks, or system reminders.

---
{messages}
---

Write a 5-12 word summary, past tense, active voice, focused on the *outcome* \
(what was built/fixed/explored). No quotes. No leading "summary:" or similar. \
No trailing period. Just the summary line itself.
"""

ROLLUP_PROMPT_TEMPLATE = """\
You are consolidating a list of micro-summaries into a single tight daily \
entry for a timesheet. The work is on the "{project}" project on {date}.

Below are the per-session summaries from that day:

---
{bullets}
---

Output one line of dot-separated bullets (• A • B • C • …) that covers all \
distinct accomplishments from the day. Rules:
- Merge near-duplicates and successive iterations of the same task into one bullet.
- Drop trivial micro-iterations (small follow-ups, retries, re-runs).
- Keep meaningfully distinct work as separate bullets.
- Past tense, active voice, outcome-focused.
- No quotes. No headers. No trailing period. Just the bullet line.
- Aim short but don't drop important work — typically 3-8 bullets.
"""

DAY_PROMPT_TEMPLATE = """\
You are writing a one-line summary of work done on {date} in a coding session \
on the "{project}" project, for a weekly timesheet.

Below are the user messages the user sent on this specific day (verbatim, in \
order). The session may have extended across multiple days; ignore any work \
not represented here. Ignore meta-instructions about skills, hooks, or \
system reminders.

---
{messages}
---

Write a 5-12 word summary, past tense, active voice, focused on the *outcome* \
of the work done on this day. No quotes. No leading "summary:" or similar. \
No trailing period. Just the summary line itself.
"""


def _load_cache() -> dict[str, str]:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _save_cache(cache: dict[str, str]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(cache, indent=2, sort_keys=True))
    tmp.replace(CACHE_PATH)


def _build_prompt(project: str, user_texts: list[str], date: str | None = None) -> str:
    msgs = []
    for i, t in enumerate(user_texts[:8], 1):
        snippet = " ".join(t.split())  # collapse whitespace
        if len(snippet) > 800:
            snippet = snippet[:800] + "…"
        msgs.append(f"[user msg {i}] {snippet}")
    text = "\n".join(msgs)
    if date:
        return DAY_PROMPT_TEMPLATE.format(project=project, date=date, messages=text)
    return PROMPT_TEMPLATE.format(project=project, messages=text)


def _run_codex(prompt: str, timeout: int = 90) -> tuple[str | None, str | None]:
    """Call `codex exec <prompt>` and return (summary, error_reason).

    Returns (summary, None) on success, (None, "reason") on failure.
    """
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(prefix="timeline-claude-summary-", suffix=".txt", delete=False) as tmp:
            tmp_path = Path(tmp.name)
        result = subprocess.run(
            [
                "codex",
                "exec",
                "--model",
                "gpt-5.4-mini",
                "--ephemeral",
                "--skip-git-repo-check",
                "--sandbox",
                "read-only",
                "--color",
                "never",
                "--output-last-message",
                str(tmp_path),
                prompt,
            ],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
            env={**os.environ, "CI": "1"},  # suppress interactive prompts
        )
        if result.returncode != 0:
            return None, f"exit={result.returncode}: {result.stderr.strip()[:200]}"
        out = tmp_path.read_text().strip() if tmp_path and tmp_path.exists() else ""
        if not out:
            out = result.stdout.strip()
        if not out:
            return None, "empty stdout"
        # Defensive cleanup: drop quotes, trailing periods, leading bullets
        for prefix in ("• ", "- ", "* "):
            if out.startswith(prefix):
                out = out[len(prefix):]
        out = out.strip("\"'").rstrip(".").strip()
        out = out.splitlines()[0] if out else ""
        return (out, None) if out else (None, "blank after cleanup")
    except subprocess.TimeoutExpired:
        return None, f"timeout after {timeout}s"
    except FileNotFoundError:
        return None, "codex CLI not found"
    except OSError as e:
        return None, f"OSError: {e}"
    finally:
        if tmp_path:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError:
                pass


def summarize_sessions(
    sessions: list,  # list of Session objects with .session_id, .project, .user_texts
    max_workers: int = 8,
    use_cache: bool = True,
    progress: bool = True,
) -> dict[str, str]:
    """Fill in summaries for sessions. Returns the full session_id → summary map.

    Mutates each session's `.summary` attribute when a summary is obtained.
    """
    cache = _load_cache() if use_cache else {}
    todo: list = []
    for s in sessions:
        if not s.user_texts:
            continue
        cached = cache.get(s.session_id) if use_cache else None
        if cached:
            s.summary = cached
            continue
        todo.append(s)

    if not todo:
        return cache

    if progress:
        print(f"Summarizing {len(todo)} session(s) via `codex exec`…", file=sys.stderr)

    def worker(sess) -> tuple[str, str | None, str | None]:
        prompt = _build_prompt(sess.project, sess.user_texts)
        summary, err = _run_codex(prompt)
        return sess.session_id, summary, err

    done = 0
    failures: list[tuple[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(worker, s): s for s in todo}
        for fut in concurrent.futures.as_completed(futures):
            sess = futures[fut]
            try:
                sid, summary, err = fut.result()
            except Exception as e:
                sid, summary, err = sess.session_id, None, f"worker exc: {e}"
            done += 1
            if progress and (done % 5 == 0 or done == len(todo)):
                print(f"  {done}/{len(todo)}", file=sys.stderr)
            if summary:
                sess.summary = summary
                cache[sid] = summary
            elif err:
                failures.append((sid, err))

    if failures and progress:
        print(f"  {len(failures)} failed:", file=sys.stderr)
        for sid, err in failures[:5]:
            print(f"    {sid[:8]}… {err}", file=sys.stderr)
        if len(failures) > 5:
            print(f"    ...and {len(failures) - 5} more", file=sys.stderr)

    if failures:
        raise RuntimeError(f"{len(failures)} session summary request(s) failed")

    if use_cache:
        _save_cache(cache)
    return cache


def summarize_session_days(
    sessions: list,  # Session objects with .session_id, .project, .user_texts_by_date
    in_week_dates: set[str],  # ISO dates we care about: "YYYY-MM-DD"
    max_workers: int = 4,
    use_cache: bool = True,
    progress: bool = True,
) -> None:
    """Fill each session's .day_summaries[date_iso] for in-week dates with messages.

    Cache key format: "<session_id>|<date_iso>" so per-day summaries don't
    collide with whole-session summaries.
    """
    cache = _load_cache() if use_cache else {}
    todo: list[tuple] = []  # (session, date_iso)
    for s in sessions:
        for date_iso, texts in s.user_texts_by_date.items():
            if date_iso not in in_week_dates:
                continue
            if not texts:
                continue
            key = f"{s.session_id}|{date_iso}"
            cached = cache.get(key) if use_cache else None
            if cached:
                s.day_summaries[date_iso] = cached
                continue
            todo.append((s, date_iso))

    if not todo:
        return

    if progress:
        print(f"Summarizing {len(todo)} session-day(s) via `codex exec`…", file=sys.stderr)

    def worker(item) -> tuple[str, str, str | None, str | None]:
        sess, date_iso = item
        prompt = _build_prompt(sess.project, sess.user_texts_by_date[date_iso], date=date_iso)
        summary, err = _run_codex(prompt)
        return sess.session_id, date_iso, summary, err

    done = 0
    failures: list[tuple[str, str]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(worker, item): item for item in todo}
        for fut in concurrent.futures.as_completed(futures):
            sess, date_iso = futures[fut]
            try:
                sid, d_iso, summary, err = fut.result()
            except Exception as e:
                sid, d_iso, summary, err = sess.session_id, date_iso, None, f"worker exc: {e}"
            done += 1
            if progress and (done % 10 == 0 or done == len(todo)):
                print(f"  {done}/{len(todo)}", file=sys.stderr)
            if summary:
                sess.day_summaries[d_iso] = summary
                cache[f"{sid}|{d_iso}"] = summary
            elif err:
                failures.append((f"{sid[:8]}|{d_iso}", err))

    if failures and progress:
        print(f"  {len(failures)} failed:", file=sys.stderr)
        for key, err in failures[:5]:
            print(f"    {key} {err}", file=sys.stderr)

    if failures:
        raise RuntimeError(f"{len(failures)} session-day summary request(s) failed")

    if use_cache:
        _save_cache(cache)


def rollup_day_summaries(
    items: list[tuple[str, str, list[str]]],  # [(project, date_iso, bullets), ...]
    max_workers: int = 4,
    use_cache: bool = True,
    progress: bool = True,
    min_bullets_to_rollup: int = 2,
) -> dict[tuple[str, str], str]:
    """Second-pass summarization. For each (project, date) with multiple
    per-session bullets, consolidate into a tighter daily entry.

    Returns {(project, date_iso): consolidated_string}. Groups with one
    bullet pass through unchanged. Cache key includes bullet count so adding
    a new session re-triggers consolidation.
    """
    cache = _load_cache() if use_cache else {}
    result: dict[tuple[str, str], str] = {}
    todo: list[tuple[str, str, list[str]]] = []

    for project, date_iso, bullets in items:
        seen = set()
        uniq = []
        for b in bullets:
            k = b.strip().lower()
            if k and k not in seen:
                seen.add(k)
                uniq.append(b.strip())
        if not uniq:
            continue
        if len(uniq) < min_bullets_to_rollup:
            result[(project, date_iso)] = " ".join("• " + b for b in uniq)
            continue
        cache_key = f"rollup|{project}|{date_iso}|{len(uniq)}"
        if use_cache and cache_key in cache:
            result[(project, date_iso)] = cache[cache_key]
            continue
        todo.append((project, date_iso, uniq))

    if not todo:
        return result

    if progress:
        print(f"Rolling up {len(todo)} day-project group(s) via `codex exec`…", file=sys.stderr)

    def worker(item) -> tuple[str, str, str | None, str | None]:
        project, date_iso, bullets = item
        bullet_text = "\n".join("• " + b for b in bullets)
        prompt = ROLLUP_PROMPT_TEMPLATE.format(project=project, date=date_iso, bullets=bullet_text)
        summary, err = _run_codex(prompt, timeout=120)
        return project, date_iso, summary, err

    done = 0
    failures: list[str] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {pool.submit(worker, item): item for item in todo}
        for fut in concurrent.futures.as_completed(futures):
            project, date_iso, bullets = futures[fut]
            try:
                p, d, summary, err = fut.result()
            except Exception as e:
                p, d, summary, err = project, date_iso, None, f"worker exc: {e}"
            done += 1
            if progress and (done % 5 == 0 or done == len(todo)):
                print(f"  {done}/{len(todo)}", file=sys.stderr)
            if summary:
                result[(p, d)] = summary
                cache[f"rollup|{p}|{d}|{len(bullets)}"] = summary
            else:
                result[(project, date_iso)] = " ".join("• " + b for b in bullets)
                if err:
                    failures.append(f"{project}|{date_iso}: {err}")

    if failures and progress:
        print(f"  {len(failures)} rollup failure(s):", file=sys.stderr)
        for line in failures[:5]:
            print(f"    {line}", file=sys.stderr)

    if failures:
        raise RuntimeError(f"{len(failures)} day rollup request(s) failed")

    if use_cache:
        _save_cache(cache)
    return result
