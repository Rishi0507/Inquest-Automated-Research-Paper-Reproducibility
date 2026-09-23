import numpy as np
from sklearn import metrics

from inquest.witness import rescore


def test_rescore_matches_direct_computation(tmp_path):
    rng = np.random.default_rng(3)
    y_true = rng.integers(0, 4, 300)
    logits = rng.normal(size=(300, 4))
    path = tmp_path / "p.npz"
    np.savez(path, y_true=y_true, y_pred=logits)
    pred = logits.argmax(1)
    assert rescore.rescore_payload(path, "accuracy_score") == metrics.accuracy_score(y_true, pred)
    macro = metrics.f1_score(y_true, pred, average="macro")
    weighted = metrics.f1_score(y_true, pred, average="weighted")
    # Witness records keyword values through repr(), so both spellings must work.
    assert rescore.rescore_payload(path, "f1_score", {"average": repr("macro")}) == macro
    assert rescore.rescore_payload(path, "f1_score", {"average": "weighted"}) == weighted


def test_ranking_metrics_keep_scores(tmp_path):
    rng = np.random.default_rng(5)
    y = rng.integers(0, 2, 200)
    s = rng.random(200)
    path = tmp_path / "s.npz"
    np.savez(path, y_true=y, y_pred=s)
    assert rescore.rescore_payload(path, "roc_auc_score") == metrics.roc_auc_score(y, s)
    assert rescore.rescore_payload(path, "average_precision_score") == metrics.average_precision_score(y, s)
