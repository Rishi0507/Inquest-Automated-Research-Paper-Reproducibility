"""Deviation finder (component C8): what the paper states against what the Witness observed.

Sources, in order of effort:
  1. stated parameters compared with Witness observations (arguments, optimizer, scheduler,
     split, loader, metric calls, seed calls);
  2. an AST walk for hardcoded constants the Witness cannot see;
  3. language-model proposals for name normalisation and for code patches.

Every patch is verified by observation before the deviation may enter attribution.
"""
from __future__ import annotations

import ast
import json
import math
import re
from pathlib import Path
from typing import Any, Callable, Optional

from pydantic import BaseModel

from . import corpus, llm
from .runner import canonical as runner_canonical
from .schemas import Claim, Deviation, PDFSpan
from .witness import reader

SYNONYMS = {
    "lr": ["lr", "learning_rate", "learningrate", "base_lr", "init_lr"],
    "weight_decay": ["weight_decay", "wd", "l2", "l2_reg", "reg", "weightdecay"],
    "epochs": ["epochs", "epoch", "n_epochs", "num_epochs", "max_epochs", "iterations", "n_iter", "train_epochs"],
    "batch_size": ["batch_size", "bs", "batchsize", "train_batch_size"],
    "dropout": ["dropout", "drop_rate", "dropout_rate", "p_drop", "keep_prob"],
    "hidden": ["hidden", "hidden_dim", "nhid", "hidden_size", "hidden_units", "n_hidden", "hidden1"],
    "hidden1": ["hidden1", "hidden_dim1", "hidden"],
    "hidden2": ["hidden2", "latent_dim", "hidden_dim2", "z_dim"],
    "momentum": ["momentum"],
    "warmup_steps": ["warmup_steps", "warmup", "n_warmup", "warmup_epochs"],
    "seed": ["seed", "random_seed", "rng_seed"],
    "degree": ["degree", "k", "hops", "K"],
}
HYPER_LEXICON = ("lr", "rate", "decay", "dropout", "epoch", "hidden", "batch", "momentum", "warmup", "degree",
                 "alpha", "beta", "gamma", "temp", "patience", "layers", "dim", "heads", "k", "l2", "reg")
NOT_HYPER = ("seed", "cuda", "gpu", "device", "workers", "log", "save", "path", "dir", "verbose", "print",
             "fastmode", "debug", "experiment", "dataset", "data")
METRIC_FUNCTIONS = {
    "accuracy": ("accuracy_score", {}), "roc_auc": ("roc_auc_score", {}), "auc": ("roc_auc_score", {}),
    "average_precision": ("average_precision_score", {}), "ap": ("average_precision_score", {}),
    "macro_f1": ("f1_score", {"average": "macro"}), "micro_f1": ("f1_score", {"average": "micro"}),
    "weighted_f1": ("f1_score", {"average": "weighted"}), "f1": ("f1_score", {}),
    "balanced_accuracy": ("balanced_accuracy_score", {}), "mcc": ("matthews_corrcoef", {}),
}


def _num(v: Any) -> Optional[float]:
    if isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _equal(a: Any, b: Any) -> bool:
    fa, fb = _num(a), _num(b)
    if fa is not None and fb is not None:
        return math.isclose(fa, fb, rel_tol=1e-6, abs_tol=1e-12)
    return str(a).strip().lower() == str(b).strip().lower()


def _observed_args(summary: dict) -> dict:
    return (summary.get("args") or {}).get("namespace", {}) or {}


def _dest_to_config_key(adapter, summary: dict) -> dict[str, str]:
    """argparse dest -> config key: the adapter's key when it maps the flag, otherwise the
    long flag observed by the Witness (valued options only; switches keep their adapter key)."""
    out = {}
    actions = (summary.get("args") or {}).get("actions", [])
    for key, flag in adapter.flags.items():
        for a in actions:
            if flag in a.get("flags", []):
                out[a["dest"]] = key
    for a in actions:
        if a["dest"] not in out and not a.get("store_true"):
            long = [f for f in a.get("flags", []) if f.startswith("--")]
            if long:
                out[a["dest"]] = long[0]
    return out


def _match_arg(param: str, observed: dict, extra: dict[str, str]) -> Optional[str]:
    base = param.split(".")[0]
    names = [extra.get(param), param, base] + SYNONYMS.get(base, [])
    lowered = {k.lower(): k for k in observed}
    for n in names:
        if n and n.lower() in lowered:
            return lowered[n.lower()]
    return None


# ----------------------------------------------------------------------------- AST


def ast_findings(repo: Path, files: Optional[list[Path]] = None) -> list[dict]:
    """Hardcoded constants that change results: metric averaging, split settings, seeds, init."""
    import warnings
    warnings.filterwarnings("ignore", category=SyntaxWarning)
    out = []
    targets = files or [p for p in repo.rglob("*.py") if ".git" not in p.parts]
    for py in targets:
        try:
            tree = ast.parse(py.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:
            continue
        rel = py.relative_to(repo).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
            if not fn:
                continue
            kw = {k.arg: k.value for k in node.keywords if k.arg}
            where = f"{rel}:{node.lineno}"
            if fn in ("f1_score", "precision_score", "recall_score", "fbeta_score", "roc_auc_score") and "average" in kw:
                if isinstance(kw["average"], ast.Constant):
                    out.append({"kind": "metric_average", "fn": fn, "value": kw["average"].value, "code_ref": where})
            elif fn == "train_test_split":
                out.append({"kind": "split", "stratify": "stratify" in kw,
                            "test_size": getattr(kw.get("test_size"), "value", None), "code_ref": where})
            elif fn in ("xavier_uniform_", "xavier_normal_", "kaiming_uniform_", "kaiming_normal_",
                        "glorot_uniform", "uniform_", "normal_"):
                out.append({"kind": "init", "fn": fn, "code_ref": where})
            elif fn in ("seed", "manual_seed", "set_seed") and node.args and isinstance(node.args[0], ast.Constant):
                out.append({"kind": "seed_literal", "fn": fn, "value": node.args[0].value, "code_ref": where})
            elif fn in ("Adam", "AdamW", "SGD", "RMSprop", "Adagrad"):
                for k in ("lr", "weight_decay", "momentum"):
                    if k in kw and isinstance(kw[k], ast.Constant):
                        out.append({"kind": "optimizer_literal", "fn": fn, "key": k, "value": kw[k].value,
                                    "code_ref": where})
    return out


INIT_FAMILY = {"xavier_uniform_": "glorot", "xavier_normal_": "glorot", "glorot_uniform": "glorot",
               "kaiming_uniform_": "he", "kaiming_normal_": "he", "uniform_": "uniform", "normal_": "normal"}


# ----------------------------------------------------------------------------- LLM proposals


class NameMap(BaseModel):
    class Pair(BaseModel):
        paper_param: str
        observed_key: str
        reason: str

    pairs: list[Pair]


class PatchProposal(BaseModel):
    class Edit(BaseModel):
        file: str
        find: str
        replace: str

    feasible: bool
    explanation: str
    edits: list[Edit]


def _llm_name_map(unmatched: list[str], observed: dict, log) -> dict[str, str]:
    if not unmatched or not llm.available():
        return {}
    system = ("You map hyperparameter names used in a machine learning paper to argument names observed in the "
              "running code. Map a pair only when both clearly denote the same quantity. Leave unrelated names out.")
    user = json.dumps({"paper_parameters": unmatched, "observed_arguments": sorted(observed)}, indent=1)
    try:
        res = llm.structured(system, user, NameMap, kind="deviation_names", max_tokens=4000)
    except llm.LLMUnavailable as exc:
        log(f"name normalisation skipped: {exc}")
        return {}
    return {p.paper_param: p.observed_key for p in res.pairs if p.paper_param in unmatched and p.observed_key in observed}


def propose_code_patch(repo: Path, dev: Deviation, files: list[str], log) -> Optional[dict]:
    """Ask the model for find/replace edits that move the code to the paper's stated behaviour."""
    if not llm.available():
        return None
    sources = {}
    for f in files[:4]:
        p = repo / f
        if p.exists() and p.stat().st_size < 60_000:
            sources[f] = p.read_text(encoding="utf-8", errors="replace")
    if not sources:
        return None
    system = ("You write minimal source edits that make a research repository follow the procedure stated in its "
              "paper for one specific parameter. Each edit replaces a `find` string that occurs exactly once in the "
              "named file with `replace`. Keep indentation exact. Change nothing unrelated. If the stated behaviour "
              "cannot be expressed with a small edit, set feasible to false and leave edits empty.")
    user = json.dumps({"deviation": {"parameter": dev.param, "paper_states": dev.paper_value,
                                     "code_does": dev.repo_value, "where": dev.code_ref},
                       "files": sources}, indent=1)
    try:
        res = llm.structured(system, user, PatchProposal, kind="deviation_patch", max_tokens=8000)
    except llm.LLMUnavailable as exc:
        log(f"patch proposal for {dev.dev_id} skipped: {exc}")
        return None
    if not res.feasible or not res.edits:
        return None
    edits = [e.model_dump() for e in res.edits]
    edits[-1]["probe"] = dev.dev_id
    return {"code": edits}


# ----------------------------------------------------------------------------- stated vs observed


def stated_vs_observed(claims: list[Claim], witness_block: dict) -> list[dict]:
    """One row per stated parameter: the paper's value and span beside what the Witness observed."""
    summary = witness_block["summary"]
    observed = _observed_args(summary)
    opt = (summary.get("optimizers") or [None])[-1]
    rows, seen = [], set()
    for c in claims:
        for param, sp in c.stated_config.items():
            if param in seen:
                continue
            seen.add(param)
            base = param.split(".")[0]
            obs, where, kind = None, None, None
            if param == "weight_decay.scope" and opt:
                groups = opt.get("param_groups") or []
                decayed = [g for g in groups if (_num(g.get("weight_decay")) or 0) > 0]
                obs = ("all parameters" if len(decayed) == len(groups) else
                       f"{len(decayed)} of {len(groups)} parameter groups")
                where, kind = opt["caller"], "OPTIMIZER param_groups"
                paper_scope = str(sp.value).lower()
                status = "match" if (paper_scope in ("all", "all_parameters") and obs == "all parameters") or \
                    (paper_scope != "all" and obs != "all parameters") else "mismatch"
                rows.append({"param": param, "paper": sp.value, "span": sp.span.model_dump(), "observed": obs,
                             "where": where, "via": kind, "status": status})
                continue
            if base == "optimizer" and opt:
                obs, where, kind = opt["name"], opt["caller"], "OPTIMIZER"
            elif base in ("lr", "weight_decay") and opt and opt.get(base) is not None:
                obs, where, kind = opt.get(base), opt["caller"], "OPTIMIZER"
            else:
                dest = _match_arg(param, observed, {})
                if dest is not None:
                    obs, where, kind = observed[dest], (summary.get("args") or {}).get("caller"), f"ARGS {dest}"
            if obs is None:
                status = "not observed"
            elif _num(sp.value) is None and _num(obs) is not None:
                status = "not comparable"
            else:
                status = "match" if _equal(obs, sp.value) else "mismatch"
            rows.append({"param": param, "paper": sp.value, "span": sp.span.model_dump(), "observed": obs,
                         "where": where, "via": kind, "status": status})
    return rows


# ----------------------------------------------------------------------------- finder


def find(paper_id: str, claims: list[Claim], analysis: dict, log: Callable[[str], None] = print) -> list[Deviation]:
    paper = corpus.get(paper_id)
    adapter = paper.adapter()
    witness = analysis.get("witness") or []
    if not witness:
        return []
    stated: dict = {}
    spans: dict[str, PDFSpan] = {}
    for c in claims:
        for k, sp in c.stated_config.items():
            stated.setdefault(k, sp.value)
            spans.setdefault(k, sp.span)
    devs: list[Deviation] = []

    origin: dict[str, dict] = {}
    current_cfg: dict = {}

    def add(**kw) -> Deviation:
        """Record a deviation once per fix. The same fix seen in several configurations is one
        deviation that remembers where it was observed and with which value."""
        from .runner import canonical
        cfg_key = canonical(current_cfg)
        patch_key = json.dumps(kw.get("patch", {}), sort_keys=True, default=str)
        for d in devs:
            if d.type == kw["type"] and d.param == kw["param"] and                     json.dumps(d.patch, sort_keys=True, default=str) == patch_key:
                d.observed[cfg_key] = kw.get("repo_value")
                values = sorted({str(v) for v in d.observed.values()})
                if len(values) > 1:
                    d.repo_value = ", ".join(values)
                    d.label = f"{d.param} {' / '.join(values)} against {d.paper_value}"
                return d
        d = Deviation(dev_id=f"f{len(devs) + 1}", **kw)
        d.observed = {cfg_key: kw.get("repo_value")}
        devs.append(d)
        origin[d.dev_id] = current_cfg
        return d

    # Every mapped configuration is compared: a documented command for one dataset may set
    # values that the default configuration does not.
    for block in witness:
        summary = block["summary"]
        current_cfg = block.get("config", {})
        observed = _observed_args(summary)
        dest_key = _dest_to_config_key(adapter, summary)
        # 1a. stated parameters against resolved arguments and constructed objects
        unmatched = []
        opt = (summary.get("optimizers") or [None])[-1]
        for param, value in stated.items():
            base = param.split(".")[0]
            if param == "weight_decay.scope":
                if opt:
                    groups = opt.get("param_groups") or []
                    decayed = [g for g in groups if (_num(g.get("weight_decay")) or 0) > 0]
                    if groups and len(decayed) == len(groups) and str(value).lower() not in ("all", "all_parameters"):
                        add(type="VALUE_MISMATCH", phase="train", param=param, paper_value=value,
                            repo_value="all parameters", paper_span=spans[param], witness_idx=opt["idx"],
                            code_ref=opt["caller"], source="witness",
                            label=f"Weight decay on all parameters, paper: {value}", patch={})
                continue
            if base == "optimizer" and opt:
                if not _equal(opt["name"], value):
                    add(type="VALUE_MISMATCH", phase="train", param="optimizer", paper_value=value, repo_value=opt["name"],
                        paper_span=spans[param], witness_idx=opt["idx"], code_ref=opt["caller"], source="witness",
                        label=f"Optimizer {opt['name']} instead of {value}", togglable=True, patch={})
                continue
            if base in ("lr", "weight_decay") and opt and _num(value) is not None:
                seen = opt.get(base)
                if seen is not None and not _equal(seen, value):
                    dest = _match_arg(param, observed, {})
                    key = dest_key.get(dest) if dest else None
                    add(type="VALUE_MISMATCH", phase="train", param=base, paper_value=value, repo_value=seen,
                        paper_span=spans[param], witness_idx=opt["idx"], code_ref=opt["caller"], source="witness",
                        label=f"{base} {seen} (observed at the optimizer) against {value}",
                        # verified where it was observed: the optimizer, not the argument namespace
                        patch={key: value, "expect": [{"kind": "OPTIMIZER", "key": base, "value": value}]} if key else {})
                continue
            dest = _match_arg(param, observed, {})
            if dest is None:
                unmatched.append(param)
                continue
            seen = observed[dest]
            if _num(value) is None and _num(seen) is not None:
                continue  # descriptive statement such as "tuned"; not comparable to a number
            if not _equal(seen, value):
                key = dest_key.get(dest)
                add(type="VALUE_MISMATCH", phase="train", param=param, paper_value=value, repo_value=seen,
                    paper_span=spans[param], witness_idx=summary["args"]["idx"], code_ref=summary["args"]["caller"],
                    source="witness", label=f"{param} {seen} against {value}", patch={key: value} if key else {})

        # 1b. names the lexicon could not place: model-proposed mapping, then the same comparison
        for param, dest in _llm_name_map(unmatched, observed, log).items():
            value, seen = stated[param], observed[dest]
            if _num(value) is not None and not _equal(seen, value):
                key = dest_key.get(dest)
                add(type="VALUE_MISMATCH", phase="train", param=param, paper_value=value, repo_value=seen,
                    paper_span=spans[param], witness_idx=summary["args"]["idx"], code_ref=summary["args"]["caller"],
                    source="llm", label=f"{param} {seen} against {value} (name mapped by model)",
                    patch={key: value} if key else {})

        # 1c. metric definition: claim metric against the witnessed function
        bound = {mkey: (src, summary) for mkey, src in (block.get("metric_sources") or {}).items()}
        for c in claims:
            cm = adapter.claim_map.get(c.claim_id)
            if cm is None or runner_canonical(cm.config) != runner_canonical(current_cfg):
                continue  # judged in the block of its own configuration
            mkey = cm.metric if cm and cm.metric else c.metric
            want = METRIC_FUNCTIONS.get(c.metric)
            if not want or mkey not in bound:
                continue
            src, summ = bound[mkey]
            if not src.startswith("witness:"):
                continue
            site = src.split(":", 1)[1]
            call = next((m for m in reversed(summ.get("metric_sites", [])) if m["caller"] == site), None)
            if not call or ":" in call["name"]:
                continue
            fn, kwargs = want
            seen_kw = {k: str(v).strip("'\"") for k, v in call["kwargs"].items()}
            same_fn = call["name"] == fn
            same_kw = all(seen_kw.get(k) == v for k, v in kwargs.items())
            if not (same_fn and same_kw):
                add(type="SEMANTIC_MISMATCH", phase="eval", param=f"metric:{mkey}", paper_value=c.metric,
                    repo_value=f"{call['name']}({', '.join(f'{k}={v}' for k, v in seen_kw.items())})",
                    paper_span=c.source, witness_idx=call["idx"], code_ref=site, source="witness",
                    label=f"Metric definition: {call['name']} instead of {c.metric}",
                    patch={"metric": {"fn": fn, "kwargs": kwargs}})

        # 1d. seeding: the paper fixes seeds or splits, the code never seeds
        seed_arg = any("seed" in k.lower() for k in observed)
        if seed_arg and not summary.get("seed_calls"):
            span = spans.get("split.fixed_across_runs") or spans.get("seed")
            add(type="UNIMPLEMENTED_IN_CODE", phase="train", param="seed", paper_value="seeded runs",
                repo_value="seed argument parsed, no seed call observed", paper_span=span,
                witness_idx=summary["args"]["idx"], code_ref=summary["args"]["caller"], source="witness",
                label="Seed argument has no effect", patch={},
                note="The seed argument is parsed but no random, NumPy or PyTorch seed function is ever called.")

        # 2. AST walk for evidence the Witness cannot see
        repo = paper.repo_path
        for f in ast_findings(repo):
            if f["kind"] == "init" and "weight_init" in stated:
                family = INIT_FAMILY.get(f["fn"])
                paper_family = str(stated["weight_init"]).lower()
                if family and family != paper_family and not any(d.param == "weight_init" for d in devs):
                    add(type="VALUE_MISMATCH", phase="train", param="weight_init", paper_value=stated["weight_init"],
                        repo_value=f["fn"], paper_span=spans["weight_init"], code_ref=f["code_ref"], source="ast",
                        label=f"Weight initialisation {f['fn']} against {stated['weight_init']}", patch={})
            elif f["kind"] == "split" and not f["stratify"] and any("stratif" in p for p in stated):
                add(type="VALUE_MISMATCH", phase="train", param="split.stratify", paper_value=True, repo_value=False,
                    code_ref=f["code_ref"], source="ast", label="Split is not stratified", patch={})

        # 3. unspecified hyperparameters: numeric arguments the paper never mentions
        mentioned = {_match_arg(p, observed, {}) for p in stated}
        for dest, val in observed.items():
            if dest in mentioned or _num(val) is None or isinstance(val, bool):
                continue
            low = dest.lower()
            if any(t in low for t in NOT_HYPER) or not any(t in low for t in HYPER_LEXICON):
                continue
            key = dest_key.get(dest)
            add(type="UNSPECIFIED_IN_PAPER", phase="train", param=dest, paper_value=None, repo_value=val,
                witness_idx=summary["args"]["idx"], code_ref=summary["args"]["caller"], source="witness",
                label=f"{dest} is not stated in the paper (code uses {val})",
                patch={key: val} if key else {}, alternatives=[])

    # 4. code patches for deviations no configuration flag can express, or whose flag
    #    does not change what the Witness observes
    from . import patches as patching
    for i, d in enumerate(devs):
        base_cfg = origin.get(d.dev_id, witness[0].get("config", {}))
        if d.phase != "train" or d.type == "UNSPECIFIED_IN_PAPER":
            continue
        if d.patch:
            checked = patching.verify(paper_id, base_cfg, d)
            if checked.patch_verified:
                devs[i] = d = checked
                continue
            log(f"{d.dev_id}: configuration patch did not change the observation ({checked.note})")
            d.patch = {}
        files = []
        if d.code_ref:
            files.append(d.code_ref.split(":")[0])
        for f in ("train.py", "utils.py", "model.py", "models.py", "layers.py"):
            for p in repo.rglob(f):
                rel = p.relative_to(repo).as_posix()
                if ".git" not in rel and rel not in files:
                    files.append(rel)
        proposal = propose_code_patch(repo, d, files, log)
        if proposal:
            d.patch = proposal
            d.source = "llm" if d.source == "witness" else d.source
            d.note = ((d.note + " ") if d.note else "") + "Patch proposed by the language model; accepted only after verification."
        else:
            d.togglable = False
            d.note = ((d.note + " ") if d.note else "") + "No patch expresses this deviation; reported as a finding."
    log(f"deviation finder: {len(devs)} deviations "
        f"({sum(1 for d in devs if d.type == 'UNSPECIFIED_IN_PAPER')} unspecified in the paper)")
    return devs
