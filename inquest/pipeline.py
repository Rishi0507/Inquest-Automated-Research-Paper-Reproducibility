"""End-to-end analysis of one paper: every stage emits a result even when the next cannot run."""
from __future__ import annotations

import json
import threading
import time
import traceback
import uuid
from collections import OrderedDict
from typing import Callable, Optional

from . import attribution, config, corpus, determinism, patches, pdfindex, preflight, runner, spec, store, swarm, verdicts
from .prepare import prepare
from .schemas import Claim, Deviation, RunRequest
from .witness import reader

STAGES = ["environment", "claims", "preflight", "mapping", "determinism", "swarm", "witness", "deviations",
          "attribution", "specification", "verdicts"]
ATTRIBUTABLE = {"VALUE_MISMATCH", "SEMANTIC_MISMATCH", "UNIMPLEMENTED_IN_CODE"}
SWEEPABLE = {"UNSPECIFIED_IN_PAPER", "AMBIGUOUS"}


class Job:
    """Progress record shared with the API through the jobs table."""

    def __init__(self, paper_id: str, kind: str = "analyze", job_id: Optional[str] = None):
        self.job_id = job_id or uuid.uuid4().hex[:10]
        self.paper_id = paper_id
        self.kind = kind
        self.state = {"status": "queued", "stage": None, "stages": {s: "pending" for s in STAGES}, "progress": 0.0,
                      "runs_done": 0, "runs_executed": 0, "cache_hits": 0, "rescores": 0, "log": [],
                      "started": time.time(), "finished": None, "error": None, "analysis_id": None}
        self._lock = threading.Lock()
        self.save()

    def save(self) -> None:
        store.put_job(self.job_id, self.paper_id, self.kind, self.state)

    def log(self, msg: str) -> None:
        with self._lock:
            self.state["log"].append({"t": time.time(), "msg": msg})
            self.state["log"] = self.state["log"][-400:]
        self.save()

    def stage(self, name: str, status: str = "running") -> None:
        with self._lock:
            self.state["stages"][name] = status
            if status == "running":
                self.state["stage"] = name
            done = sum(1 for v in self.state["stages"].values() if v in ("done", "skipped", "failed"))
            self.state["progress"] = done / len(STAGES)
        self.save()

    def on_run(self, event: str, data: dict) -> None:
        if data.get("paper_id") != self.paper_id or event != "run":
            return
        with self._lock:
            self.state["runs_done"] += 1
            if data.get("cached"):
                self.state["cache_hits"] += 1
            else:
                self.state["runs_executed"] += 1
        self.save()


def _group_claims(claims: list[Claim], adapter) -> tuple[OrderedDict, dict]:
    groups: OrderedDict = OrderedDict()
    mapping = {}
    for c in claims:
        cm = adapter.claim_map.get(c.claim_id) if adapter else None
        if cm is None:
            mapping[c.claim_id] = {"status": "unmappable", "reason": "no experiment mapped for this claim"}
            continue
        mapping[c.claim_id] = cm.model_dump()
        if cm.status != "mapped":
            continue
        key = runner.canonical(cm.config)
        groups.setdefault(key, {"config": cm.config, "claims": []})["claims"].append(c)
    return groups, mapping


def _metric_of(claim: Claim, adapter) -> str:
    cm = adapter.claim_map.get(claim.claim_id)
    return (cm.metric if cm and cm.metric else claim.metric)


def _load_deviations(paper: corpus.Paper, source: str) -> list[Deviation]:
    if source == "hand":
        return paper.hand_deviations()
    return paper.found_deviations()


def _self_check(run, adapter) -> dict:
    """E7: re-score the baseline predictions with the witnessed definition; must reproduce to 1e-9."""
    from .witness import rescore
    events = reader.load(run.witness_path)
    out = {}
    for metric, path in run.predictions.items():
        ev = next((e for e in events if e.payload_path == path), None)
        if ev is None:
            continue
        fn = ev.name
        kwargs = ev.kwargs
        if ":" in fn:
            fn, kwargs = adapter.metrics[metric].rescore_fn, {}
            if not fn:
                out[metric] = {"fn": ev.name, "ok": False, "error": "repository metric without a rescore_fn equivalent"}
                continue
        try:
            value = rescore.rescore_payload(path, fn, kwargs)
            out[metric] = {"fn": fn, "rescored": value, "witnessed": ev.value, "abs_diff": abs(value - ev.value),
                           "ok": abs(value - ev.value) <= 1e-9}
        except Exception as exc:
            out[metric] = {"fn": fn, "ok": False, "error": str(exc)}
    return out


def analyze(paper_id: str, job: Optional[Job] = None, deviation_source: Optional[str] = None,
            n_seeds: Optional[int] = None) -> dict:
    job = job or Job(paper_id)
    unsubscribe = runner.subscribe(job.on_run)
    job.state["status"] = "running"
    job.save()
    started = time.time()
    counters_before = store.counters()
    paper = corpus.get(paper_id)
    result: dict = {"analysis_id": job.job_id, "paper_id": paper_id, "created": time.time(),
                    "standing_note": config.STANDING_NOTE, "paper": paper.summary(), "claims": {}, "groups": [],
                    "determinism": None, "deviations": [], "stages": {}, "errors": []}
    try:
        _analyze(paper, job, result, deviation_source, n_seeds)
        job.state["status"] = "done"
    except Exception as exc:
        job.state["status"] = "failed"
        job.state["error"] = f"{type(exc).__name__}: {exc}"
        result["errors"].append({"stage": job.state.get("stage"), "error": str(exc),
                                 "trace": traceback.format_exc()[-3000:]})
        job.log(f"failed: {exc}")
        if job.state.get("stage"):
            job.stage(job.state["stage"], "failed")
    finally:
        unsubscribe()
        after = store.counters()
        result["cost"] = {k: after.get(k, 0) - counters_before.get(k, 0)
                          for k in ("runs_requested", "runs_executed", "cache_hits")}
        result["cost"]["rescores"] = job.state["rescores"]
        result["cost"]["wall_seconds"] = round(time.time() - started, 1)
        result["stages"] = job.state["stages"]
        result["status"] = job.state["status"]
        store.put_analysis(job.job_id, paper_id, result)
        job.state["finished"] = time.time()
        job.state["analysis_id"] = job.job_id
        job.save()
    return result


def _analyze(paper: corpus.Paper, job: Job, result: dict, deviation_source: Optional[str], n_seeds: Optional[int]):
    pid = paper.paper_id
    budget = paper.meta.get("analysis", {})
    n = n_seeds or budget.get("n_seeds", swarm.DEFAULT_N)

    # 1. environment ------------------------------------------------------------------
    job.stage("environment")
    try:
        fp = prepare(pid, log=job.log)
        result["environment"] = {k: v for k, v in fp.items() if k != "packages"} | {"packages": fp.get("packages", [])}
        runnable = True
        job.stage("environment", "done")
    except Exception as exc:
        runnable = False
        result["environment"] = {"error": str(exc)}
        job.log(f"environment could not be built: {exc}")
        job.stage("environment", "failed")

    # 2. claims -------------------------------------------------------------------------
    job.stage("claims")
    adapter = paper.adapter()
    claims = []
    for c in paper.claims():
        verified = pdfindex.verify_claim_span(paper.pdf_path, c.source, c.value_text)
        if verified is None:
            result["errors"].append({"stage": "claims", "error": f"{c.claim_id}: value not found at its span; dropped"})
            continue
        claims.append(c.model_copy(update={"source": verified}))
    result["claim_source"] = paper.claims_source
    for c in claims:
        result["claims"][c.claim_id] = {"claim": c.model_dump(), "sci": spec.sci(c)}
    job.log(f"{len(claims)} claims with verified spans ({paper.claims_source})")
    job.stage("claims", "done")

    # 3. preflight ------------------------------------------------------------------------
    job.stage("preflight")
    for c in claims:
        result["claims"][c.claim_id]["preflight"] = preflight.check_claim(c)
    impossible = [c for c in claims if result["claims"][c.claim_id]["preflight"]["status"] == "impossible"]
    if impossible:
        job.log(f"GRIM: {', '.join(c.claim_id for c in impossible)} numerically impossible")
    job.stage("preflight", "done")

    # 4. mapping ----------------------------------------------------------------------------
    job.stage("mapping")
    groups, mapping = _group_claims(claims, adapter)
    for cid, mp in mapping.items():
        result["claims"][cid]["mapping"] = mp
    result["adapter"] = adapter.model_dump() if adapter else None
    result["adapter_origin"] = paper.adapter_origin()
    job.log(f"{sum(len(g['claims']) for g in groups.values())} claims mapped to {len(groups)} configuration(s)")
    job.stage("mapping", "done")

    if not runnable or not groups:
        for s in ("determinism", "swarm", "witness", "deviations", "attribution", "specification"):
            job.stage(s, "skipped")
        _verdicts(paper, claims, result, job, runnable=runnable, groups=groups)
        return

    # 5. determinism ------------------------------------------------------------------------
    job.stage("determinism")
    first = next(iter(groups.values()))
    det_metric = _metric_of(first["claims"][0], adapter)
    det = determinism.audit(pid, first["config"], det_metric)
    result["determinism"] = det.to_dict()
    paired = det.deterministic
    k = 3 if paired else 5
    job.log("deterministic: repeated runs agree" if paired else
            f"non-deterministic: repeated runs differ by {det.delta:.4f}; using {k} unpaired seeds per coalition")
    if det.hash_sensitive:
        job.log(f"hash-seed sensitive: changing PYTHONHASHSEED alone moves the metric by {det.hash_delta:.4f}")
    job.stage("determinism", "done")

    # 6. swarm ------------------------------------------------------------------------------
    job.stage("swarm")
    baselines = {}
    for key, g in groups.items():
        res = swarm.collect(pid, g["config"], range(n))
        failed = [r for r in res.runs if not r.ok]
        if len(failed) == len(res.runs):
            g["failed"] = failed[0].error
            job.log(f"baseline failed for {g['config']}: {failed[0].error}")
            continue
        # sequential extension for claims no run reaches
        need_ext = any(max(res.metric(_metric_of(c, adapter)) or [float('inf')]) < c.value for c in g["claims"])
        if need_ext and n >= swarm.DEFAULT_N:
            job.log(f"no run reaches a reported value; extending to {swarm.EXTENDED_N} seeds")
            more = swarm.collect(pid, g["config"], range(n, swarm.EXTENDED_N))
            res.runs += more.runs
            for mk, v in more.values.items():
                res.values.setdefault(mk, []).extend(v)
        g["swarm"] = res
        ok_runs = [r for r in res.runs if r.ok]
        baselines[key] = ok_runs[0]
        for c in g["claims"]:
            metric = _metric_of(c, adapter)
            vals = res.metric(metric)
            m = c.n_seeds_reported or 1
            b = swarm.band(vals, m)
            sel = swarm.selection_signal(vals, c.value, "baseline")
            result["claims"][c.claim_id].update({
                "metric_key": metric, "values": vals, "seed_band": b.model_dump(), "selection": sel.model_dump(),
                "selection_resolution": swarm.resolution_limit(len(vals)), "config": g["config"],
                "baseline_run_ids": [r.run_id for r in ok_runs],
                "dispersion_check": preflight.dispersion_check(c, b.std),
            })
            job.log(f"{c.claim_id}: reported {c.value}, baseline {b.mean:.2f} ± {b.std:.2f} (n={b.n}), "
                    f"band [{b.lo:.2f}, {b.hi:.2f}]")
    job.stage("swarm", "done")

    # 7. witness ----------------------------------------------------------------------------
    job.stage("witness")
    witness = {}
    for key, g in groups.items():
        if key not in baselines:
            continue
        run = baselines[key]
        summary = reader.summarize(reader.load(run.witness_path))
        checks = _self_check(run, adapter)
        witness[key] = {"config": g["config"], "run_id": run.run_id, "summary": summary, "self_check": checks,
                        "metric_sources": run.metric_sources, "stdout_path": run.stdout_path}
        g["eval_reuse"] = all(v.get("ok") for v in checks.values()) if checks else False
    result["witness"] = list(witness.values())
    job.stage("witness", "done")

    # 8. deviations -------------------------------------------------------------------------
    job.stage("deviations")
    source = deviation_source or ("hand" if paper.hand_deviations() and paper.meta.get("deviation_source", "hand") == "hand"
                                  else "finder")
    if source == "finder" and not paper.found_deviations():
        from . import deviations as finder
        found = finder.find(pid, claims, result, log=job.log)
        store.put_deviations(pid, "finder", [d.model_dump() for d in found])
    devs = _load_deviations(paper, source)
    first_key = next(iter(baselines), None)
    base_cfg = groups[first_key]["config"] if first_key else {}
    eval_reuse = groups[first_key].get("eval_reuse", False) if first_key else False
    verified = []
    for d in devs:
        v = patches.verify(pid, base_cfg, d, eval_reuse=eval_reuse)
        verified.append(v)
        job.log(f"{d.dev_id} {d.label or d.param}: {'verified' if v.patch_verified else 'not verified'}"
                f"{' (' + v.note + ')' if v.note and not v.patch_verified else ''}")
    result["deviations"] = [d.model_dump() for d in verified]
    result["deviation_source"] = source
    attributable = [d for d in verified if d.type in ATTRIBUTABLE]
    sweepable = [d for d in verified if d.type in SWEEPABLE]
    job.stage("deviations", "done")

    # 9. attribution ------------------------------------------------------------------------
    job.stage("attribution")
    for key, g in groups.items():
        if "swarm" not in g:
            continue
        for c in g["claims"]:
            info = result["claims"][c.claim_id]
            b = swarm.band(info["values"], c.n_seeds_reported or 1)
            if b.lo <= c.value <= b.hi:
                info["attribution"] = None
                continue
            if not attributable:
                info["attribution"] = None
                info["attribution_note"] = "no attributable deviation with a verified patch"
                continue
            job.log(f"{c.claim_id}: attributing a {c.value - b.mean:+.2f}-point gap over "
                    f"{len([d for d in attributable if d.patch_verified])} verified deviations")
            usable = [d for d in attributable if d.phase == "train" or g.get("eval_reuse")]
            att = attribution.attribute(pid, g["config"], usable, info["metric_key"], c.value, b.std,
                                        seeds=range(k), paired=paired, m=c.n_seeds_reported or 1, progress=job.log)
            job.state["rescores"] += att.rescores
            info["attribution"] = att.to_dict()
            # selection signal and band on the aligned configuration
            s_star = [d for d in usable if d.dev_id in att.survivors]
            aligned_cfg = patches.apply_patches(g["config"], [d.patch for d in s_star if d.phase == "train"])
            ares = swarm.collect(pid, aligned_cfg, range(n))
            avals = ares.metric(info["metric_key"])
            eval_spec = patches.merge_metric_specs([d.patch for d in s_star if d.phase == "eval"])
            if eval_spec:
                from .witness import rescore
                scale = adapter.metrics[info["metric_key"]].scale
                avals = [rescore.rescore_payload(r.predictions[info["metric_key"]], eval_spec["fn"],
                                                 eval_spec["kwargs"]) * scale for r in ares.runs if r.ok]
                job.state["rescores"] += len(avals)
            if avals:
                ab = swarm.band(avals, c.n_seeds_reported or 1)
                info["aligned_band"] = ab.model_dump()
                info["aligned_values"] = avals
                info["attribution"]["residual_in_noise"] = ab.lo <= c.value <= ab.hi
                info["selection_aligned"] = swarm.selection_signal(avals, c.value, "aligned").model_dump()
    job.stage("attribution", "done")

    # 10. specification sweep --------------------------------------------------------------
    job.stage("specification")
    for key, g in groups.items():
        if "swarm" not in g:
            continue
        for c in g["claims"]:
            info = result["claims"][c.claim_id]
            b = swarm.band(info["values"], c.n_seeds_reported or 1)
            if not sweepable:
                info["sci"] = spec.sci(c)
                continue
            sw = spec.sweep(pid, g["config"], c, info["metric_key"], sweepable, b, progress=job.log)
            info["spec"] = sw
            info["sci"] = spec.sci(c, sw["costs"], b.std, [d.param for d in sweepable])
    job.stage("specification", "done")

    _verdicts(paper, claims, result, job, runnable=True, groups=groups, det=result.get("determinism"),
              devs=result.get("deviations", []))


def _verdicts(paper, claims, result, job, runnable, groups, det=None, devs=None):
    job.stage("verdicts")
    group_of = {c.claim_id: g for g in groups.values() for c in g["claims"]}
    out = []
    for c in claims:
        info = result["claims"][c.claim_id]
        mp = info.get("mapping", {"status": "unmappable"})
        g = group_of.get(c.claim_id)
        flags = []
        if det and not det.get("deterministic"):
            flags.append("NON_DETERMINISTIC")
        if det and det.get("hash_sensitive"):
            flags.append("HASH_SEED_SENSITIVE")
        if devs and any(not d.get("togglable") for d in devs):
            flags.append("NON_TOGGLABLE_DEVIATIONS")
        src = (info.get("metric_key") and next((w["metric_sources"].get(info["metric_key"]) for w in
                                                result.get("witness", []) if w["config"] == info.get("config")), None))
        if src == "stdout":
            flags.append("WITNESS_PARTIAL")
        from .schemas import Band, SelectionSignal
        seed_band = Band.model_validate(info["seed_band"]) if info.get("seed_band") else None
        aligned = Band.model_validate(info["aligned_band"]) if info.get("aligned_band") else None
        sel_raw = info.get("selection_aligned") or info.get("selection")
        sel = SelectionSignal.model_validate(sel_raw) if sel_raw else None
        spec_band = tuple(info["spec"]["spec_band"]) if info.get("spec") else None
        sci_v = info.get("sci", {})
        sci_value = sci_v.get("weighted") if sci_v.get("weighted") is not None else sci_v.get("plain", 0.0)
        v = verdicts.decide(
            c.claim_id, c.value, preflight=info.get("preflight"), mapping_status=mp.get("status", "unmappable"),
            mapping_reason=mp.get("reason"), runnable=runnable and mp.get("status") == "mapped",
            baseline_failed=bool(g and g.get("failed")), baseline_error=g.get("failed") if g else None,
            seed_band=seed_band, aligned_band=aligned, spec_band=spec_band, attribution=info.get("attribution"),
            selection=sel, sci=sci_value or 0.0, flags=flags)
        info["verdict"] = v.model_dump()
        out.append(v.model_dump())
        _provenance(paper.paper_id, c, info, v)
    result["verdicts"] = out
    job.stage("verdicts", "done")


def _provenance(pid: str, c: Claim, info: dict, v) -> None:
    node = f"Claim({c.claim_id})"
    store.add_edge(pid, node, "cited_at", f"PDFSpan(p{c.source.page})", c.source.model_dump())
    for w in info.get("baseline_run_ids", [])[:1]:
        store.add_edge(pid, node, "executed_as", f"Run({w})", {"seeds": len(info.get("values", []))})
    if info.get("seed_band"):
        store.add_edge(pid, node, "measured_as", "SeedBand", info["seed_band"])
    for s in (info.get("attribution") or {}).get("shares", []):
        store.add_edge(pid, node, "attributed_to", f"Shapley({s['label']})", s)
    store.add_edge(pid, node, "judged", f"Verdict({v.verdict})", {"reasons": v.reasons, "flags": v.flags})


def start(paper_id: str, **kw) -> Job:
    job = Job(paper_id)
    t = threading.Thread(target=analyze, args=(paper_id, job), kwargs=kw, daemon=True, name=f"analyze-{paper_id}")
    t.start()
    return job
