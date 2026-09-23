import pytest

from inquest.attribution import shapley


def test_known_answer_with_interaction():
    table = {frozenset(): 0.0, frozenset("a"): 2.0, frozenset("b"): 1.0, frozenset("ab"): 6.0}
    phi = shapley(["a", "b"], lambda S: table[frozenset(S)])
    assert phi["a"] == pytest.approx(3.5)
    assert phi["b"] == pytest.approx(2.5)


def test_efficiency_three_players():
    weights = {"a": 1.3, "b": -0.4, "c": 2.2}

    def v(S):
        S = frozenset(S)
        return sum(weights[i] for i in S) + (1.5 if {"a", "c"} <= S else 0.0)

    phi = shapley(list(weights), v)
    assert sum(phi.values()) == pytest.approx(v({"a", "b", "c"}) - v(set()))


def test_additive_game_returns_weights():
    weights = {"x": 0.7, "y": 1.1, "z": -0.2, "w": 0.05}
    phi = shapley(list(weights), lambda S: sum(weights[i] for i in S))
    for k, w in weights.items():
        assert phi[k] == pytest.approx(w)


def test_symmetry_and_null_player():
    def v(S):
        return 4.0 if {"p", "q"} <= set(S) else 0.0

    phi = shapley(["p", "q", "null"], v)
    assert phi["p"] == pytest.approx(phi["q"])
    assert phi["null"] == pytest.approx(0.0)
