import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { api, type PaperSummary } from "../api";
import { Empty, PageHead, VerdictChip, ago, useApp, useAsync, useJob } from "../ui";
import JobPanel from "../components/JobPanel";

function RegisterForm({ onStarted }: { onStarted: (paperId: string, jobId: string) => void }) {
  const { status } = useApp();
  const [repo, setRepo] = useState("");
  const [pdfUrl, setPdfUrl] = useState("");
  const [file, setFile] = useState<File | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const llmReady = !!(status?.llm.anthropic || status?.llm.local);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    const form = new FormData();
    form.set("repo_url", repo.trim());
    if (file) form.set("pdf", file);
    else form.set("pdf_url", pdfUrl.trim());
    setBusy(true);
    try {
      const r = await api.register(form);
      onStarted(r.paper_id, r.job_id);
      setRepo(""); setPdfUrl(""); setFile(null);
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="card" onSubmit={submit} style={{ display: "grid", gap: 14 }}>
      <div className="card-title" style={{ margin: 0 }}>Add a paper</div>
      <p className="ink2 small" style={{ margin: 0 }}>
        Claims are extracted with verified spans, an adapter is drafted and validated by running it, and the
        environment is rebuilt at the paper's date.
      </p>
      <label className="field">Repository URL
        <input className="input" placeholder="https://github.com/owner/repository" value={repo} onChange={(e) => setRepo(e.target.value)} required />
      </label>
      <label className="field">Paper PDF URL
        <input className="input" placeholder="https://arxiv.org/pdf/xxxx.xxxxxvN" value={pdfUrl} onChange={(e) => setPdfUrl(e.target.value)} disabled={!!file} />
      </label>
      <label className="field">or upload the PDF
        <input className="input" type="file" accept="application/pdf" onChange={(e) => setFile(e.target.files?.[0] ?? null)} />
      </label>
      {!llmReady && (
        <div className="banner warn">Extraction and mapping need a language model. Set ANTHROPIC_API_KEY in the .env file, or point INQUEST_LOCAL_LLM_URL at a local Ollama model, then restart the server.</div>
      )}
      {error && <div className="banner warn">{error}</div>}
      <div className="row"><button className="btn primary" disabled={busy || !llmReady || !repo || (!file && !pdfUrl)}>{busy ? "Registering" : "Register and analyse"}</button></div>
    </form>
  );
}

function AdapterReview({ paper }: { paper: PaperSummary }) {
  const { data, reload } = useAsync(() => api.adapter(paper.paper_id), [paper.paper_id]);
  const [text, setText] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  useJob(jobId, () => reload());
  useEffect(() => { if (data?.adapter) setText(JSON.stringify(data.adapter, null, 2)); }, [data]);
  if (!data) return null;
  if (!data.adapter) return <Empty title="No adapter yet">The Cartographer drafts one after extraction.</Empty>;
  const a = data.adapter as { validated?: boolean; validation_log?: string[] };
  const save = async () => {
    setError(null);
    try { setJobId((await api.putAdapter(paper.paper_id, JSON.parse(text))).job_id); } catch (e) { setError((e as Error).message); }
  };
  return (
    <div style={{ display: "grid", gap: 12 }}>
      <div className="row">
        <span className={`tag ${a.validated ? "v-good" : "v-serious"}`}><span className="dot" />{a.validated ? "Validated by execution" : "Not validated"}</span>
        <span className="muted small">origin: {data.origin}</span>
      </div>
      {(a.validation_log ?? []).length > 0 && <div className="log">{(a.validation_log ?? []).join("\n")}</div>}
      <textarea className="input" rows={14} value={text} onChange={(e) => setText(e.target.value)} spellCheck={false} />
      {error && <div className="banner warn">{error}</div>}
      <div className="row">
        <button className="btn" onClick={save} disabled={!!jobId}>Save and validate</button>
        {jobId && <JobPanel jobId={jobId} compact onDone={() => setJobId(null)} />}
      </div>
    </div>
  );
}

export default function Papers() {
  const { papers, paperId, setPaperId, refreshPapers, go } = useApp();
  const [jobId, setJobId] = useState<string | null>(null);
  const selected = papers.find((p) => p.paper_id === paperId) ?? null;
  const groups = [
    { title: "Curated corpus", items: papers.filter((p) => p.source === "corpus") },
    { title: "Registered papers", items: papers.filter((p) => p.source === "registered") },
    { title: "Controls", items: papers.filter((p) => p.source === "variant") },
  ];
  const run = async (fn: () => Promise<{ job_id: string }>) => {
    try { setJobId((await fn()).job_id); refreshPapers(); } catch (e) { alertInline((e as Error).message); }
  };
  const [msg, setMsg] = useState<string | null>(null);
  const alertInline = (m: string) => { setMsg(m); window.setTimeout(() => setMsg(null), 6000); };

  return (
    <div>
      <PageHead title="Papers" sub="Each paper pairs a PDF with the repository that supposedly implements it. Pick one to inspect, or register a new pair." />
      <div className="grid side">
        <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
          {groups.filter((g) => g.items.length).map((g) => (
            <div key={g.title} className="card" style={{ padding: 6 }}>
              <div className="card-title" style={{ padding: "12px 14px 0" }}>{g.title}</div>
              <table className="t">
                <tbody>
                  {g.items.map((p) => {
                    const vs = Object.values(p.analysis?.verdicts ?? {});
                    return (
                      <tr key={p.paper_id} className="clickable" onClick={() => setPaperId(p.paper_id)}
                        style={p.paper_id === paperId ? { boxShadow: "inset 3px 0 0 var(--ink)" } : undefined}>
                        <td>
                          <div style={{ fontWeight: 500 }}>{p.title}</div>
                          <div className="muted small">{p.arxiv ? `arXiv ${p.arxiv} · ` : ""}{p.repo_url?.replace("https://github.com/", "")}</div>
                        </td>
                        <td style={{ width: 1, whiteSpace: "nowrap" }}>
                          {p.active_job ? <span className="tag"><span className="dot" style={{ background: "var(--accent)" }} />{p.active_job.kind}</span>
                            : vs.length ? <span className="muted small">{vs.length} verdicts · {ago(p.analysis!.created)}</span>
                            : <span className="muted small">not analysed</span>}
                        </td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
          ))}
          <RegisterForm onStarted={(pid, jid) => { setPaperId(pid); setJobId(jid); refreshPapers(); }} />
        </div>

        <AnimatePresence mode="wait">
          {selected && (
            <motion.div key={selected.paper_id} initial={{ opacity: 0, x: 12 }} animate={{ opacity: 1, x: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.3 }}
              style={{ display: "grid", gap: 16, alignContent: "start" }}>
              <div className="card">
                <div className="card-title">Paper</div>
                <h2 style={{ fontFamily: "var(--serif)", fontWeight: 400, fontSize: 26, lineHeight: 1.15, margin: "0 0 6px" }}>{selected.title}</h2>
                <p className="ink2 small" style={{ margin: 0 }}>{selected.authors}{selected.venue ? ` · ${selected.venue}` : ""}</p>
                <div className="hr" />
                <div className="kv">
                  <dt>Repository</dt><dd><a href={selected.repo_url} target="_blank" rel="noreferrer">{selected.repo_url?.replace("https://", "")}</a></dd>
                  <dt>Commit</dt><dd className="mono">{selected.repo_sha?.slice(0, 12) ?? "HEAD"}</dd>
                  <dt>Provenance</dt><dd>{selected.provenance_note ?? selected.provenance}</dd>
                  <dt>Paper date</dt><dd>{selected.paper_date}</dd>
                  <dt>Claims</dt><dd>{selected.n_claims} ({selected.claims_source === "hand" ? "hand-authored ground truth" : "extracted"})</dd>
                  {selected.analysis && <><dt>Verdicts</dt><dd><div className="row" style={{ gap: 6 }}>{Object.entries(selected.analysis.verdicts).map(([c, v]) => <span key={c} className="row" style={{ gap: 4 }}><span className="muted small">{c}</span><VerdictChip verdict={v} /></span>)}</div></dd></>}
                </div>
                <div className="hr" />
                <div className="row">
                  <button className="btn primary" disabled={!!selected.active_job} onClick={() => run(() => api.analyze(selected.paper_id)).then(() => go("runs"))}>Analyse</button>
                  <button className="btn" disabled={!!selected.active_job} onClick={() => run(() => api.extract(selected.paper_id))}>Extract claims</button>
                  {selected.source === "registered" && <button className="btn" disabled={!!selected.active_job} onClick={() => run(() => api.cartographer(selected.paper_id))}>Draft adapter</button>}
                  <button className="btn ghost" onClick={() => go("claims")}>Claims</button>
                </div>
                {msg && <div className="banner warn" style={{ marginTop: 12 }}>{msg}</div>}
                {(jobId || selected.active_job) && <div style={{ marginTop: 14 }}><JobPanel jobId={jobId ?? selected.active_job!.job_id} compact onDone={() => { setJobId(null); refreshPapers(); }} /></div>}
              </div>
              <div className="card">
                <div className="card-title">Adapter review</div>
                <AdapterReview paper={selected} />
              </div>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </div>
  );
}
