"""Fault injection and controls (component C12).

Planted faults, historical faults and clean controls create variant papers that share the
parent's PDF, stated parameters and environment. Every variant's claim values are replaced
by a reference: the parent repository's own measured mean (baseline swarm), so the only gap
a variant can show is the one its fault introduces. Variants are disclosed as controls.

Blind protocol: `plant` writes the fault manifest outside the corpus and records only its
SHA-256 in the variant's metadata. The analysis never reads the manifest. `reveal` checks
the hash, then scores the analysis against the manifest.
"""
from __future__ import annotations

import hashlib
import json
import random
import shutil
import time
from pathlib import Path

import numpy as np

from . import config, corpus, repos, runner, store
from .schemas import RunRequest

FAULT_DIR = config.WORKSPACE / "faults"

# Catalogue: per fault kind, per paper, a find/replace edit. Kinds that do not apply to a
# repository (no scheduler to remove, no split call to unstratify) have no entry there.
CATALOGUE: dict[str, dict[str, dict]] = {
    "metric_swap": {
        "wu2019-sgc": {"file": "citation.py", "find": "    return accuracy(model(test_features), test_labels)\n",
                       "replace": "    from sklearn.metrics import f1_score\n"
                                  "    return f1_score(test_labels.cpu().numpy(), model(test_features).max(1)[1].cpu().numpy(),"
                                  " average='weighted')\n",
                       "expected": {"param": "metric", "phase": "eval"}},
        "kipf2016-vgae": {"file": "gae/utils.py", "find": "    ap_score = average_precision_score(labels_all, preds_all)\n",
                          "replace": "    ap_score = roc_auc_score(labels_all, preds_all)\n",
                          "expected": {"param": "metric", "phase": "eval"}},
    },
    "epoch_drift": {
        "kipf2017-gcn": {"file": "pygcn/train.py",
                         "find": "parser.add_argument('--epochs', type=int, default=200,",
                         "replace": "parser.add_argument('--epochs', type=int, default=120,",
                         "expected": {"param": "epochs", "phase": "train"}},
        "wu2019-sgc": {"file": "args.py", "find": "parser.add_argument('--epochs', type=int, default=100,",
                       "replace": "parser.add_argument('--epochs', type=int, default=30,",
                       "expected": {"param": "epochs", "phase": "train"}},
    },
    "weight_decay_hardcoded": {
        "kipf2017-gcn": {"file": "pygcn/train.py",
                         "find": "                       lr=args.lr, weight_decay=args.weight_decay)\n",
                         "replace": "                       lr=args.lr, weight_decay=5e-3)\n",
                         "expected": {"param": "weight_decay", "phase": "train"}},
    },
    "lr_hardcoded": {
        "wu2019-sgc": {"file": "citation.py", "find": "    optimizer = optim.Adam(model.parameters(), lr=lr,\n",
                       "replace": "    optimizer = optim.Adam(model.parameters(), lr=0.02,\n",
                       "expected": {"param": "lr", "phase": "train"}},
    },
}
NOT_APPLICABLE_NOTE = {
    "unstratified": "no train_test_split call in the curated repositories",
    "warmup_removed": "no learning-rate scheduler in the curated repositories",
}


def _variant_dir(variant_id: str) -> Path:
    return config.CORPUS / variant_id


def reference_values(parent_id: str, n: int = 20) -> dict[str, float]:
    """The parent repository's measured mean per mapped claim over seeds 0..n-1 (cached runs)."""
    parent = corpus.get(parent_id)
    adapter = parent.adapter()
    out = {}
    for c in parent.hand_claims():
        cm = adapter.claim_map.get(c.claim_id)
        if not cm or cm.status != "mapped":
            continue
        runs = runner.run_many(RunRequest(paper_id=parent_id, config=cm.config, seed=s) for s in range(n))
        vals = [r.metrics[cm.metric or c.metric] for r in runs if r.ok]
        if vals:
            out[c.claim_id] = round(float(np.mean(vals)), 4)
    return out


def _write_variant(variant_id: str, parent_id: str, kind: str, repo_src: Path, refs: dict[str, float],
                   extra_meta: dict, only_claims: list[str] | None = None) -> Path:
    parent = corpus.get(parent_id)
    vdir = _variant_dir(variant_id)
    vdir.mkdir(parents=True, exist_ok=True)
    claims = []
    for c in parent.hand_claims():
        if only_claims and c.claim_id not in only_claims:
            continue
        d = c.model_dump()
        if c.claim_id in refs:
            d["value"] = refs[c.claim_id]
            d["text"] = (f"Reference for control: the unmodified repository's mean {refs[c.claim_id]:.2f} "
                         f"(the paper prints {c.value_text})")
        claims.append(d)
    (vdir / "claims.json").write_text(json.dumps(claims, indent=1, ensure_ascii=False), encoding="utf-8")
    meta = {
        "paper_id": variant_id, "parent": parent_id, "title": f"{parent.title} [{kind} control]",
        "authors": parent.meta.get("authors"), "venue": parent.meta.get("venue"), "arxiv": parent.meta.get("arxiv"),
        "paper_date": parent.meta.get("paper_date"), "repo_url": parent.meta.get("repo_url"),
        "repo_sha": parent.meta.get("repo_sha"), "provenance": parent.meta.get("provenance"),
        "role": "control", "variant_kind": kind, "deviation_source": "finder",
        "control_note": "Disclosed control. Claim values are the unmodified repository's measured means, so any gap "
                        "comes from the variant itself.",
        "created": time.time(), **extra_meta,
    }
    (vdir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")
    dest = config.WORKSPACE / "variants" / variant_id / "repo"
    if dest.exists():
        shutil.rmtree(dest)
    shutil.copytree(repo_src, dest, ignore=shutil.ignore_patterns(".git"))
    return dest


def plant(paper_id: str, fault: str, out: str | None = None, seed: int | None = None) -> dict:
    """Plant a fault. `fault="random"` draws one applicable kind so the analyst cannot know which."""
    applicable = [k for k, v in CATALOGUE.items() if paper_id in v]
    if fault == "random":
        rng = random.Random(seed if seed is not None else time.time_ns())
        fault = rng.choice(applicable)
    if fault not in CATALOGUE or paper_id not in CATALOGUE[fault]:
        raise ValueError(f"{fault} does not apply to {paper_id}; applicable: {applicable}")
    spec = CATALOGUE[fault][paper_id]
    parent = corpus.get(paper_id)
    parent.ensure_repo()
    variant_id = out or f"{paper_id}~blind{int(time.time()) % 100000}"
    refs = reference_values(paper_id)
    edit = {k: spec[k] for k in ("file", "find", "replace")}
    manifest = {"variant": variant_id, "parent": paper_id, "fault": fault, "edit": edit,
                "expected": spec["expected"], "planted_at": time.time()}
    blob = json.dumps(manifest, sort_keys=True).encode()
    sealed = hashlib.sha256(blob).hexdigest()
    FAULT_DIR.mkdir(parents=True, exist_ok=True)
    (FAULT_DIR / f"{variant_id}.json").write_bytes(blob)
    dest = _write_variant(variant_id, paper_id, "planted", parent.repo_path, refs,
                          {"sealed_manifest_sha256": sealed, "fault_hash": sealed[:16]})
    repos.apply_edits(dest, [edit])
    return {"variant": variant_id, "sealed_manifest_sha256": sealed}


def clean(paper_id: str, out: str | None = None) -> dict:
    parent = corpus.get(paper_id)
    parent.ensure_repo()
    variant_id = out or f"{paper_id}~clean"
    refs = reference_values(paper_id)
    _write_variant(variant_id, paper_id, "clean", parent.repo_path, refs, {"fault_hash": "clean"})
    return {"variant": variant_id}


def history(paper_id: str, fix_commit: str, out: str | None = None, note: str = "") -> dict:
    """Check out the parent of a result-changing fix commit. Ground truth comes from the fix diff."""
    parent = corpus.get(paper_id)
    repo = parent.ensure_repo()
    before = repos._git(["rev-parse", f"{fix_commit}^"], cwd=repo)
    diff = repos._git(["show", "--stat", "--patch", fix_commit], cwd=repo)
    variant_id = out or f"{paper_id}~hist{fix_commit[:7]}"
    tmp = config.TMP / f"hist_{variant_id}"
    if tmp.exists():
        shutil.rmtree(tmp)
    repos._git(["worktree", "add", "--force", "--detach", str(tmp), before], cwd=repo)
    try:
        refs = reference_values(paper_id)
        _write_variant(variant_id, paper_id, "historical", tmp, refs,
                       {"fix_commit": fix_commit, "repo_sha_effective": before, "fault_hash": f"hist-{before[:12]}",
                        "history_note": note})
    finally:
        repos._git(["worktree", "remove", "--force", str(tmp)], cwd=repo)
    (FAULT_DIR).mkdir(parents=True, exist_ok=True)
    (FAULT_DIR / f"{variant_id}.fix.diff").write_text(diff, encoding="utf-8")
    return {"variant": variant_id, "checked_out": before, "fix_commit": fix_commit}


def reveal(variant_id: str) -> dict:
    """Unseal the manifest, check its hash, and score the latest analysis against it."""
    meta = corpus.get(variant_id).meta
    raw = (FAULT_DIR / f"{variant_id}.json").read_bytes()
    ok = hashlib.sha256(raw).hexdigest() == meta.get("sealed_manifest_sha256")
    manifest = json.loads(raw)
    analysis = store.latest_analysis(variant_id)
    outcome = score(manifest, analysis) if analysis else {"analysed": False}
    result = {"variant": variant_id, "seal_intact": ok, "manifest": manifest, "outcome": outcome}
    store.kv_set(f"fault_reveal:{variant_id}", result)
    return result


def _matches(dev: dict, expected: dict) -> bool:
    return dev.get("phase") == expected["phase"] and expected["param"] in str(dev.get("param", ""))


def score(manifest: dict, analysis: dict) -> dict:
    """Top-1: the largest attributed share names the planted deviation."""
    expected = manifest["expected"]
    devs = {d["dev_id"]: d for d in analysis.get("deviations", [])}
    found = [d for d in devs.values() if _matches(d, expected)]
    tops = []
    for cid, info in analysis.get("claims", {}).items():
        att = info.get("attribution")
        if not att or not att.get("shares"):
            continue
        top = max(att["shares"], key=lambda s: abs(s["points"]))
        tops.append({"claim": cid, "top": top["dev_id"], "label": top["label"], "points": top["points"],
                     "correct": _matches(devs.get(top["dev_id"], {}), expected)})
    return {"analysed": True, "detected": bool(found), "detected_as": [d["dev_id"] for d in found],
            "top1": tops, "top1_correct": bool(tops) and all(t["correct"] for t in tops)}


def list_variants() -> list[dict]:
    out = []
    for pid, p in corpus.all_papers().items():
        if p.parent_id:
            rev = store.kv_get(f"fault_reveal:{pid}")
            out.append({"paper_id": pid, "parent": p.parent_id, "kind": p.meta.get("variant_kind"),
                        "sealed": p.meta.get("sealed_manifest_sha256"), "revealed": rev})
    return out


def cli(args) -> int:
    if args.action == "plant":
        print(json.dumps(plant(args.paper, args.fault or "random", args.out, args.seed), indent=1))
    elif args.action == "clean":
        print(json.dumps(clean(args.paper, args.out), indent=1))
    elif args.action == "history":
        print(json.dumps(history(args.paper, args.fix_commit, args.out), indent=1))
    elif args.action == "reveal":
        print(json.dumps(reveal(args.out or args.paper), indent=1, default=str))
    elif args.action == "list":
        print(json.dumps(list_variants(), indent=1, default=str))
    return 0
