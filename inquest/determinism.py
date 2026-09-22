"""Determinism audit (component C5).

Runs the same configuration and seed twice with the cache bypassed. A difference is a
finding about the repository, and it changes the statistics downstream: paired-seed
attribution loses its variance advantage, so seeds per coalition rise from three to five
and the bootstrap becomes unpaired.

A third run keeps the seed and changes only Python's hash seed. A difference there points
at iteration order over sets or dicts of strings, which Python 3 randomises per process.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field

from . import runner
from .schemas import RunRequest
from .witness import reader

AUDIT_SEED = 42


@dataclass
class DeterminismReport:
    deterministic: bool
    delta: float
    hash_sensitive: bool
    hash_delta: float
    metric: str
    values: dict = field(default_factory=dict)
    seed_calls_observed: list = field(default_factory=list)
    seed_arg_parsed: bool = False
    candidates: list[str] = field(default_factory=list)
    run_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def audit(paper_id: str, config: dict, metric: str) -> DeterminismReport:
    a = runner.execute(RunRequest(paper_id=paper_id, config=config, seed=AUDIT_SEED))
    b = runner.execute(RunRequest(paper_id=paper_id, config=config, seed=AUDIT_SEED, force=True))
    c = runner.execute(RunRequest(paper_id=paper_id, config=config, seed=AUDIT_SEED, hash_seed="1042"))
    for r in (a, b, c):
        if not r.ok:
            raise RuntimeError(f"determinism audit run failed: {r.error}")
    va, vb, vc = a.metrics[metric], b.metrics[metric], c.metrics[metric]
    delta, hdelta = abs(va - vb), abs(va - vc)
    summary = reader.summarize(reader.load(a.witness_path))
    seeds = summary["seed_calls"]
    args = (summary.get("args") or {}).get("namespace", {})
    seed_arg = any("seed" in k.lower() for k in args)
    names = {s["name"] for s in seeds}
    candidates = []
    deterministic = delta < 1e-9
    if not deterministic:
        if not seeds and seed_arg:
            candidates.append("A seed argument is parsed but no seed function is ever called (observed: no SEED_CALL).")
        if not any(n.startswith("numpy") for n in names):
            candidates.append("NumPy's global generator is never seeded.")
        if not any(n.startswith("torch") for n in names):
            candidates.append("PyTorch's generator is never seeded.")
    # Only meaningful when repeated runs agree; otherwise every run differs anyway.
    hash_sensitive = deterministic and hdelta >= 1e-9
    if hash_sensitive:
        candidates.append("The result changes with PYTHONHASHSEED alone, which points at iteration order over a set or "
                          "dict of strings (for example, label encoding built from set()).")
    return DeterminismReport(
        deterministic=deterministic, delta=delta, hash_sensitive=hash_sensitive, hash_delta=hdelta, metric=metric,
        values={"first": va, "repeat": vb, "other_hash_seed": vc}, seed_calls_observed=seeds,
        seed_arg_parsed=seed_arg, candidates=candidates, run_ids=[a.run_id, b.run_id, c.run_id])
