import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { api, pdfPage, type Span } from "../api";
import { VerdictChip, fmt, useApp, useAsync } from "../ui";

function CodeView({ paperId, refStr }: { paperId: string; refStr: string }) {
  const [path, lineStr] = refStr.split(":");
  const line = parseInt(lineStr || "1", 10);
  const { data, error } = useAsync(() => api.code(paperId, path, line), [paperId, refStr]);
  if (error) return <div className="muted small">{error}</div>;
  if (!data) return <div className="code" style={{ height: 200 }} />;
  return (
    <div className="code">
      <div className="row small" style={{ padding: "8px 12px", borderBottom: "1px solid var(--line)", fontFamily: "var(--sans)" }}>
        <span className="mono">{data.path}:{data.line}</span><span className="spacer" /><span className="muted mono">{data.sha.slice(0, 10)}</span>
      </div>
      <div style={{ padding: "6px 0" }}>
        {data.lines.map((l, i) => {
          const n = data.start + i;
          return (
            <div key={n} className={`ln ${n === data.line ? "hit" : ""}`}>
              <span className="no">{n}</span><pre>{l || " "}</pre>
            </div>
          );
        })}
      </div>
    </div>
  );
}

function RunLog({ runId }: { runId: string }) {
  const { data } = useAsync(() => api.run(runId), [runId]);
  if (!data) return null;
  return (
    <div style={{ display: "grid", gap: 8 }}>
      <div className="muted small mono" style={{ wordBreak: "break-all" }}>{data.command.argv?.slice(1).join(" ")} <span className="muted">(cwd {data.command.cwd?.split(/[\\/]/).slice(-2).join("/")})</span></div>
      <div className="log" style={{ maxHeight: 220 }}>{data.stdout_tail}</div>
    </div>
  );
}

export default function EvidenceDrawer() {
  const { claimId, openClaim, paperId } = useApp();
  const open = !!(claimId && paperId);
  const { data } = useAsync(() => (open ? api.evidence(paperId!, claimId!) : Promise.resolve(null)), [paperId, claimId]);
  const [span, setSpan] = useState<Span | null>(null);
  const [codeRef, setCodeRef] = useState<string | null>(null);
  const [run, setRun] = useState<string | null>(null);

  useEffect(() => {
    if (!data) return;
    setSpan(data.claim.source);
    setCodeRef(data.code_refs[0]?.ref ?? null);
    setRun(data.runs[0] ?? null);
  }, [data]);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => { if (e.key === "Escape") openClaim(null); };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [openClaim]);

  const c = data?.claim;
  const info = data?.info;
  const stated = Object.entries(c?.stated_config ?? {});

  return (
    <AnimatePresence>
      {open && (
        <>
          <motion.div className="scrim" initial={{ opacity: 0 }} animate={{ opacity: 1 }} exit={{ opacity: 0 }} onClick={() => openClaim(null)} />
          <motion.aside className="drawer" initial={{ x: 40, opacity: 0 }} animate={{ x: 0, opacity: 1 }} exit={{ x: 40, opacity: 0 }}
            transition={{ duration: 0.35, ease: [0.22, 1, 0.36, 1] }} role="dialog" aria-label="Evidence">
            <div className="drawer-head">
              <div style={{ flex: 1 }}>
                <div className="row" style={{ marginBottom: 6 }}>
                  <span className="mono muted">{claimId}</span>
                  <VerdictChip verdict={info?.verdict?.verdict} />
                  {info?.verdict?.flags.map((f) => <span key={f} className="tag">{f.toLowerCase().replace(/_/g, " ")}</span>)}
                </div>
                <div style={{ fontSize: 18, fontWeight: 500 }}>{c?.text ?? "Loading"}</div>
                {info?.seed_band && (
                  <div className="ink2 small" style={{ marginTop: 6 }}>
                    Reported {c?.value_text}. Seed band [{fmt(info.seed_band.lo)}, {fmt(info.seed_band.hi)}], mean {fmt(info.seed_band.mean)} over {info.seed_band.n} runs.
                  </div>
                )}
              </div>
              <button className="icon-btn" aria-label="Close" onClick={() => openClaim(null)}>
                <svg width="14" height="14" viewBox="0 0 24 24" stroke="currentColor" strokeWidth="2" fill="none"><path d="M6 6l12 12M18 6L6 18" strokeLinecap="round" /></svg>
              </button>
            </div>
            <div className="drawer-body">
              {c && (
                <div style={{ display: "grid", gap: 18 }}>
                  <div className="evidence-grid">
                    <div style={{ display: "grid", gap: 8, alignContent: "start" }}>
                      <div className="row small">
                        <strong>Paper</strong>
                        <span className="muted">page {span?.page}{span?.table ? ` · ${span.table}` : ""}</span>
                        <span className="spacer" />
                        <select className="select" style={{ fontSize: 12 }} value={span ? JSON.stringify(span) : ""} onChange={(e) => setSpan(JSON.parse(e.target.value))}>
                          <option value={JSON.stringify(c.source)}>claimed value</option>
                          {stated.map(([k, p]) => <option key={k} value={JSON.stringify(p.span)}>{k} = {String(p.value)}</option>)}
                        </select>
                      </div>
                      {span && (
                        <div className="pdf-frame">
                          <img key={JSON.stringify(span)} src={pdfPage(paperId!, span, 2)} alt={`Page ${span.page} with the cited words highlighted`}
                            onLoad={(e) => {
                              const img = e.currentTarget;
                              const frame = img.parentElement!;
                              const scale = img.clientWidth / (img.naturalWidth / 2);
                              frame.scrollTo({ top: Math.max(0, span.bbox[1] * scale - 160), behavior: "smooth" });
                            }} />
                        </div>
                      )}
                    </div>
                    <div style={{ display: "grid", gap: 8, alignContent: "start" }}>
                      <div className="row small">
                        <strong>Code</strong>
                        <span className="spacer" />
                        {data!.code_refs.length > 0 && (
                          <select className="select" style={{ fontSize: 12 }} value={codeRef ?? ""} onChange={(e) => setCodeRef(e.target.value)}>
                            {data!.code_refs.map((r, i) => <option key={i} value={r.ref}>{r.ref} · {r.why}</option>)}
                          </select>
                        )}
                      </div>
                      {codeRef ? <CodeView paperId={paperId!} refStr={codeRef} /> : <div className="muted small">No code location recorded for this claim.</div>}
                    </div>
                  </div>
                  <div>
                    <div className="row small" style={{ marginBottom: 8 }}>
                      <strong>Run log</strong>
                      <span className="spacer" />
                      {data!.runs.length > 1 && (
                        <select className="select" style={{ fontSize: 12 }} value={run ?? ""} onChange={(e) => setRun(e.target.value)}>
                          {data!.runs.map((r) => <option key={r} value={r}>run {r}</option>)}
                        </select>
                      )}
                    </div>
                    {run ? <RunLog runId={run} /> : <div className="muted small">{info?.mapping?.status && info.mapping.status !== "mapped" ? `No run: ${info.mapping.reason}` : "No runs yet."}</div>}
                  </div>
                  {info?.verdict?.reasons?.length ? (
                    <div className="card flat" style={{ background: "var(--surface-2)" }}>
                      <div className="card-title">Why this verdict</div>
                      {info.verdict.reasons.map((r, i) => <p key={i} style={{ margin: "4px 0" }} className="ink2">{r}</p>)}
                    </div>
                  ) : null}
                </div>
              )}
            </div>
          </motion.aside>
        </>
      )}
    </AnimatePresence>
  );
}
