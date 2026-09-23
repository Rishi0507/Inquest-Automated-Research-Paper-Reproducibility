import math

import numpy as np
from scipy import stats

from inquest.swarm import SELECTION_FLAG_K, resolution_limit, selection_signal


def k50(p: float) -> float:
    """Attempts needed for a best-of-k draw to reach a value of tail probability p with 50% chance."""
    return max(1.0, math.log(2) / -math.log(1 - p)) if p < 1 else 1.0


def test_lower_bound_covers_implied_attempts():
    """k_lower must not exceed the true implied attempts k50(p) in at least 95% of draws."""
    rng = np.random.default_rng(11)
    for k in (1, 5, 20):
        violations, trials = 0, 1500
        for _ in range(trials):
            reported = rng.normal(0, 1, size=k).max()
            p_true = float(stats.norm.sf(reported))
            fresh = rng.normal(0, 1, size=20)
            violations += selection_signal(fresh, reported).k_lower > k50(p_true) + 1e-9
        assert violations / trials <= 0.05, (k, violations / trials)


def test_honest_single_reports_rarely_flag():
    """A reported value that is one honest draw should almost never reach the flag threshold of 10."""
    rng = np.random.default_rng(12)
    flags = sum(selection_signal(rng.normal(0, 1, 20), rng.normal()).k_lower >= SELECTION_FLAG_K for _ in range(3000))
    assert flags / 3000 <= 0.05


def test_no_exceedance_gives_resolution_limit():
    sig = selection_signal(np.zeros(20), 1.0)
    assert sig.exceed == 0
    assert abs(sig.k_lower - resolution_limit(20)) < 1e-9
    assert 4.0 < sig.k_lower < 6.0
    assert 10.0 < resolution_limit(50) < 12.0
