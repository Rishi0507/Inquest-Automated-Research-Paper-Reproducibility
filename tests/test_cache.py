import pytest

from inquest import config, corpus, runner
from inquest.schemas import RunRequest

PAPER = "kipf2017-gcn"


@pytest.fixture
def paper():
    try:
        p = corpus.get(PAPER)
    except corpus.PaperNotFound:
        pytest.skip("corpus paper missing")
    if not p.repo_path.exists():
        pytest.skip("repository not cloned")
    return p


def key(paper, **kw):
    return runner.cache_key(RunRequest(paper_id=PAPER, **({"config": {}, "seed": 0} | kw)), paper, paper.adapter())


def test_key_changes_with_seed_config_and_patch(paper):
    k0 = key(paper)
    assert key(paper, seed=1) != k0
    assert key(paper, config={"epochs": 10}) != k0
    edit = [{"file": "pygcn/models.py", "find": "a", "replace": "b"}]
    assert key(paper, config={runner.CODE_KEY: edit}) != k0
    assert key(paper, hash_seed="9") != k0


def test_key_changes_with_repo_sha_and_fault(paper):
    k0 = key(paper)
    other = corpus.Paper(PAPER, {**paper.meta, "repo_sha_effective": "0" * 40}, paper.dir, "corpus")
    assert runner.cache_key(RunRequest(paper_id=PAPER, seed=0), other, paper.adapter()) != k0
    faulted = corpus.Paper(PAPER, {**paper.meta, "fault_hash": "abc"}, paper.dir, "corpus")
    assert runner.cache_key(RunRequest(paper_id=PAPER, seed=0), faulted, paper.adapter()) != k0


def test_key_changes_with_environment(paper, monkeypatch, tmp_path):
    k0 = key(paper)
    lock = tmp_path / PAPER / "env.lock"
    lock.parent.mkdir(parents=True)
    lock.write_text("torch==9.9.9\n")
    monkeypatch.setattr(config, "CORPUS", tmp_path)
    assert key(paper) != k0


def test_config_order_does_not_matter(paper):
    assert key(paper, config={"lr": 0.01, "epochs": 5}) == key(paper, config={"epochs": 5, "lr": 0.01})
