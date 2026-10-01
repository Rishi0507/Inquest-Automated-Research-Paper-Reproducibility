import { useState } from "react";
import { api } from "../api";
import JobPanel from "../components/JobPanel";
import { Empty, PageHead, VerdictChip, useApp, useAsync } from "../ui";
import Select from "../components/Select";

export default function Controls() {
  const { papers, refreshPapers, go, setPaperId, analysisVersion } = useApp();
  const { data, reload } = useAsync(() => api.faults(), [analysisVersion]);
  const curated = papers.filter((p) => p.source === "corpus");
  const [target, setTarget] = useState<string>("");
  const [fault, setFault] = useState("random");
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [revealed, setRevealed] = useState<Record<string, any>>({});
  const paper = target || curated[0]?.paper_id || "";
  const applicable = data ? Object.entries(data.catalogue).filter(([, ps]) => ps.includes(paper)).map(([k]) => k) : [];

  const act = async (fn: () => Promise<{ job_id: string }>) => {
    setError(null);
    try { setJobId((await fn()).job_id); } catch (e) { setError((e as Error).message); }
  };
  const reveal = async (v: string) => {
    try { const r = await api.reveal(v); setRevealed((m) => ({ ...m, [v]: r })); reload(); } catch (e) { setError((e as Error).message); }
  };

  return (
    <div>
      <PageHead
        title="Controls"
        sub="Faults are planted blind: the manifest is sealed by its SHA-256 before analysis and only revealed afterwards. Clean controls measure false positives. Every control is disclosed as a control."
      />
      <div className="grid side">
        <div className="card" style={{ padding: 6 }}>
          <div className="card-title" style={{ padding: "12px 14px 0" }}>Variants</div>
          {!data?.variants.length ? (
            <div style={{ padding: 14 }}><Empty title="No controls yet">Plant a fault or create a clean control from a curated paper.</Empty></div>
          ) : (
            <table className="t">
              <thead><tr><th>Variant</th><th>Kind</th><th>Verdicts</th><th>Outcome</th></tr></thead>
              <tbody>
                {data.variants.map((v) => {
                  const p = papers.find((x) => x.paper_id === v.paper_id);
                  const rev = revealed[v.paper_id] ?? v.revealed;
                  return (
                    <tr key={v.paper_id}>
                      <td>
                        <div className="mono small">{v.paper_id}</div>
                        {v.sealed && <div className="muted small mono" title={v.sealed}>sealed {v.sealed.slice(0, 16)}…</div>}
                      </td>
                      <td>{v.kind}</td>
                      <td>
                        <div className="row" style={{ gap: 6 }}>
                          {p?.analysis ? Object.entries(p.analysis.verdicts).map(([c, vd]) => <VerdictChip key={c} verdict={vd} />) : <span className="muted small">not analysed</span>}
                        </div>
                      </td>
                      <td className="small">
                        {rev ? (
                          <div>
                            <div><strong>{rev.manifest?.fault}</strong> {rev.seal_intact ? "(seal intact)" : "(seal broken)"}</div>
                            <div className={rev.outcome?.top1_correct ? "v-good" : "v-critical"}>
                              {rev.outcome?.analysed ? (rev.outcome.top1_correct ? "ranked first by attribution" : rev.outcome.detected ? "detected, not ranked first" : "not detected") : "not analysed"}
                            </div>
                          </div>
                        ) : (
                          <div className="row">
                            <button className="btn small" onClick={() => { setPaperId(v.paper_id); go("runs", v.paper_id); }}>{p?.analysis ? "Re-analyse" : "Analyse"}</button>
                            {v.kind === "planted" && p?.analysis && <button className="btn small" onClick={() => reveal(v.paper_id)}>Reveal</button>}
                          </div>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          )}
        </div>
        <div style={{ display: "grid", gap: 16, alignContent: "start" }}>
          <div className="card" style={{ display: "grid", gap: 12 }}>
            <div className="card-title" style={{ margin: 0 }}>Create a control</div>
            <div className="field">Paper
              <Select block label="Paper" value={paper} onChange={setTarget}
                options={curated.map((p) => ({ value: p.paper_id, label: p.title, hint: p.repo_url?.replace("https://github.com/", "") }))} />
            </div>
            <div className="field">Fault
              <Select block label="Fault" value={fault} onChange={setFault}
                options={[{ value: "random", label: "Drawn at random", hint: "blind: the kind stays sealed until reveal" },
                  ...applicable.map((k) => ({ value: k, label: k.replace(/_/g, " "), hint: "known kind, manifest still sealed" }))]} />
            </div>
            <div className="row">
              <button className="btn primary" disabled={!applicable.length || !!jobId} onClick={() => act(() => api.plant(paper, fault))}>Plant fault</button>
              <button className="btn" disabled={!!jobId} onClick={() => act(() => api.clean(paper))}>Clean control</button>
            </div>
            {!applicable.length && <p className="muted small" style={{ margin: 0 }}>No catalogued fault applies to this repository.</p>}
            {error && <div className="banner warn">{error}</div>}
            {jobId && <JobPanel jobId={jobId} compact onDone={() => { setJobId(null); refreshPapers(); reload(); }} />}
          </div>
          {data && Object.keys(data.not_applicable).length > 0 && (
            <div className="card">
              <div className="card-title">Catalogue coverage</div>
              {Object.entries(data.not_applicable).map(([k, why]) => <p key={k} className="small ink2" style={{ margin: "4px 0" }}><span className="mono">{k}</span>: {why}.</p>)}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
