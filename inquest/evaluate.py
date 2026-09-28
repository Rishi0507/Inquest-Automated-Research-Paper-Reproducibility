"""Evaluation harness (component C16): experiments E1 to E7, reported as measured.

  E1  extraction precision and recall against hand-authored ground truth
  E2  Cartographer first-attempt validation rate and human edits on unseen papers
  E3  planted-fault recovery (top-1), share error against single-fault effects, clean-control false positives
  E4  selection-signal calibration from a seed pool
  E5  single-run verdict fragility from the same pool
  E6  cost counters
  E7  Witness fidelity: re-scoring reproduces the printed metric
"""
from __future__ import annotations

import json
import math
import time
from pathlib import Path

import numpy as np

from . import config, corpus, extract, faults, llm, runner, store, swarm
from .schemas import RunRequest

OUT = config.ROOT / "eval"
POOL_PAPER = "kipf2017-gcn"
POOL_SIZE = 60


def e1() -> dict:
    rows = []
    for pid, p in corpus.all_papers().items():
        if p.parent_id or not p.hand_claims():
            continue
        extracted = p.extracted_claims()
        if not extracted:
            rows.append({"paper": pid, "status": "not extracted"})
            continue
        cmp = extract.compare(extracted, p.hand_claims())
        rep = store.kv_get(f"extraction_report:{pid}", {})
        rows.append({"paper": pid, **cmp, "span_verification_rate": rep.get("span_verification_rate"),
                     "proposed": rep.get("proposed_claims")})
    return {"experiment": "E1", "rows": rows}


def e2() -> dict:
    rows = []
    for pid, p in corpus.all_papers().items():
        rep = store.kv_get(f"cartographer_report:{pid}")
        if rep:
            rows.append({"paper": pid, "first_attempt_ok": rep["first_attempt_ok"], "validated": rep["validated"],
                         "attempts": len(rep["attempts"]), "human_edits": store.kv_get(f"adapter_edits:{pid}", 0)})
    rate = (sum(r["first_attempt_ok"] for r in rows) / len(rows)) if rows else None
    return {"experiment": "E2", "rows": rows, "first_attempt_rate": rate}


def _single_fault_effect(variant_id: str, dev: dict, metric: str, cfg: dict) -> float | None:
    from . import patches
    from .witness import rescore
    seeds = range(20)
    base = runner.run_many(RunRequest(paper_id=variant_id, config=cfg, seed=s) for s in seeds)
    if dev["phase"] == "eval":
        spec = dev["patch"]["metric"]
        scale = corpus.get(variant_id).adapter().metrics[metric].scale
        fixed = [rescore.rescore_payload(r.predictions[metric], spec["fn"], spec.get("kwargs", {})) * scale
                 for r in base if r.ok]
    else:
        aligned = patches.apply_patches(cfg, [dev["patch"]])
        fixed = [r.metrics[metric] for r in runner.run_many(RunRequest(paper_id=variant_id, config=aligned, seed=s)
                                                            for s in seeds) if r.ok]
    b = [r.metrics[metric] for r in base if r.ok]
    return float(np.mean(fixed) - np.mean(b)) if fixed and b else None


def e3() -> dict:
    rows = []
    for v in faults.list_variants():
        analysis = store.latest_analysis(v["paper_id"])
        row = {"variant": v["paper_id"], "kind": v["kind"], "analysed": bool(analysis)}
        if not analysis:
            rows.append(row)
            continue
        verdicts = {x["claim_id"]: x["verdict"] for x in analysis.get("verdicts", [])}
        row["verdicts"] = verdicts
        if v["kind"] == "clean":
            bad = [c for c, vd in verdicts.items() if vd not in ("REPRODUCED", "NOT_IMPLEMENTED", "UNVERIFIABLE")]
            att_named = [c for c, i in analysis["claims"].items() if (i.get("attribution") or {}).get("shares")]
            row.update({"false_positive": bool(bad or att_named), "non_reproduced_claims": bad})
        elif v["kind"] == "historical":
            executed = {c: vd for c, vd in verdicts.items() if analysis["claims"][c].get("values")}
            named = {c: [s["label"] for s in (analysis["claims"][c].get("attribution") or {}).get("shares", [])
                         if not s["is_noise"]] for c in executed}
            row.update({"fix_commit": corpus.get(v["paper_id"]).meta.get("fix_commit"),
                        "gap_detected": {c: vd != "REPRODUCED" for c, vd in executed.items()},
                        "attributed_to": named})
        elif v["kind"] == "planted":
            rev = v.get("revealed") or faults.reveal(v["paper_id"])
            out = rev["outcome"]
            row.update({"fault": rev["manifest"]["fault"], "seal_intact": rev["seal_intact"],
                        "detected": out.get("detected"), "top1_correct": out.get("top1_correct"), "top1": out.get("top1")})
            errors = []
            for t in out.get("top1") or []:
                info = analysis["claims"][t["claim"]]
                dev = next((d for d in analysis["deviations"] if d["dev_id"] == t["top"]), None)
                if dev and t["correct"]:
                    eff = _single_fault_effect(v["paper_id"], dev, info["metric_key"], info["config"])
                    if eff is not None:
                        errors.append({"claim": t["claim"], "shapley": t["points"], "single_fault_effect": eff,
                                       "abs_error": abs(t["points"] - eff)})
            row["share_errors"] = errors
        rows.append(row)
    planted = [r for r in rows if r.get("kind") == "planted" and r.get("analysed")]
    clean = [r for r in rows if r.get("kind") == "clean" and r.get("analysed")]
    return {"experiment": "E3", "rows": rows,
            "top1_accuracy": (sum(bool(r.get("top1_correct")) for r in planted) / len(planted)) if planted else None,
            "false_positive_rate": (sum(bool(r.get("false_positive")) for r in clean) / len(clean)) if clean else None}


def _pool() -> list[float]:
    p = corpus.get(POOL_PAPER)
    cm = p.adapter().claim_map["C-01"]
    runs = runner.run_many(RunRequest(paper_id=POOL_PAPER, config=cm.config, seed=s) for s in range(POOL_SIZE))
    return [r.metrics[cm.metric] for r in runs if r.ok]


def e4(pool: list[float]) -> dict:
    """Best-of-k reports drawn from seeds 0..39, estimated from 20 fresh seeds 40..59, resampled."""
    rng = np.random.default_rng(4)
    report_pool, fresh_pool = np.asarray(pool[:40]), np.asarray(pool[40:60])
    ecdf = np.asarray(pool)
    rows = []
    for k in (1, 5, 20):
        within, silent, trials = 0, 0, 2000
        for _ in range(trials):
            reported = rng.choice(report_pool, size=k, replace=False).max()
            fresh = rng.choice(fresh_pool, size=20, replace=True)
            sig = swarm.selection_signal(fresh, reported)
            p_emp = max((ecdf >= reported).mean(), 1.0 / len(ecdf))
            k50 = max(1.0, math.log(2) / -math.log(1 - p_emp)) if p_emp < 1 else 1.0
            within += sig.k_lower <= k50 + 1e-9
            silent += sig.k_lower < swarm.SELECTION_FLAG_K
        rows.append({"k": k, "lower_bound_coverage": within / trials, "silent_rate": silent / trials})
    return {"experiment": "E4", "pool_size": len(pool), "rows": rows,
            "note": "Coverage is measured against the empirical tail probability of the pooled runs."}


def e5(pool: list[float], reported: float, tolerance: float = 0.5) -> dict:
    """How often two single runs disagree on "reproduced within +/- tolerance".

    Measured against the paper's value and against the pool's own mean, the case in which a
    claim is exactly what the code produces on average.
    """
    rng = np.random.default_rng(5)
    pool_a = np.asarray(pool)
    mean = float(pool_a.mean())
    band = swarm.band(pool, 1)
    rows = []
    for label, ref in (("paper value", reported), ("code's own mean", mean)):
        flips, passes, trials = 0, 0, 5000
        for _ in range(trials):
            a, b = rng.choice(pool_a, size=2, replace=False)
            pa, pb = abs(a - ref) <= tolerance, abs(b - ref) <= tolerance
            flips += pa != pb
            passes += pa
        rows.append({"reference": label, "value": ref, "flip_rate": flips / trials, "pass_rate": passes / trials,
                     "band_verdict": "REPRODUCED" if band.lo <= ref <= band.hi else "NOT_REPRODUCED"})
    return {"experiment": "E5", "tolerance": tolerance, "rows": rows, "pool_mean": mean,
            "pool_std": float(pool_a.std(ddof=1)), "pool_size": len(pool)}


def e6() -> dict:
    c = store.counters()
    per_paper = []
    for pid in corpus.all_papers():
        a = store.latest_analysis(pid)
        if a and a.get("cost"):
            per_paper.append({"paper": pid, **a["cost"]})
    return {"experiment": "E6", "totals": c, "per_analysis": per_paper}


def e7() -> dict:
    rows = []
    for pid in corpus.all_papers():
        a = store.latest_analysis(pid)
        for w in (a or {}).get("witness", []) or []:
            for metric, chk in (w.get("self_check") or {}).items():
                rows.append({"paper": pid, "config": w["config"], "metric": metric, **chk})
    ok = [r for r in rows if r.get("ok")]
    return {"experiment": "E7", "rows": rows, "agreement_rate": (len(ok) / len(rows)) if rows else None}


def run(which: str = "all") -> dict:
    OUT.mkdir(exist_ok=True)
    results = {"generated": time.strftime("%Y-%m-%d %H:%M:%S"), "llm": llm.status()}
    if which in ("all", "e1"):
        results["E1"] = e1()
    if which in ("all", "e2"):
        results["E2"] = e2()
    if which in ("all", "e3"):
        results["E3"] = e3()
    if which in ("all", "e4", "e5"):
        pool = _pool()
        results["E4"] = e4(pool)
        results["E5"] = e5(pool, corpus.get(POOL_PAPER).claim("C-01").value)
    if which in ("all", "e6"):
        results["E6"] = e6()
    if which in ("all", "e7"):
        results["E7"] = e7()
    (OUT / "results.json").write_text(json.dumps(results, indent=1, default=str), encoding="utf-8")
    (OUT / "results.md").write_text(markdown(results), encoding="utf-8")
    store.kv_set("eval_results", results)
    return results


def _pct(x) -> str:
    return "not measured" if x is None else f"{100 * x:.1f}%"


def markdown(r: dict) -> str:
    lines = ["# Evaluation results", "", f"Generated {r['generated']}. Values are measured on this machine; "
             "missing rows mean the experiment has not run.", "", "| Experiment | Result |", "|---|---|"]
    if "E1" in r:
        for row in r["E1"]["rows"]:
            if "precision" in row:
                lines.append(f"| E1 extraction, {row['paper']} | precision {_pct(row['precision'])}, recall "
                             f"{_pct(row['recall'])}, span verification {_pct(row.get('span_verification_rate'))} |")
            else:
                lines.append(f"| E1 extraction, {row['paper']} | {row['status']} |")
    if "E2" in r:
        lines.append(f"| E2 Cartographer | first-attempt validation {_pct(r['E2']['first_attempt_rate'])} over "
                     f"{len(r['E2']['rows'])} paper(s) |")
    if "E3" in r:
        lines.append(f"| E3 planted faults | top-1 accuracy {_pct(r['E3']['top1_accuracy'])}; clean-control false "
                     f"positives {_pct(r['E3']['false_positive_rate'])} |")
        for row in r["E3"]["rows"]:
            if row.get("kind") == "historical":
                det = ", ".join(f"{c} {'gap detected' if g else 'no gap'}" for c, g in row.get("gap_detected", {}).items())
                att = "; ".join(f"{c}: {', '.join(n) or 'unexplained'}" for c, n in row.get("attributed_to", {}).items())
                lines.append(f"| E3 historical fault, {row['variant']} | {det or 'not analysed'}; attribution: {att or 'n/a'} |")
            for e in row.get("share_errors", []):
                lines.append(f"| E3 share error, {row['variant']} {e['claim']} | Shapley {e['shapley']:+.2f} against "
                             f"single-fault effect {e['single_fault_effect']:+.2f} (error {e['abs_error']:.2f}) |")
    if "E4" in r:
        for row in r["E4"]["rows"]:
            lines.append(f"| E4 selection signal, k={row['k']} | lower-bound coverage {_pct(row['lower_bound_coverage'])},"
                         f" silent {_pct(row['silent_rate'])} |")
    if "E5" in r:
        e = r["E5"]
        for row in e["rows"]:
            lines.append(f"| E5 single-run fragility, against the {row['reference']} ({row['value']:.2f}) | "
                         f"{_pct(row['flip_rate'])} of pairs of single runs disagree at +/-{e['tolerance']}; a single run "
                         f"passes {_pct(row['pass_rate'])} of the time; the band verdict is "
                         f"{row['band_verdict'].lower().replace('_', ' ')} |")
    if "E6" in r:
        t = r["E6"]["totals"]
        lines.append(f"| E6 cost | {t.get('runs_requested', 0)} runs requested, {t.get('runs_executed', 0)} executed, "
                     f"{t.get('cache_hits', 0)} cache hits |")
    if "E7" in r:
        lines.append(f"| E7 Witness fidelity | re-scoring agreement {_pct(r['E7']['agreement_rate'])} over "
                     f"{len(r['E7']['rows'])} checks |")
    return "\n".join(lines) + "\n"


def cli(which: str) -> int:
    res = run(which.lower())
    print(markdown(res))
    return 0
