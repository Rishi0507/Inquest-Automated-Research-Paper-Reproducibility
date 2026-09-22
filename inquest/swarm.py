"""Seed swarm, prediction-interval bands and the selection signal (component C6)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np
from scipy import stats

from . import runner
from .schemas import Band, RunResult, SelectionSignal

DEFAULT_N = 20
EXTENDED_N = 50
SELECTION_FLAG_K = 10.0


def band(values: Sequence[float], m: int = 1) -> Band:
    """95% prediction interval for the mean of `m` fresh runs: x̄ ± t(0.975, n−1) · s · √(1/m + 1/n)."""
    v = np.asarray(values, dtype=float)
    n = len(v)
    mean = float(v.mean())
    s = float(v.std(ddof=1)) if n > 1 else 0.0
    if n < 2:
        return Band(mean=mean, std=s, n=n, m=m, lo=mean, hi=mean)
    t = float(stats.t.ppf(0.975, n - 1))
    half = t * s * math.sqrt(1.0 / m + 1.0 / n)
    return Band(mean=mean, std=s, n=n, m=m, lo=mean - half, hi=mean + half)


def selection_signal(values: Sequence[float], reported: float, distribution: str = "baseline") -> SelectionSignal:
    """How many attempts a best-of-k report would need to reach `reported` with 50% probability.

    p = P(X ≥ reported) is bounded above by a one-sided 95% Clopper–Pearson interval, so
    k_lower = ln 2 / −ln(1 − p_hi) is a lower bound on the implied number of attempts.
    """
    v = np.asarray(values, dtype=float)
    n = len(v)
    c = int((v >= reported).sum())
    if c >= n:
        p_hi = 1.0
    elif c == 0:
        p_hi = 1.0 - 0.05 ** (1.0 / n)
    else:
        p_hi = float(stats.beta.ppf(0.95, c + 1, n - c))
    k_lower = math.log(2) / -math.log(1.0 - p_hi) if p_hi < 1.0 else 1.0
    return SelectionSignal(n=n, exceed=c, p_hi=p_hi, k_lower=max(1.0, k_lower), distribution=distribution)


def resolution_limit(n: int) -> float:
    """Largest k_lower that n runs can support when none exceed the reported value."""
    return math.log(2) / -math.log(0.05 ** (1.0 / n))


@dataclass
class SwarmResult:
    runs: list[RunResult]
    values: dict[str, list[float]] = field(default_factory=dict)   # metric -> values, seed order

    def metric(self, name: str) -> list[float]:
        return self.values.get(name, [])


def collect(paper_id: str, cfg: dict, seeds: Sequence[int], progress: Callable[[int, int], None] | None = None
            ) -> SwarmResult:
    reqs = list(seeds)
    runs = runner.parallel_run(paper_id, cfg, reqs)
    if progress:
        progress(len(runs), len(reqs))
    values: dict[str, list[float]] = {}
    for r in runs:
        if not r.ok:
            continue
        for k, v in r.metrics.items():
            values.setdefault(k, []).append(v)
    return SwarmResult(runs=runs, values=values)


def swarm(paper_id: str, cfg: dict, metric: str, reported: float, m: int = 1, n: int = DEFAULT_N,
          extend_to: int = EXTENDED_N, seed_offset: int = 0) -> tuple[SwarmResult, Band, SelectionSignal]:
    """Run n seeds; extend to `extend_to` when no run reaches the reported value (sequential extension)."""
    res = collect(paper_id, cfg, range(seed_offset, seed_offset + n))
    vals = res.metric(metric)
    if vals and max(vals) < reported and extend_to > n:
        more = collect(paper_id, cfg, range(seed_offset + n, seed_offset + extend_to))
        res.runs += more.runs
        for k, v in more.values.items():
            res.values.setdefault(k, []).extend(v)
        vals = res.metric(metric)
    return res, band(vals, m), selection_signal(vals, reported)
