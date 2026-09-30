from types import SimpleNamespace

import numpy as np

from inquest.attribution import _bootstrap


class StubEvaluator:
    """Two deviations: `t` changes training (new runs), `e` only re-scores the same runs."""

    def __init__(self, k: int = 5, seed: int = 0):
        rng = np.random.default_rng(seed)
        self.seeds = list(range(k))
        self.devs = {"t": SimpleNamespace(phase="train"), "e": SimpleNamespace(phase="eval")}
        base = rng.normal(90.0, 1.5, k)         # noisy, non-deterministic runs
        trained = rng.normal(92.0, 1.5, k)      # a different training, independent noise
        self.table = {
            frozenset(): base, frozenset("e"): base + 0.9,
            frozenset("t"): trained, frozenset("te"): trained + 0.9,
        }

    def per_seed(self, S):
        return tuple(self.table[frozenset(S)])


def test_eval_only_deviation_gets_a_tight_interval_without_paired_seeds():
    ev = StubEvaluator()
    ci = _bootstrap(["t", "e"], ev, paired=False, B=400, rng=np.random.default_rng(1))
    lo, hi = ci["e"]
    assert abs(lo - 0.9) < 1e-9 and abs(hi - 0.9) < 1e-9
    t_lo, t_hi = ci["t"]
    assert t_hi - t_lo > 0.5  # the training deviation still carries run-to-run noise
