"""Sandboxed runner with a content-hash cache and a worker pool (component C3)."""
from __future__ import annotations

import hashlib
import json
import os
import shlex
import shutil
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Callable, Iterable

from . import config, corpus, env, repos, sandbox, store
from .schemas import AdapterSpec, RunRequest, RunResult
from .witness import reader

CODE_KEY = "__code__"

_pool: ThreadPoolExecutor | None = None
_pool_lock = threading.Lock()
_listeners: list[Callable[[str, dict], None]] = []


def pool() -> ThreadPoolExecutor:
    global _pool
    with _pool_lock:
        if _pool is None:
            _pool = ThreadPoolExecutor(max_workers=config.WORKERS, thread_name_prefix="inquest-run")
        return _pool


def subscribe(fn: Callable[[str, dict], None]) -> Callable[[], None]:
    _listeners.append(fn)
    return lambda: _listeners.remove(fn) if fn in _listeners else None


def _notify(event: str, data: dict) -> None:
    for fn in list(_listeners):
        try:
            fn(event, data)
        except Exception:
            pass


def canonical(cfg: dict) -> str:
    return json.dumps(cfg, sort_keys=True, separators=(",", ":"), default=str)


def _env_digest(paper: corpus.Paper, adapter: AdapterSpec) -> str:
    if config.SANDBOX == "docker" and adapter.image:
        return adapter.image
    lock = config.CORPUS / paper.env_id / "env.lock"
    if lock.exists():
        return hashlib.sha256(lock.read_bytes()).hexdigest()[:16]
    fp = env.fingerprint(paper.env_id) if env.ready(paper.env_id) else {}
    return hashlib.sha256(json.dumps(fp.get("packages", []), sort_keys=True).encode()).hexdigest()[:16]


def cache_key(req: RunRequest, paper: corpus.Paper | None = None, adapter: AdapterSpec | None = None) -> str:
    paper = paper or corpus.get(req.paper_id)
    adapter = adapter or paper.adapter()
    cfg = dict(req.config)
    edits = cfg.pop(CODE_KEY, [])
    parts = [req.paper_id, _env_digest(paper, adapter), paper.repo_sha, repos.code_patch_hash(edits),
             paper.meta.get("fault_hash", ""), canonical(cfg), str(req.seed), str(req.hash_seed or ""),
             canonical(adapter.model_dump(include={"command", "flags", "workdir", "seed_flag", "seed_env", "pythonpath"}))]
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _format(value, kind: str | None) -> str:
    if kind == "bool":
        return "True" if value in (True, "true", "True", 1) else "False"
    if kind == "int":
        return str(int(value))
    if kind == "float":
        return repr(float(value))
    return str(value)


def build_command(adapter: AdapterSpec, cfg: dict, seed: int | None) -> list[str]:
    cfg = {k: v for k, v in cfg.items() if k != CODE_KEY}
    template = adapter.command
    used = set()
    for key, value in cfg.items():
        token = "{" + key + "}"
        if token in template:
            template = template.replace(token, _format(value, adapter.config_types.get(key)))
            used.add(key)
    if seed is not None and "{seed}" in template:
        template = template.replace("{seed}", str(seed))
        used.add("seed")
    argv = shlex.split(template, posix=True)
    for key, value in cfg.items():
        if key in used:
            continue
        # A key may also be a command-line flag itself, as observed by the Witness in argparse.
        flag = adapter.flags.get(key) or (key if key.startswith("-") else None)
        if not flag:
            raise ValueError(f"config key {key!r} has no command-line flag in the adapter")
        kind = adapter.config_types.get(key, "")
        if kind.startswith("flag"):
            if value in (True, "true", "True", 1):
                argv.append(flag)
        else:
            argv += [flag, _format(value, kind)]
    if seed is not None and adapter.seed_flag and "seed" not in used:
        argv += [adapter.seed_flag, str(seed)]
    return argv


def _run_env(paper: corpus.Paper, adapter: AdapterSpec, repo_root: Path, run_dir: Path, seed: int | None,
             hash_seed: str | None = None) -> dict:
    e = {k: v for k, v in os.environ.items() if not k.startswith(("PYTHON", "VIRTUAL_ENV", "CONDA"))}
    threads = str(config.THREADS_PER_RUN)
    extra = [str((repo_root / p).resolve()) for p in adapter.pythonpath]
    metric_fns = [m.witness_fn for m in adapter.metrics.values() if m.witness_fn and ":" in m.witness_fn]
    e.update({
        "PYTHONPATH": os.pathsep.join([str(config.WITNESS_DIR), *extra]),
        # Python's hash seed follows the run seed: each seed is a reproducible draw of the
        # per-process hash randomisation that an unconfigured Python 3 process would get.
        "PYTHONHASHSEED": hash_seed if hash_seed is not None else str(seed if seed is not None else 0),
        "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8",
        "CUBLAS_WORKSPACE_CONFIG": ":4096:8",
        "OMP_NUM_THREADS": threads, "MKL_NUM_THREADS": threads, "OPENBLAS_NUM_THREADS": threads,
        "NUMEXPR_NUM_THREADS": threads, "INQUEST_THREADS": threads,
        "WITNESS_OUT": str(run_dir / "witness.jsonl"), "WITNESS_PAYLOAD_DIR": str(run_dir / "payloads"),
        "WITNESS_REPO_ROOT": str(repo_root), "WITNESS_METRIC_FNS": ",".join(metric_fns),
        "WITNESS_DETERMINISTIC": "1", "INQUEST_NO_NETWORK": "1",
        "CUDA_VISIBLE_DEVICES": "", "MPLBACKEND": "Agg",
        "TMP": str(run_dir / "tmp"), "TEMP": str(run_dir / "tmp"), "TMPDIR": str(run_dir / "tmp"),
    })
    if adapter.seed_env and seed is not None:
        e[adapter.seed_env] = str(seed)
    return e


def execute(req: RunRequest, timeout: int | None = None) -> RunResult:
    """Run one configuration and seed. Every call goes through the cache unless `force` is set."""
    paper = corpus.get(req.paper_id)
    adapter = paper.adapter()
    if adapter is None:
        raise RuntimeError(f"{req.paper_id} has no adapter")
    key = cache_key(req, paper, adapter)
    store.bump("runs_requested")
    if not req.force:
        hit = store.get_run(key)
        if hit:
            store.bump("cache_hits")
            res = RunResult.model_validate(hit)
            res.cached = True
            _notify("run", {"paper_id": req.paper_id, "cached": True, "run_id": res.run_id})
            return res
    if not env.ready(paper.env_id):
        raise RuntimeError(f"environment for {paper.env_id} is not built")
    cfg = dict(req.config)
    edits = cfg.pop(CODE_KEY, [])
    base = paper.repo_path
    root = repos.worktree(paper.paper_id, base, edits) if edits else base
    run_id = uuid.uuid4().hex[:12]
    run_dir = config.RUNS / run_id
    (run_dir / "tmp").mkdir(parents=True, exist_ok=True)
    argv = build_command(adapter, cfg, req.seed)
    interp = str(env.interpreter(paper.env_id))
    if argv and argv[0] in ("python", "python3"):
        argv[0] = interp
    cwd = (root / adapter.workdir).resolve()
    stdout_path = run_dir / "stdout.txt"
    (run_dir / "command.json").write_text(json.dumps({"argv": argv, "cwd": str(cwd), "config": req.config,
                                                       "seed": req.seed}, indent=1, default=str), encoding="utf-8")
    _notify("run_start", {"paper_id": req.paper_id, "run_id": run_id, "seed": req.seed})
    ex = sandbox.execute(argv, cwd, root, _run_env(paper, adapter, root, run_dir, req.seed, req.hash_seed), stdout_path, run_dir,
                         image=adapter.image, timeout=timeout)
    shutil.rmtree(run_dir / "tmp", ignore_errors=True)
    store.bump("runs_executed")
    stdout = stdout_path.read_text(encoding="utf-8", errors="replace") if stdout_path.exists() else ""
    events = reader.load(run_dir / "witness.jsonl")
    metrics, preds, sources, errors = {}, {}, {}, []
    for name, spec in adapter.metrics.items():
        b = reader.bind_metric(spec, events, stdout)
        if b["value_raw"] is None:
            errors.append(f"metric {name} not found ({b['binding']})")
            continue
        metrics[name] = b["value_raw"] * spec.scale
        sources[name] = b["source"]
        if b["event"] is not None and b["event"].payload_path:
            preds[name] = b["event"].payload_path
    ok = ex.returncode == 0 and not ex.timed_out and bool(metrics)
    if ex.timed_out:
        errors.insert(0, f"timed out after {timeout or config.RUN_TIMEOUT_S}s")
    elif ex.returncode != 0:
        tail = "\n".join(stdout.strip().splitlines()[-6:])
        errors.insert(0, f"exit status {ex.returncode}: {tail}")
    primary = next(iter(adapter.metrics), "metric")
    fp = env.fingerprint(paper.env_id) if env.ready(paper.env_id) else {}
    fp = {**{k: v for k, v in fp.items() if k != "packages"}, "backend": ex.backend,
          "threads": config.THREADS_PER_RUN, "repo_sha": paper.repo_sha,
          "code_patch": repos.code_patch_hash(edits)}
    result = RunResult(
        run_id=run_id, cache_key=key, seed=req.seed, metric_name=primary,
        metric_value=metrics.get(primary, float("nan")), metric_scale=adapter.metrics[primary].scale
        if primary in adapter.metrics else 1.0,
        predictions_path=preds.get(primary), witness_path=str(run_dir / "witness.jsonl"),
        wall_seconds=round(ex.wall_seconds, 3), stdout_path=str(stdout_path), env_fingerprint=fp, cached=False,
        ok=ok, error="; ".join(errors) or None, metrics=metrics, predictions=preds, metric_sources=sources,
        config=req.config, hash_seed=req.hash_seed,
    )
    (run_dir / "result.json").write_text(result.model_dump_json(indent=1), encoding="utf-8")
    if ok:
        store.put_run(key, run_id, req.paper_id, req.seed, req.config, result.model_dump())
    _notify("run", {"paper_id": req.paper_id, "cached": False, "run_id": run_id, "ok": ok,
                    "seconds": result.wall_seconds})
    return result


def run(req: RunRequest) -> RunResult:
    return execute(req)


def run_many(reqs: Iterable[RunRequest]) -> list[RunResult]:
    futures = [pool().submit(execute, r) for r in reqs]
    return [f.result() for f in futures]


def parallel_run(paper_id: str, config_: dict, seeds: Iterable[int]) -> list[RunResult]:
    return run_many(RunRequest(paper_id=paper_id, config=config_, seed=s) for s in seeds)
