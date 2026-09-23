"""Cartographer (component C7): claim to experiment mapping for a repository not seen before.

The model drafts a declarative adapter. Validation is executable, never an opinion:
  1. `--help` of the entry point runs and lists every mapped flag;
  2. a smoke run exits with status 0 and yields a metric the adapter can bind;
  3. a run at each claim's configuration yields a plausible metric, and the Witness shows the
     mapped values as the resolved arguments.
A failure goes back to the proposer once with the error; after that the adapter waits for a
human edit in the review view.
"""
from __future__ import annotations

import json
import shlex
from pathlib import Path
from typing import Callable, Optional

from pydantic import BaseModel, Field

from . import corpus, env, llm, runner, sandbox, store
from .prepare import entry_script, prepare
from .schemas import AdapterSpec, ClaimMapping, MetricSpec, RunRequest
from .witness import reader

MAX_FILE_CHARS = 14000


class DraftMetric(BaseModel):
    name: str = Field(description="metric key used by claims, e.g. accuracy, roc_auc, average_precision, macro_f1")
    witness_fn: Optional[str] = Field(default=None, description="scikit-learn function name that computes the final "
                                      "metric (e.g. f1_score), or module:function for a metric defined in the "
                                      "repository, importable as a module (e.g. pygcn.utils:accuracy)")
    stdout_regex: Optional[str] = Field(default=None, description="Python regex with one capture group matching the "
                                        "final printed test metric")
    scale: float = Field(description="multiplier from the raw metric to the paper's scale, usually 100 or 1")
    rescore_fn: Optional[str] = Field(default=None, description="scikit-learn equivalent of a repository-defined "
                                      "metric, e.g. accuracy_score")


class DraftFlag(BaseModel):
    key: str
    flag: str
    type: str = Field(description="int, float, str, bool, or flag for store_true switches")


class DraftClaim(BaseModel):
    claim_id: str
    status: str = Field(description="mapped, not_implemented, or unmappable")
    config: dict[str, str] = Field(default_factory=dict, description="config key to value, as text")
    metric: Optional[str] = None
    reason: Optional[str] = None


class Draft(BaseModel):
    workdir: str = Field(description="directory, relative to the repository root, to run the command from")
    command: str = Field(description="command without the configurable flags, e.g. python train.py")
    pythonpath: list[str] = Field(default_factory=list, description="repository-relative directories to add to "
                                  "PYTHONPATH so imports resolve")
    flags: list[DraftFlag]
    seed_flag: Optional[str] = None
    metrics: list[DraftMetric]
    smoke: dict[str, str] = Field(default_factory=dict, description="config overrides for a fast smoke run, "
                                  "e.g. epochs 1")
    claims: list[DraftClaim]
    notes: str


SYSTEM = """You map the numerical claims of a machine learning paper to the command that produces each of them in
the authors' repository. You are given the README, the file tree, the source of likely entry points and the claims.

Produce one declarative adapter: the working directory, the base command, the command-line flags a configuration
may set, the seed flag, how the final test metric is observed, and for every claim the configuration that
reproduces it. Only use flags that exist in the source. Mark a claim not_implemented when no code path produces it
(for example, a dataset with no loader or data files, or a model the code never builds), and give the reason.
Relative data paths in the source tell you which working directory the command needs."""


def _tree(repo: Path, limit: int = 250) -> str:
    rows = []
    for p in sorted(repo.rglob("*")):
        if ".git" in p.parts or p.is_dir():
            continue
        rel = p.relative_to(repo).as_posix()
        rows.append(f"{rel} ({p.stat().st_size} B)")
        if len(rows) >= limit:
            rows.append("...")
            break
    return "\n".join(rows)


def _candidates(repo: Path) -> list[Path]:
    names = ("train", "main", "run", "citation", "execute", "experiment", "eval")
    found = [p for p in repo.rglob("*.py") if ".git" not in p.parts and any(n in p.stem.lower() for n in names)]
    found += [p for p in repo.rglob("*.py") if ".git" not in p.parts and "__main__" in p.read_text(
        encoding="utf-8", errors="replace") and p not in found]
    return found[:6]


def _context(paper: corpus.Paper, repo: Path) -> str:
    readme = next((p for p in repo.glob("README*")), None)
    parts = [f"# README\n{readme.read_text(encoding='utf-8', errors='replace')[:8000] if readme else '(none)'}",
             f"# FILE TREE\n{_tree(repo)}"]
    budget = 60000
    files = _candidates(repo)
    for f in list(repo.rglob("*.py")):
        if f not in files and ".git" not in f.parts and len(files) < 12:
            files.append(f)
    for f in files:
        src = f.read_text(encoding="utf-8", errors="replace")[:MAX_FILE_CHARS]
        block = f"# FILE {f.relative_to(repo).as_posix()}\n{src}"
        if len(block) > budget:
            break
        budget -= len(block)
        parts.append(block)
    claims = [{"claim_id": c.claim_id, "text": c.text, "model": c.model, "dataset": c.dataset, "metric": c.metric,
               "value": c.value} for c in paper.claims()]
    parts.append("# CLAIMS\n" + json.dumps(claims, indent=1))
    return "\n\n".join(parts)


def _to_adapter(paper_id: str, d: Draft, python: str) -> AdapterSpec:
    def typed(v: str, t: str):
        try:
            if t == "int":
                return int(float(v))
            if t == "float":
                return float(v)
            if t in ("bool", "flag"):
                return str(v).lower() in ("true", "1", "yes")
        except ValueError:
            pass
        return v

    types = {f.key: f.type for f in d.flags}
    return AdapterSpec(
        paper_id=paper_id, workdir=d.workdir or ".", command=d.command,
        flags={f.key: f.flag for f in d.flags}, config_types=types, seed_flag=d.seed_flag,
        metrics={m.name: MetricSpec(witness_fn=m.witness_fn, stdout_regex=m.stdout_regex, scale=m.scale,
                                    rescore_fn=m.rescore_fn) for m in d.metrics},
        pythonpath=d.pythonpath, smoke_overrides={k: typed(v, types.get(k, "str")) for k, v in d.smoke.items()},
        claim_map={c.claim_id: ClaimMapping(status=c.status if c.status in ("mapped", "not_implemented", "unmappable")
                                            else "unmappable",
                                            config={k: typed(v, types.get(k, "str")) for k, v in c.config.items()},
                                            metric=c.metric, reason=c.reason) for c in d.claims},
        python=python, validated=False, validation_log=[f"Drafted by the Cartographer. {d.notes}"])


def validate(paper_id: str, log: Callable[[str], None] = print) -> tuple[bool, list[str]]:
    """Executable validation of the stored adapter. Returns (ok, messages)."""
    paper = corpus.get(paper_id)
    adapter = paper.adapter()
    msgs: list[str] = []
    repo = paper.repo_path
    entry = entry_script(adapter, repo)
    if entry is None or not entry.exists():
        return False, [f"entry point in '{adapter.command}' does not exist under {adapter.workdir}"]
    # 1. --help lists the mapped flags
    run_dir = runner.config.RUNS / f"help_{paper_id}"
    run_dir.mkdir(parents=True, exist_ok=True)
    argv = [str(env.interpreter(paper.env_id)), *shlex.split(adapter.command, posix=True)[1:], "--help"]
    e = runner._run_env(paper, adapter, repo, run_dir, 0)
    ex = sandbox.execute(argv, (repo / adapter.workdir).resolve(), repo, e, run_dir / "stdout.txt", run_dir, timeout=120)
    help_text = (run_dir / "stdout.txt").read_text(encoding="utf-8", errors="replace")
    missing = [f for f in adapter.flags.values() if f not in help_text]
    if ex.returncode != 0 or missing:
        msgs.append(f"--help exited {ex.returncode}; flags missing from help: {missing or 'none'}; "
                    f"output tail: {help_text[-600:]}")
        return False, msgs
    msgs.append(f"--help lists all {len(adapter.flags)} mapped flags")
    # 2. smoke run
    smoke = runner.execute(RunRequest(paper_id=paper_id, config=dict(adapter.smoke_overrides), seed=0))
    if not smoke.ok:
        msgs.append(f"smoke run failed: {smoke.error}")
        return False, msgs
    msgs.append(f"smoke run exited cleanly in {smoke.wall_seconds:.1f}s with {smoke.metrics}")
    # 3. stated configuration per mapped claim
    for cid, cm in adapter.claim_map.items():
        if cm.status != "mapped":
            continue
        res = runner.execute(RunRequest(paper_id=paper_id, config=dict(cm.config), seed=0))
        if not res.ok:
            msgs.append(f"{cid}: run failed: {res.error}")
            return False, msgs
        metric = cm.metric or next(iter(adapter.metrics))
        val = res.metrics.get(metric)
        scale = adapter.metrics[metric].scale if metric in adapter.metrics else 100.0
        if val is None or not (0.0 <= val <= (100.0 if scale == 100.0 else max(1.0, scale))):
            msgs.append(f"{cid}: metric {metric} = {val} is outside the plausible range")
            return False, msgs
        ns = (reader.summarize(reader.load(res.witness_path)).get("args") or {}).get("namespace", {})
        dests = {a["flags"][0]: a["dest"] for a in (reader.summarize(reader.load(res.witness_path)).get("args") or {})
                 .get("actions", []) if a.get("flags")}
        for key, value in cm.config.items():
            flag = adapter.flags.get(key)
            dest = next((d for f, d in dests.items() if f == flag), None)
            if dest is None:
                for a in (reader.summarize(reader.load(res.witness_path)).get("args") or {}).get("actions", []):
                    if flag in a.get("flags", []):
                        dest = a["dest"]
            if dest and str(ns.get(dest)).lower() != str(value).lower():
                try:
                    if abs(float(ns.get(dest)) - float(value)) < 1e-12:
                        continue
                except (TypeError, ValueError):
                    pass
                msgs.append(f"{cid}: Witness shows {dest}={ns.get(dest)!r}, adapter maps {value!r}")
                return False, msgs
        msgs.append(f"{cid}: {metric} = {val:.2f}; resolved arguments match the mapping")
    return True, msgs


def draft(paper_id: str, log: Callable[[str], None] = print) -> dict:
    """Draft, build the environment, validate, retry once with the error. Stores the adapter either way."""
    paper = corpus.get(paper_id)
    repo = paper.ensure_repo()
    context = _context(paper, repo)
    attempts = []
    feedback = ""
    for attempt in (1, 2):
        user = context if not feedback else context + "\n\n# PREVIOUS ATTEMPT FAILED VALIDATION\n" + feedback
        log(f"drafting adapter (attempt {attempt})")
        d = llm.structured(SYSTEM, user, Draft, kind="cartographer", max_tokens=16000)
        adapter = _to_adapter(paper_id, d, "3.10")
        store.put_adapter(paper_id, adapter.model_dump(), "cartographer")
        try:
            prepare(paper_id, log=log)
        except Exception as exc:
            ok, msgs = False, [f"environment could not be built: {exc}"]
        else:
            ok, msgs = validate(paper_id, log=log)
        attempts.append({"attempt": attempt, "ok": ok, "messages": msgs, "draft": d.model_dump()})
        for m in msgs:
            log(m)
        if ok:
            adapter = adapter.model_copy(update={"validated": True, "validation_log": adapter.validation_log + msgs})
            store.put_adapter(paper_id, adapter.model_dump(), "cartographer")
            break
        feedback = "\n".join(msgs)
    else:
        adapter = paper.adapter()
        adapter = adapter.model_copy(update={"validated": False, "validation_log": adapter.validation_log + msgs +
                                             ["Validation failed twice; waiting for a human edit in the review view."]})
        store.put_adapter(paper_id, adapter.model_dump(), "cartographer")
    report = {"first_attempt_ok": attempts[0]["ok"], "attempts": attempts, "validated": attempts[-1]["ok"]}
    store.kv_set(f"cartographer_report:{paper_id}", report)
    return report


def accept_edit(paper_id: str, adapter: dict, log: Callable[[str], None] = print) -> dict:
    """Human review path: store the edited adapter and validate it by execution."""
    spec = AdapterSpec.model_validate({**adapter, "paper_id": paper_id, "validated": False})
    store.put_adapter(paper_id, spec.model_dump(), "human")
    prepare(paper_id, log=log)
    ok, msgs = validate(paper_id, log=log)
    spec = spec.model_copy(update={"validated": ok, "validation_log": spec.validation_log + ["Edited by a reviewer."] + msgs})
    store.put_adapter(paper_id, spec.model_dump(), "human")
    edits = store.kv_get(f"adapter_edits:{paper_id}", 0) + 1
    store.kv_set(f"adapter_edits:{paper_id}", edits)
    return {"validated": ok, "messages": msgs, "human_edits": edits}
