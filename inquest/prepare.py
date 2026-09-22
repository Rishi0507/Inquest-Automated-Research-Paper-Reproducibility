"""Repository and environment preparation for a paper (component C1)."""
from __future__ import annotations

import shlex
from pathlib import Path

from . import config, corpus, env


def entry_script(adapter, repo: Path) -> Path | None:
    """The script named in the adapter command, resolved against the working directory."""
    for tok in shlex.split(adapter.command, posix=True)[1:]:
        if tok.endswith(".py"):
            return (repo / adapter.workdir / tok).resolve()
    return None


def prepare(paper_id: str, log=print, rebuild: bool = False) -> dict:
    paper = corpus.get(paper_id)
    if paper.parent_id:
        return prepare(paper.parent_id, log=log, rebuild=rebuild)
    paper.ensure_pdf()
    repo = paper.ensure_repo()
    log(f"repository at {paper.meta.get('repo_sha', 'HEAD')[:12]}")
    if env.ready(paper.env_id) and not rebuild:
        log("environment already built")
        return env.fingerprint(paper.env_id)
    adapter = paper.adapter()
    entries, search = None, None
    if adapter is not None:
        e = entry_script(adapter, repo)
        entries = [e] if e else None
        search = [(repo / p).resolve() for p in adapter.pythonpath]
    lock_dir = paper.dir if paper.source == "corpus" else config.UPLOADS / paper_id
    lock_dir.mkdir(parents=True, exist_ok=True)
    return env.build(paper.env_id, repo, paper.meta["paper_date"], lock_path=lock_dir / "env.lock",
                     entries=entries, search=search, log=log)
