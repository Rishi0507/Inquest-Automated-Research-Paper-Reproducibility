"""Measured gap attribution (component C9).

Stage B  two-sided screening against the measured noise floor τ = 1.96 · s · √(2/k)
Stage C  exact Shapley values over at most four train-phase and three eval-phase survivors
Stage D  joint check: does the pruned set matter together?
Stage E  bootstrap intervals over seed indices (paired when runs are deterministic) and residual
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations
from math import factorial
from typing import Callable, Iterable, Sequence

import numpy as np

from . import patches, runner, swarm
from .schemas import Band, Deviation, RunRequest, Share
from .witness import rescore

MAX_TRAIN = 4
MAX_EVAL = 3


def shapley(players: Sequence[str], value: Callable[[frozenset], float]) -> dict[str, float]:
    n, phi = len(players), {}
    for i in players:
        others = [j for j in players if j != i]
        total = 0.0
        for r in range(len(others) + 1):
            w = factorial(r) * factorial(n - r - 1) / factorial(n)
            for S in combinations(others, r):
                s = frozenset(S)
                total += w * (value(s | {i}) - value(s))
        phi[i] = total
    return phi


@dataclass
class Attribution:
    metric: str
    reported: float
    base: float
    full: float
    aligned: float
    gap: float
    tau: float
    seed_std: float
    k: int
    paired: bool
    effects: dict[str, dict] = field(default_factory=dict)
    survivors: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    phi: dict[str, float] = field(default_factory=dict)
    ci: dict[str, tuple[float, float]] = field(default_factory=dict)
    residual: float = 0.0
    residual_in_noise: bool = False
    pruned_joint_effect: bool = False
    shares_valid: bool = False
    shares: list[Share] = field(default_factory=list)
    aligned_band: Band | None = None
    coalitions: dict[str, list[float]] = field(default_factory=dict)
    trainings: int = 0
    rescores: int = 0
    excluded: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = {k: v for k, v in self.__dict__.items() if k not in ("shares", "aligned_band")}
        d["ci"] = {k: list(v) for k, v in self.ci.items()}
        d["shares"] = [s.model_dump() for s in self.shares]
        d["aligned_band"] = self.aligned_band.model_dump() if self.aligned_band else None
        return d


class CoalitionEvaluator:
    """v(S): mean claim metric over the seed set with the deviations in S aligned to the paper."""

    def __init__(self, paper_id: str, base_cfg: dict, devs: dict[str, Deviation], metric: str,
                 seeds: Sequence[int], progress: Callable[[str], None] | None = None):
        self.paper_id, self.base_cfg, self.devs, self.metric = paper_id, base_cfg, devs, metric
        self.seeds = list(seeds)
        self.cache: dict[frozenset, tuple[float, ...]] = {}
        self.progress = progress
        self.trainings = 0
        self.rescores = 0
        from . import corpus
        spec = corpus.get(paper_id).adapter().metrics.get(metric)
        self.scale = spec.scale if spec else 100.0

    def _train_cfg(self, S: frozenset) -> dict:
        train = [self.devs[i].patch for i in sorted(S) if self.devs[i].phase == "train"]
        return patches.apply_patches(self.base_cfg, train)

    def per_seed(self, S: Iterable[str]) -> tuple[float, ...]:
        S = frozenset(S)
        if S in self.cache:
            return self.cache[S]
        cfg = self._train_cfg(S)
        eval_spec = patches.merge_metric_specs([self.devs[i].patch for i in sorted(S) if self.devs[i].phase == "eval"])
        results = runner.run_many(RunRequest(paper_id=self.paper_id, config=cfg, seed=s) for s in self.seeds)
        self.trainings += sum(1 for r in results if not r.cached)
        out = []
        for r in results:
            if not r.ok:
                raise RuntimeError(f"coalition {sorted(S)} seed {r.seed} failed: {r.error}")
            if eval_spec:
                path = r.predictions.get(self.metric)
                if not path:
                    raise RuntimeError(f"no captured predictions for {self.metric}; eval-phase patch cannot be applied")
                out.append(rescore.rescore_payload(path, eval_spec["fn"], eval_spec["kwargs"]) * self.scale)
                self.rescores += 1
            else:
                out.append(r.metrics[self.metric])
        self.cache[S] = tuple(out)
        if self.progress:
            self.progress(f"coalition {{{', '.join(sorted(S)) or '∅'}}} = {np.mean(out):.3f}")
        return self.cache[S]

    def v(self, S: Iterable[str]) -> float:
        return float(np.mean(self.per_seed(S)))


def _bootstrap(survivors: list[str], ev: CoalitionEvaluator, paired: bool, B: int, rng: np.random.Generator
               ) -> dict[str, tuple[float, float]]:
    k = len(ev.seeds)
    subsets = [frozenset(c) for r in range(len(survivors) + 1) for c in combinations(survivors, r)]
    arrays = {S: np.asarray(ev.per_seed(S)) for S in subsets}
    draws = {i: np.empty(B) for i in survivors}
    for b in range(B):
        if paired:
            idx = rng.integers(0, k, size=k)
            means = {S: float(a[idx].mean()) for S, a in arrays.items()}
        else:
            means = {S: float(a[rng.integers(0, k, size=k)].mean()) for S, a in arrays.items()}
        phi = shapley(survivors, lambda S: means[frozenset(S)])
        for i in survivors:
            draws[i][b] = phi[i]
    return {i: (float(np.percentile(draws[i], 2.5)), float(np.percentile(draws[i], 97.5))) for i in survivors}


def attribute(paper_id: str, base_cfg: dict, delta: list[Deviation], metric: str, reported: float,
              seed_std: float, seeds: Sequence[int] = (0, 1, 2), paired: bool = True, m: int = 1,
              B: int = 1000, progress: Callable[[str], None] | None = None) -> Attribution:
    k = len(seeds)
    tau = 1.96 * seed_std * math.sqrt(2.0 / k)
    usable = {d.dev_id: d for d in delta if d.togglable and d.patch_verified}
    excluded = [{"dev_id": d.dev_id, "label": d.label, "reason": d.note or "patch not verified"}
                for d in delta if d.dev_id not in usable]
    ev = CoalitionEvaluator(paper_id, base_cfg, usable, metric, seeds, progress)
    N = frozenset(usable)
    base = ev.v(frozenset())
    full = ev.v(N) if usable else base

    # Stage B: two-sided screening
    effects = {}
    for i in sorted(usable):
        first = abs(ev.v({i}) - base)
        total = abs(full - ev.v(N - {i})) if len(usable) > 1 else first
        effects[i] = {"first_order": first, "total": total, "effect": max(first, total), "survives": max(first, total) > tau}
    ranked = sorted((i for i in usable if effects[i]["survives"]), key=lambda i: -effects[i]["effect"])
    survivors = ([i for i in ranked if usable[i].phase == "train"][:MAX_TRAIN] +
                 [i for i in ranked if usable[i].phase == "eval"][:MAX_EVAL])
    pruned = [i for i in usable if i not in survivors]

    # Stage C: exact Shapley over survivors
    phi = shapley(survivors, ev.v) if survivors else {}

    # Stage D: joint check
    s_star = frozenset(survivors)
    aligned = ev.v(s_star)
    joint = bool(pruned) and abs(full - aligned) > tau

    # Stage E: intervals, residual, aligned band
    rng = np.random.default_rng(20260921)
    ci = _bootstrap(survivors, ev, paired, B, rng) if survivors else {}
    gap = reported - base
    residual = reported - aligned
    aligned_vals = list(ev.per_seed(s_star))
    aligned_band = swarm.band(aligned_vals, m) if len(aligned_vals) > 1 else None
    in_noise = bool(aligned_band and aligned_band.lo <= reported <= aligned_band.hi)
    shares_valid = abs(gap) > 2 * tau
    shares = []
    for i in sorted(survivors, key=lambda i: -abs(phi[i])):
        lo, hi = ci[i]
        shares.append(Share(dev_id=i, label=usable[i].label or usable[i].param, points=phi[i],
                            fraction=(phi[i] / gap) if shares_valid else None, ci=(lo, hi),
                            is_noise=abs(phi[i]) <= tau or (lo <= 0.0 <= hi)))
    return Attribution(
        metric=metric, reported=reported, base=base, full=full, aligned=aligned, gap=gap, tau=tau,
        seed_std=seed_std, k=k, paired=paired, effects=effects, survivors=survivors, pruned=pruned,
        phi=phi, ci=ci, residual=residual, residual_in_noise=in_noise, pruned_joint_effect=joint,
        shares_valid=shares_valid, shares=shares, aligned_band=aligned_band,
        coalitions={",".join(sorted(S)) or "∅": list(v) for S, v in ev.cache.items()},
        trainings=ev.trainings, rescores=ev.rescores, excluded=excluded,
    )
