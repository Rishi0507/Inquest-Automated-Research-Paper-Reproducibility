"""Paths and settings. Every writable location lives under the workspace root."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _env(name: str, default):
    """Environment value, treating an empty string as unset."""
    value = os.environ.get(name, "")
    return value if value.strip() else default


WORKSPACE = Path(_env("INQUEST_WORKSPACE", ROOT / ".inquest")).resolve()
CORPUS = Path(_env("INQUEST_CORPUS", ROOT / "corpus")).resolve()

REPOS = WORKSPACE / "repos"          # pinned clones, one per paper
WORKTREES = WORKSPACE / "worktrees"  # patched copies, keyed by code patch hash
ENVS = WORKSPACE / "envs"            # per-paper interpreter environments
RUNS = WORKSPACE / "runs"            # per-run artefacts
UPLOADS = WORKSPACE / "uploads"      # registered papers (unseen path)
EXPORTS = WORKSPACE / "exports"      # dossiers and figures
TMP = WORKSPACE / "tmp"
DB_PATH = WORKSPACE / "inquest.sqlite3"

UV_CACHE = WORKSPACE / "cache" / "uv"
UV_PYTHON = WORKSPACE / "python"

WITNESS_DIR = Path(__file__).resolve().parent / "witness"

SANDBOX = _env("INQUEST_SANDBOX", "local")        # "local" or "docker"
LLM_MODEL = _env("INQUEST_LLM_MODEL", "claude-opus-5")
LOCAL_LLM_URL = _env("INQUEST_LOCAL_LLM_URL", "")  # Ollama server, e.g. http://localhost:11434
LOCAL_LLM_MODEL = _env("INQUEST_LOCAL_LLM_MODEL", "")

CPU_COUNT = os.cpu_count() or 2
THREADS_PER_RUN = int(_env("INQUEST_THREADS_PER_RUN", "1"))
WORKERS = int(_env("INQUEST_WORKERS", str(max(1, CPU_COUNT // max(1, THREADS_PER_RUN) // 2 or 1))))
RUN_TIMEOUT_S = int(_env("INQUEST_RUN_TIMEOUT", "420"))
RUN_MEMORY_MB = int(_env("INQUEST_RUN_MEMORY_MB", "4096"))

STANDING_NOTE = (
    "Execution ran on CPU in a pinned environment. The authors' hardware is unknown."
)


def ensure_dirs() -> None:
    for p in (WORKSPACE, REPOS, WORKTREES, ENVS, RUNS, UPLOADS, EXPORTS, TMP, UV_CACHE, UV_PYTHON):
        p.mkdir(parents=True, exist_ok=True)


def tool_env() -> dict[str, str]:
    """Environment for helper processes (uv, git) that keeps caches and temp files on the workspace drive."""
    ensure_dirs()
    env = dict(os.environ)
    env["UV_CACHE_DIR"] = str(UV_CACHE)
    env["UV_PYTHON_INSTALL_DIR"] = str(UV_PYTHON)
    env["TMP"] = env["TEMP"] = env["TMPDIR"] = str(TMP)
    env["PIP_CACHE_DIR"] = str(WORKSPACE / "cache" / "pip")
    return env
