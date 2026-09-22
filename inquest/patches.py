"""Deviation patches: application to configurations and verification by observation.

A patch dict may contain:
  config keys           {"epochs": 50}                  train phase, passed on the command line
  "code"                [{"file", "find", "replace"}]    train phase, applied to a patched copy
  "metric"              {"fn": ..., "kwargs": {...}}    eval phase, applied by re-scoring predictions
  "expect"              [{"kind", "key", "value"}]       optional explicit Witness expectations
"""
from __future__ import annotations

import math
from typing import Any

from . import corpus, runner
from .schemas import Deviation, RunRequest
from .witness import reader

RESERVED = {"code", "metric", "expect"}


def config_part(patch: dict) -> dict:
    return {k: v for k, v in patch.items() if k not in RESERVED}


def apply_patches(base_cfg: dict, patches: list[dict]) -> dict:
    cfg = dict(base_cfg)
    edits = list(cfg.get(runner.CODE_KEY, []))
    for p in patches:
        cfg.update(config_part(p))
        edits += p.get("code", [])
    if edits:
        cfg[runner.CODE_KEY] = edits
    return cfg


def merge_metric_specs(patches: list[dict]) -> dict | None:
    spec: dict | None = None
    for p in patches:
        m = p.get("metric")
        if not m:
            continue
        if spec is None:
            spec = {"fn": m.get("fn"), "kwargs": dict(m.get("kwargs", {}))}
        else:
            if m.get("fn"):
                spec["fn"] = m["fn"]
            spec["kwargs"].update(m.get("kwargs", {}))
    return spec


def _same(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return str(a).lower() == str(b).lower()
    try:
        fa, fb = float(a), float(b)
        return math.isclose(fa, fb, rel_tol=1e-9, abs_tol=1e-12)
    except (TypeError, ValueError):
        return str(a) == str(b)


def _dest_for(flag: str, args_event: dict | None) -> str | None:
    if not args_event:
        return None
    for a in args_event.get("actions", []):
        if flag in a.get("flags", []):
            return a["dest"]
    return None


def expectations(dev: Deviation, adapter, baseline_args: dict | None) -> list[dict]:
    patch = dev.patch
    if patch.get("expect"):
        return patch["expect"]
    out = []
    for key, value in config_part(patch).items():
        flag = adapter.flags.get(key)
        dest = _dest_for(flag, baseline_args) if flag else None
        out.append({"kind": "ARGS", "key": dest or key, "value": value})
    for e in patch.get("code", []):
        if e.get("probe"):
            out.append({"kind": "PATCH_HIT", "key": e["probe"]})
    return out


def check(expect: list[dict], summary: dict) -> tuple[bool, list[str]]:
    notes, ok = [], True
    for x in expect:
        if x["kind"] == "ARGS":
            ns = (summary.get("args") or {}).get("namespace", {})
            seen = ns.get(x["key"], "<absent>")
            good = _same(seen, x["value"])
            notes.append(f"ARGS {x['key']} observed {seen!r}, expected {x['value']!r}")
        elif x["kind"] == "PATCH_HIT":
            good = any(h["tag"] == x["key"] for h in summary.get("patch_hits", []))
            notes.append(f"patched code {x['key']} {'executed' if good else 'never executed'}")
        elif x["kind"] == "OPTIMIZER":
            opts = summary.get("optimizers", [])
            seen = opts[-1].get(x["key"]) if opts else "<no optimizer observed>"
            good = _same(seen, x["value"])
            notes.append(f"optimizer {x['key']} observed {seen!r}, expected {x['value']!r}")
        elif x["kind"] == "SEED_CALL":
            good = len(summary.get("seed_calls", [])) > 0
            notes.append("seed calls observed" if good else "no seed call observed")
        elif x["kind"] == "SCHEDULER":
            good = any(s["name"] == x.get("value") for s in summary.get("schedulers", []))
            notes.append(f"scheduler {x.get('value')} {'observed' if good else 'not observed'}")
        else:
            good = False
            notes.append(f"unsupported expectation {x['kind']}")
        ok = ok and good
    return ok, notes


def verify(paper_id: str, base_cfg: dict, dev: Deviation, eval_reuse: bool = True) -> Deviation:
    """Apply the patch to a smoke run and accept it only if the Witness shows the intended change."""
    paper = corpus.get(paper_id)
    adapter = paper.adapter()
    if not dev.patch:
        return dev.model_copy(update={"patch_verified": False, "togglable": False,
                                      "note": "no patch can express this deviation"})
    if dev.phase == "eval":
        spec = dev.patch.get("metric")
        if not eval_reuse:
            return dev.model_copy(update={"patch_verified": False, "togglable": False,
                                          "note": "eval-phase re-scoring disabled: the self-check did not reproduce "
                                                  "the printed metric"})
        from .witness import rescore
        if not spec or spec.get("fn") not in rescore.ALLOWED:
            return dev.model_copy(update={"patch_verified": False, "togglable": False,
                                          "note": "metric patch does not name a supported scikit-learn function"})
        return dev.model_copy(update={"patch_verified": True, "note": "eval-phase patch applied to captured predictions"})
    smoke = {**base_cfg, **{k: v for k, v in adapter.smoke_overrides.items()}}
    base_run = runner.execute(RunRequest(paper_id=paper_id, config=smoke, seed=0))
    baseline_args = reader.summarize(reader.load(base_run.witness_path)).get("args")
    cfg = apply_patches(smoke, [dev.patch])
    for k in adapter.smoke_overrides:
        if k in config_part(dev.patch):
            cfg[k] = dev.patch[k]
    try:
        res = runner.execute(RunRequest(paper_id=paper_id, config=cfg, seed=0))
    except Exception as exc:
        return dev.model_copy(update={"patch_verified": False, "togglable": False, "note": f"patch failed: {exc}"})
    if not res.ok:
        return dev.model_copy(update={"patch_verified": False, "togglable": False,
                                      "note": f"patched smoke run failed: {res.error}"})
    summary = reader.summarize(reader.load(res.witness_path))
    exp = expectations(dev, adapter, baseline_args)
    if not exp:
        return dev.model_copy(update={"patch_verified": False, "togglable": False,
                                      "note": "no observable expectation for this patch"})
    ok, notes = check(exp, summary)
    return dev.model_copy(update={"patch_verified": ok, "togglable": ok and dev.togglable,
                                  "note": "; ".join(notes)})
