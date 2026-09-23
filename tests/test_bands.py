import numpy as np
import pytest

from inquest.swarm import band


@pytest.mark.parametrize("m", [1, 5])
def test_prediction_interval_coverage(m):
    """The band must cover the mean of m fresh runs about 95% of the time."""
    rng = np.random.default_rng(7)
    n, trials, hits = 20, 4000, 0
    for _ in range(trials):
        past = rng.normal(80.0, 0.6, size=n)
        fresh = rng.normal(80.0, 0.6, size=m).mean()
        b = band(past, m)
        hits += b.lo <= fresh <= b.hi
    assert 0.93 <= hits / trials <= 0.97


def test_band_narrows_with_m():
    vals = np.random.default_rng(1).normal(0, 1, 20)
    wide, narrow = band(vals, 1), band(vals, 10)
    assert narrow.hi - narrow.lo < wide.hi - wide.lo
