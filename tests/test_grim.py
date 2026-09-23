from inquest.preflight import grim, nearest_achievable


def test_impossible_accuracy():
    assert grim(93.47, 2, 2000) is False
    near = nearest_achievable(93.47, 2, 2000)
    assert 93.45 in near and 93.5 in near


def test_possible_accuracy():
    assert grim(93.45, 2, 2000) is True
    assert grim(81.5, 1, 1000) is None  # step 0.1 at one decimal: every value is achievable


def test_mean_over_seeds_loosens_constraint():
    assert grim(93.47, 2, 2000, m=1) is False
    assert grim(93.47, 2, 2000, m=10) is None
