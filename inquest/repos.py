"""Repository acquisition at a pinned commit, and patched working copies."""
from __future__ import annotations

import hashlib
import json
import shutil
import subprocess
import threading
import uuid
from pathlib import Path

from . import config


def _git(args: list[str], cwd: Path | None = None) -> str:
    out = subprocess.run(["git", *args], cwd=cwd, env=config.tool_env(), capture_output=True,
                         text=True, encoding="utf-8", errors="replace")
    if out.returncode != 0:
        raise RuntimeError(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout.strip()


def clone(paper_id: str, url: str, sha: str | None = None) -> tuple[Path, str]:
    """Clone `url` into the workspace and check out `sha` (or HEAD). Returns (path, sha)."""
    config.ensure_dirs()
    dest = config.REPOS / paper_id
    if not (dest / ".git").exists():
        if dest.exists():
            shutil.rmtree(dest)
        _git(["clone", "--quiet", url, str(dest)])
    if sha:
        current = _git(["rev-parse", "HEAD"], cwd=dest)
        if current != sha:
            _git(["fetch", "--quiet", "origin"], cwd=dest)
            _git(["checkout", "--quiet", sha], cwd=dest)
    return dest, _git(["rev-parse", "HEAD"], cwd=dest)


def head_sha(path: Path) -> str:
    return _git(["rev-parse", "HEAD"], cwd=path)


def commit_date(path: Path, sha: str = "HEAD") -> str:
    return _git(["show", "-s", "--format=%cI", sha], cwd=path)


def code_patch_hash(edits: list[dict]) -> str:
    if not edits:
        return ""
    blob = json.dumps(edits, sort_keys=True).encode()
    return hashlib.sha256(blob).hexdigest()[:16]


class PatchError(RuntimeError):
    pass


def apply_edits(root: Path, edits: list[dict]) -> None:
    """Apply find/replace edits in place.

    Each edit is {"file", "find", "replace", "probe"?}. `find` must occur exactly once.
    When `probe` is set, a Witness PATCH_HIT call is inserted before the replacement so
    that execution of the patched code is observed rather than assumed.
    """
    for e in edits:
        path = root / e["file"]
        if not path.exists():
            raise PatchError(f"{e['file']} does not exist")
        src = path.read_text(encoding="utf-8")
        count = src.count(e["find"])
        if count != 1:
            raise PatchError(f"{e['file']}: expected exactly one match for patch text, found {count}")
        idx = src.index(e["find"])
        src = src[:idx] + e["replace"] + src[idx + len(e["find"]):]
        probe = e.get("probe")
        if probe:
            # The probe goes on its own line directly above the replacement, at the
            # indentation of the replacement's first line.
            line_start = src.rfind("\n", 0, idx) + 1
            prefix = src[line_start:idx]
            if prefix.strip() == "":
                first = e["replace"].split("\n", 1)[0]
                indent = prefix + first[:len(first) - len(first.lstrip())]
                hit = f"{indent}__import__('sitecustomize').witness_hit({probe!r})\n"
                src = src[:line_start] + hit + src[line_start:]
        path.write_text(src, encoding="utf-8")


def worktree(paper_id: str, base: Path, edits: list[dict]) -> Path:
    """A copy of `base` with `edits` applied, cached by patch hash. No edits returns `base`."""
    if not edits:
        return base
    h = code_patch_hash(edits)
    dest = config.WORKTREES / paper_id / h
    marker = dest / ".inquest_patch.json"
    with _lock_for(str(dest)):
        if marker.exists():
            return dest
        if dest.exists():
            shutil.rmtree(dest)
        staging = dest.with_name(f"{h}.staging-{uuid.uuid4().hex[:6]}")
        shutil.copytree(base, staging, ignore=shutil.ignore_patterns(".git"))
        try:
            apply_edits(staging, edits)
        except Exception:
            shutil.rmtree(staging, ignore_errors=True)
            raise
        (staging / ".inquest_patch.json").write_text(json.dumps(edits, indent=1), encoding="utf-8")
        staging.rename(dest)
    return dest


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _lock_for(key: str) -> threading.Lock:
    with _locks_guard:
        return _locks.setdefault(key, threading.Lock())
