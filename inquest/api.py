"""HTTP API and static web interface."""
from __future__ import annotations

import json
import re
import threading
import time
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config, corpus, llm, pdfindex, pipeline, runner, store
from .schemas import AdapterSpec

app = FastAPI(title="Inquest", version="0.1.0")
_busy: dict[str, str] = {}
_busy_lock = threading.Lock()


def _paper(paper_id: str) -> corpus.Paper:
    try:
        return corpus.get(paper_id)
    except corpus.PaperNotFound:
        raise HTTPException(404, f"unknown paper {paper_id}")


def _background(paper_id: str, kind: str, fn, *args) -> dict:
    """Run `fn(job, *args)` in a thread; one job per paper at a time."""
    with _busy_lock:
        current = _busy.get(paper_id)
        if current:
            state = store.get_job(current)
            if state and state.get("status") in ("queued", "running"):
                raise HTTPException(409, f"{paper_id} already has a running job ({state['kind']})")
        job = pipeline.Job(paper_id, kind)
        _busy[paper_id] = job.job_id

    def target():
        job.state["status"] = "running"
        job.save()
        try:
            out = fn(job, *args)
            if job.state["status"] == "running":
                job.state["status"] = "done"
            if isinstance(out, dict):
                job.state["result"] = out
        except Exception as exc:
            job.state["status"] = "failed"
            job.state["error"] = f"{type(exc).__name__}: {exc}"
            job.log(f"failed: {exc}")
        finally:
            job.state["finished"] = time.time()
            job.save()

    threading.Thread(target=target, daemon=True, name=f"{kind}-{paper_id}").start()
    return {"job_id": job.job_id}


# ------------------------------------------------------------------ status and corpus

@app.get("/api/status")
def status():
    return {"llm": llm.status(), "sandbox": config.SANDBOX, "workers": config.WORKERS,
            "threads_per_run": config.THREADS_PER_RUN, "counters": store.counters(),
            "standing_note": config.STANDING_NOTE}


@app.get("/api/papers")
def papers():
    out = []
    for pid, p in corpus.all_papers().items():
        s = p.summary()
        a = store.latest_analysis(pid)
        s["analysis"] = None if not a else {
            "analysis_id": a["analysis_id"], "created": a["created"], "status": a.get("status"),
            "verdicts": {v["claim_id"]: v["verdict"] for v in a.get("verdicts", [])}}
        jobs = store.jobs_for(pid)
        s["active_job"] = next((j for j in jobs if j.get("status") in ("queued", "running")), None)
        out.append(s)
    return out


@app.get("/api/papers/{paper_id}")
def paper(paper_id: str):
    p = _paper(paper_id)
    return p.summary() | {"meta": p.meta}


_ID_RE = re.compile(r"[^a-z0-9-]+")


@app.post("/api/papers")
async def register(repo_url: str = Form(...), pdf: Optional[UploadFile] = File(None), pdf_url: str = Form(""),
                   title: str = Form(""), paper_date: str = Form(""), paper_id: str = Form("")):
    """Unseen-paper path: register a PDF and a repository, then extract, map and validate."""
    if not pdf and not pdf_url:
        raise HTTPException(400, "provide a PDF file or a PDF URL")
    if not repo_url.startswith(("https://", "http://")):
        raise HTTPException(400, "repository URL must be http(s)")
    base = paper_id or (title or repo_url.rstrip("/").split("/")[-1])
    pid = _ID_RE.sub("-", base.lower()).strip("-")[:48] or f"paper-{int(time.time())}"
    if pid in corpus.all_papers():
        raise HTTPException(409, f"{pid} already exists")
    d = config.UPLOADS / pid
    d.mkdir(parents=True, exist_ok=True)
    meta = {"paper_id": pid, "title": title or pid, "repo_url": repo_url, "paper_date": paper_date or
            time.strftime("%Y-%m-%d"), "provenance": "unknown", "role": "unseen", "deviation_source": "finder"}
    if pdf:
        data = await pdf.read()
        if not data.startswith(b"%PDF"):
            raise HTTPException(400, "the uploaded file is not a PDF")
        (d / "paper.pdf").write_bytes(data)
    else:
        meta["pdf_url"] = pdf_url
    store.put_paper(pid, meta, "registered")
    p = corpus.get(pid)
    try:
        p.ensure_pdf()
    except Exception as exc:
        store.delete_paper(pid)
        raise HTTPException(400, f"could not fetch the PDF: {exc}")
    if not paper_date:
        meta["paper_date"] = _guess_date(p.pdf_path) or meta["paper_date"]
    if not title:
        meta["title"] = _guess_title(p.pdf_path) or pid
    store.put_paper(pid, meta, "registered")
    job = _background(pid, "register", _register_job)
    return {"paper_id": pid, **job}


def _guess_title(pdf: Path) -> Optional[str]:
    try:
        md = pdfindex.doc(pdf).metadata or {}
        if md.get("title") and len(md["title"]) > 8:
            return md["title"].strip()
        first = pdfindex.page_text(pdf, 1).splitlines()
        return next((ln.strip() for ln in first if len(ln.strip()) > 12), None)
    except Exception:
        return None


def _guess_date(pdf: Path) -> Optional[str]:
    m = re.search(r"arXiv:\d{4}\.\d{4,5}v\d+\s+\[[^\]]+\]\s+(\d{1,2})\s+(\w{3})\w*\s+(\d{4})", pdfindex.page_text(pdf, 1))
    if not m:
        return None
    months = {k: i for i, k in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                           "nov", "dec"], start=1)}
    mon = months.get(m.group(2).lower()[:3])
    return f"{m.group(3)}-{mon:02d}-{int(m.group(1)):02d}" if mon else None


def _register_job(job: pipeline.Job) -> dict:
    from . import cartographer, extract
    pid = job.paper_id
    p = corpus.get(pid)
    job.log("cloning repository")
    p.ensure_repo()
    job.stage("claims")
    res = extract.extract(pid, p.pdf_path, log=job.log)
    job.stage("claims", "done")
    job.stage("mapping")
    rep = cartographer.draft(pid, log=job.log)
    job.stage("mapping", "done")
    return {"extraction": res["report"], "cartographer": {k: v for k, v in rep.items() if k != "attempts"}}


@app.delete("/api/papers/{paper_id}")
def remove(paper_id: str):
    p = _paper(paper_id)
    if p.source != "registered":
        raise HTTPException(400, "only registered papers can be removed")
    store.delete_paper(paper_id)
    return {"removed": paper_id}


# ------------------------------------------------------------------ claims and extraction

@app.get("/api/papers/{paper_id}/claims")
def claims(paper_id: str, source: Optional[str] = None):
    p = _paper(paper_id)
    src = source or p.claims_source
    cl = p.hand_claims() if src == "hand" else p.extracted_claims()
    return {"source": src, "claims": [c.model_dump() for c in cl],
            "extraction_report": store.kv_get(f"extraction_report:{paper_id}")}


class SourceBody(BaseModel):
    source: str


@app.put("/api/papers/{paper_id}/claims_source")
def set_claims_source(paper_id: str, body: SourceBody):
    _paper(paper_id)
    if body.source not in ("hand", "extracted"):
        raise HTTPException(400, "source must be hand or extracted")
    store.kv_set(f"claims_source:{paper_id}", body.source)
    return {"source": body.source}


@app.post("/api/papers/{paper_id}/extract")
def run_extract(paper_id: str):
    p = _paper(paper_id)
    if not llm.available():
        raise HTTPException(412, "no language model configured; set ANTHROPIC_API_KEY in .env")

    def fn(job):
        from . import extract
        p.ensure_pdf()
        return extract.extract(paper_id, p.pdf_path, log=job.log)["report"]
    return _background(paper_id, "extract", fn)


# ------------------------------------------------------------------ adapter

@app.get("/api/papers/{paper_id}/adapter")
def adapter(paper_id: str):
    p = _paper(paper_id)
    a = p.adapter()
    return {"adapter": a.model_dump() if a else None, "origin": p.adapter_origin(),
            "report": store.kv_get(f"cartographer_report:{paper_id}")}


@app.put("/api/papers/{paper_id}/adapter")
def edit_adapter(paper_id: str, body: dict):
    _paper(paper_id)
    try:
        AdapterSpec.model_validate({**body, "paper_id": paper_id})
    except Exception as exc:
        raise HTTPException(400, f"invalid adapter: {exc}")

    def fn(job):
        from . import cartographer
        return cartographer.accept_edit(paper_id, body, log=job.log)
    return _background(paper_id, "validate", fn)


@app.post("/api/papers/{paper_id}/cartographer")
def run_cartographer(paper_id: str):
    _paper(paper_id)
    if not llm.available():
        raise HTTPException(412, "no language model configured; set ANTHROPIC_API_KEY in .env")

    def fn(job):
        from . import cartographer
        rep = cartographer.draft(paper_id, log=job.log)
        return {k: v for k, v in rep.items() if k != "attempts"}
    return _background(paper_id, "cartographer", fn)


# ------------------------------------------------------------------ analysis

class AnalyzeBody(BaseModel):
    deviations: Optional[str] = None
    seeds: Optional[int] = None


@app.post("/api/papers/{paper_id}/analyze")
def analyze(paper_id: str, body: AnalyzeBody | None = None):
    p = _paper(paper_id)
    if p.adapter() is None:
        raise HTTPException(412, "this paper has no adapter yet")
    body = body or AnalyzeBody()

    def fn(job):
        res = pipeline.analyze(paper_id, job, deviation_source=body.deviations, n_seeds=body.seeds)
        return {"analysis_id": res["analysis_id"], "status": res.get("status")}
    return _background(paper_id, "analyze", fn)


@app.get("/api/jobs/{job_id}")
def job(job_id: str):
    j = store.get_job(job_id)
    if not j:
        raise HTTPException(404, "unknown job")
    return j


@app.get("/api/papers/{paper_id}/jobs")
def jobs(paper_id: str):
    return store.jobs_for(paper_id)


@app.get("/api/papers/{paper_id}/analysis")
def analysis(paper_id: str):
    _paper(paper_id)
    a = store.latest_analysis(paper_id)
    if not a:
        raise HTTPException(404, "not analysed yet")
    return a


@app.get("/api/papers/{paper_id}/verdicts")
def verdicts(paper_id: str):
    return (store.latest_analysis(paper_id) or {}).get("verdicts", [])


@app.get("/api/papers/{paper_id}/witness")
def witness(paper_id: str):
    return (store.latest_analysis(paper_id) or {}).get("witness", [])


@app.get("/api/papers/{paper_id}/stated_vs_observed")
def stated_vs_observed(paper_id: str):
    from . import deviations
    p = _paper(paper_id)
    a = store.latest_analysis(paper_id) or {}
    blocks = a.get("witness") or []
    if not blocks:
        return []
    claims = p.claims()
    out = []
    for w in blocks:
        cids = [cid for cid, info in a["claims"].items() if info.get("config") == w["config"]]
        relevant = [c for c in claims if c.claim_id in cids] or claims
        out.append({"config": w["config"], "run_id": w["run_id"], "rows": deviations.stated_vs_observed(relevant, w)})
    return out


@app.get("/api/claims/{paper_id}/{claim_id}/evidence")
def evidence(paper_id: str, claim_id: str):
    p = _paper(paper_id)
    a = store.latest_analysis(paper_id) or {}
    info = a.get("claims", {}).get(claim_id)
    try:
        claim = p.claim(claim_id).model_dump()
    except KeyError:
        raise HTTPException(404, "unknown claim")
    code_refs = []
    mp = (info or {}).get("mapping") or {}
    if mp.get("code_ref"):
        code_refs.append({"ref": mp["code_ref"], "why": "metric computed here" if mp.get("status") == "mapped"
                          else "mapping evidence"})
    for w in a.get("witness", []) or []:
        if info and w["config"] == info.get("config"):
            for mkey, src in (w.get("metric_sources") or {}).items():
                if mkey == info.get("metric_key") and src.startswith("witness:"):
                    code_refs.append({"ref": src.split(":", 1)[1], "why": "observed metric call"})
    for d in a.get("deviations", []) or []:
        if d.get("code_ref"):
            code_refs.append({"ref": d["code_ref"], "why": f"{d['dev_id']} {d.get('label') or d['param']}"})
    run_ids = (info or {}).get("baseline_run_ids", [])[:3]
    return {"claim": claim, "info": info, "code_refs": code_refs, "runs": run_ids,
            "stated": claim.get("stated_config", {})}


@app.get("/api/claims/{paper_id}/{claim_id}/spec")
def spec(paper_id: str, claim_id: str):
    info = (store.latest_analysis(paper_id) or {}).get("claims", {}).get(claim_id, {})
    return {"spec": info.get("spec"), "sci": info.get("sci")}


# ------------------------------------------------------------------ evidence sources

@app.get("/api/pdf/{paper_id}/page/{page}.png")
def pdf_page(paper_id: str, page: int, x0: Optional[float] = None, y0: Optional[float] = None,
             x1: Optional[float] = None, y1: Optional[float] = None, zoom: float = 2.0):
    p = _paper(paper_id)
    pdf = p.ensure_pdf()
    if page < 1 or page > pdfindex.page_count(pdf):
        raise HTTPException(404, "page out of range")
    bbox = (x0, y0, x1, y1) if None not in (x0, y0, x1, y1) else None
    return Response(pdfindex.render(pdf, page, bbox, zoom=min(max(zoom, 1.0), 3.0)), media_type="image/png",
                    headers={"Cache-Control": "max-age=3600"})


@app.get("/api/pdf/{paper_id}/info")
def pdf_info(paper_id: str):
    pdf = _paper(paper_id).ensure_pdf()
    d = pdfindex.doc(pdf)
    return {"pages": len(d), "sizes": [[pg.rect.width, pg.rect.height] for pg in d]}


@app.get("/api/pdf/{paper_id}/file")
def pdf_file(paper_id: str):
    return FileResponse(_paper(paper_id).ensure_pdf(), media_type="application/pdf")


@app.get("/api/code/{paper_id}")
def code(paper_id: str, path: str, line: int = 1, context: int = 14):
    p = _paper(paper_id)
    root = p.repo_path.resolve()
    target = (root / path).resolve()
    if root not in target.parents or not target.is_file():
        raise HTTPException(404, "file not in repository")
    lines = target.read_text(encoding="utf-8", errors="replace").splitlines()
    lo, hi = max(1, line - context), min(len(lines), line + context)
    return {"path": path, "line": line, "start": lo, "lines": lines[lo - 1:hi], "total": len(lines),
            "sha": p.repo_sha}


@app.get("/api/runs/{run_id}")
def run_info(run_id: str):
    d = config.RUNS / run_id
    if not re.fullmatch(r"[a-f0-9]{12}", run_id) or not d.exists():
        raise HTTPException(404, "unknown run")
    res = json.loads((d / "result.json").read_text(encoding="utf-8")) if (d / "result.json").exists() else {}
    cmd = json.loads((d / "command.json").read_text(encoding="utf-8")) if (d / "command.json").exists() else {}
    stdout = (d / "stdout.txt").read_text(encoding="utf-8", errors="replace") if (d / "stdout.txt").exists() else ""
    lines = stdout.splitlines()
    return {"result": res, "command": cmd, "stdout_tail": "\n".join(lines[-60:]), "stdout_lines": len(lines)}


# ------------------------------------------------------------------ ledger, faults, evaluation, dossier

@app.get("/api/consensus")
def consensus(model: Optional[str] = None, dataset: Optional[str] = None, metric: Optional[str] = None):
    from . import ledger
    return ledger.groups(model, dataset, metric)


@app.get("/api/faults")
def fault_list():
    from . import faults
    return {"variants": faults.list_variants(),
            "catalogue": {k: sorted(v) for k, v in faults.CATALOGUE.items()},
            "not_applicable": faults.NOT_APPLICABLE_NOTE}


class PlantBody(BaseModel):
    paper_id: str
    fault: str = "random"


@app.post("/api/faults/plant")
def fault_plant(body: PlantBody):
    from . import faults
    _paper(body.paper_id)

    def fn(job):
        return faults.plant(body.paper_id, body.fault)
    return _background(body.paper_id, "plant", fn)


@app.post("/api/faults/clean/{paper_id}")
def fault_clean(paper_id: str):
    from . import faults
    _paper(paper_id)
    return _background(paper_id, "clean", lambda job: faults.clean(paper_id))


@app.post("/api/faults/reveal/{variant_id}")
def fault_reveal(variant_id: str):
    from . import faults
    p = _paper(variant_id)
    if p.meta.get("variant_kind") != "planted":
        raise HTTPException(400, "only planted variants have a sealed manifest")
    return faults.reveal(variant_id)


@app.get("/api/eval")
def eval_results():
    return store.kv_get("eval_results") or {}


@app.post("/api/eval/run")
def eval_run():
    from . import evaluate
    return _background("__eval__", "evaluate", lambda job: {"generated": evaluate.run("all")["generated"]})


@app.post("/api/papers/{paper_id}/dossier")
def make_dossier(paper_id: str):
    from . import dossier
    _paper(paper_id)
    try:
        path = dossier.export(paper_id)
    except RuntimeError as exc:
        raise HTTPException(412, str(exc))
    return {"path": str(path), "url": f"/api/papers/{paper_id}/dossier.pdf"}


@app.get("/api/papers/{paper_id}/dossier.pdf")
def get_dossier(paper_id: str):
    path = config.EXPORTS / paper_id / f"{paper_id}-dossier.pdf"
    if not path.exists():
        raise HTTPException(404, "export the dossier first")
    return FileResponse(path, media_type="application/pdf", filename=path.name)


@app.get("/api/papers/{paper_id}/figures/{name}")
def figure(paper_id: str, name: str):
    if not re.fullmatch(r"[A-Za-z0-9_-]+\.png", name):
        raise HTTPException(404, "unknown figure")
    path = config.EXPORTS / paper_id / name
    if not path.exists():
        raise HTTPException(404, "unknown figure")
    return FileResponse(path, media_type="image/png")


# ------------------------------------------------------------------ static UI

_UI = config.ROOT / "ui" / "dist"
if _UI.exists():
    app.mount("/assets", StaticFiles(directory=_UI / "assets"), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa(full_path: str):
        if full_path.startswith("api/"):
            raise HTTPException(404)
        f = _UI / full_path
        if full_path and f.is_file():
            return FileResponse(f)
        return FileResponse(_UI / "index.html")
