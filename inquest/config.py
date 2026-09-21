"""Paths and settings. Every writable location lives under the workspace root."""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

WORKSPACE = Path(os.environ.get("INQUEST_WORKSPACE", ROOT / ".inquest")).resolve()
CORPUS = Path(os.environ.get("INQUEST_CORPUS", ROOT / "corpus")).resolve()

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

SANDBOX = os.environ.get("INQUEST_SANDBOX", "local")        # "local" or "docker"
LLM_MODEL = os.environ.get("INQUEST_LLM_MODEL", "claude-opus-5")
LOCAL_LLM_URL = os.environ.get("INQUEST_LOCAL_LLM_URL", "")  # OpenAI-compatible endpoint
LOCAL_LLM_MODEL = os.environ.get("INQUEST_LOCAL_LLM_MODEL", "")

CPU_COUNT = os.cpu_count() or 2
THREADS_PER_RUN = int(os.environ.get("INQUEST_THREADS_PER_RUN", "1"))
WORKERS = int(os.environ.get("INQUEST_WORKERS", str(max(1, CPU_COUNT // max(1, THREADS_PER_RUN) // 2 or 1))))
RUN_TIMEOUT_S = int(os.environ.get("INQUEST_RUN_TIMEOUT", "180"))
RUN_MEMORY_MB = int(os.environ.get("INQUEST_RUN_MEMORY_MB", "4096"))

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
