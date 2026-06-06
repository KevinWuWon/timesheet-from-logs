"""Resolve a session cwd to a canonical project key.

Order:
1. Path-pattern rules (regex) — fast, works even when cwd is gone.
2. Live `git remote get-url origin` lookup if path exists on disk.
3. Fallback to last path segment, or 'unknown'.

Results cached in ~/.claude/timeline-claude-cache.json by absolute cwd.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

CACHE_PATH = Path.home() / ".claude" / "timeline-claude-cache.json"

# Rules: (compiled regex, group_name)
# Order matters — first match wins. Most specific first.
RULES: list[tuple[re.Pattern[str], str]] = [
    # ~/worktrees/<proj>/<task...>
    (re.compile(r"^/Users/[^/]+/worktrees/(?P<proj>[^/]+)(?:/|$)"), "proj"),
    # ~/Code/<proj>/.claude-worktrees/<branch>
    (re.compile(r"^/Users/[^/]+/Code/(?P<proj>[^/]+)/\.claude-worktrees/"), "proj"),
    # ~/.cline-worktrees/<hash>-<proj>
    (re.compile(r"^/Users/[^/]+/\.cline-worktrees/[0-9a-f]+-(?P<proj>[^/]+)(?:/|$)"), "proj"),
    # ~/.codex-worktrees/<hash>-<proj>
    (re.compile(r"^/Users/[^/]+/\.codex-worktrees/[0-9a-f]+-(?P<proj>[^/]+)(?:/|$)"), "proj"),
    # ~/.t3-worktrees/<proj>-...-<hash>  — proj is the leading segment
    (re.compile(r"^/Users/[^/]+/\.t3-worktrees/(?P<proj>[a-z][a-z0-9-]*?)-[a-z0-9]+-[0-9a-f]+(?:/|$)"), "proj"),
    # ~/Code/<proj>[/...] — plain project checkout (after .claude-worktrees rule above)
    (re.compile(r"^/Users/[^/]+/Code/(?P<proj>[^/]+)(?:/|$)"), "proj"),
]


def _load_cache() -> dict[str, str]:
    if CACHE_PATH.exists():
        try:
            return json.loads(CACHE_PATH.read_text())
        except (OSError, json.JSONDecodeError):
            return {}
    return {}


def _save_cache(cache: dict[str, str]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(cache, indent=2, sort_keys=True))


def _git_remote_basename(cwd: str) -> str | None:
    try:
        out = subprocess.run(
            ["git", "-C", cwd, "remote", "get-url", "origin"],
            capture_output=True, text=True, timeout=2, check=False,
        )
        if out.returncode != 0:
            return None
        url = out.stdout.strip()
        if not url:
            return None
        # git@host:owner/repo.git or https://host/owner/repo[.git]
        name = url.rstrip("/").rsplit("/", 1)[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return name or None
    except (OSError, subprocess.SubprocessError):
        return None


def _resolve_uncached(cwd: str) -> str:
    for pattern, group in RULES:
        m = pattern.match(cwd)
        if m:
            return m.group(group)

    if os.path.isdir(cwd):
        name = _git_remote_basename(cwd)
        if name:
            return name

    # Fallback: last non-empty segment
    parts = [p for p in cwd.rstrip("/").split("/") if p]
    return parts[-1] if parts else "unknown"


class Resolver:
    def __init__(self, use_cache: bool = True) -> None:
        self.use_cache = use_cache
        self.cache = _load_cache() if use_cache else {}
        self.dirty = False

    def resolve(self, cwd: str | None) -> str:
        if not cwd:
            return "unknown"
        if self.use_cache and cwd in self.cache:
            return self.cache[cwd]
        result = _resolve_uncached(cwd)
        if self.use_cache:
            self.cache[cwd] = result
            self.dirty = True
        return result

    def flush(self) -> None:
        if self.use_cache and self.dirty:
            _save_cache(self.cache)
            self.dirty = False
