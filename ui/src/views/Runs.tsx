import { useState } from "react";
import { api } from "../api";
import { Empty, PageHead, PaperPicker, Stat, useApp, useAsync, useJob, ago } from "../ui";
import { JobLog, Stepper } from "../components/JobPanel";

export default function Runs() {
  const { paperId, papers, refreshPapers, go } = useApp();
  const paper = papers.find((p) => p.paper_id === paperId);
  const [started, setStarted] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const { data: jobs, reload } = useAsync(() => (paperId ? api.jobs(paperId) : Promise.resolve([])), [paperId, started]);
  const current = started ?? paper?.active_job?.job_id ?? jobs?.[0]?.job_id ?? null;
  const job = useJob(current, () => { refreshPapers(); reload(); });

  const start = async (deviations?: string) => {
    if (!paperId) return;
    setError(null);
    try {
      const r = await api.analyze(paperId, deviations ? { deviations } : {});
      setStarted(r.job_id);
      refreshPapers();
    } catch (e) {
      setError((e as Error).message);
    }
  };
  const busy = job?.status === "running" || job?.status === "queued";

  return (
    <div>
      <PageHead
        title="Runs"
        sub="Every configuration runs in the sandbox under the Witness. Runs are cached by content hash, metric-definition changes are re-scored from captured predictions, and seeds are paired when repeated runs agree."
        right={<>
          <PaperPicker />
          <button className="btn primary" disabled={busy || !paperId} onClick={() => start()}>{busy ? "Running" : "Analyse"}</button>
        </>}
      />
      {error && <div className="banner warn" style={{ marginBottom: 16 }}>{error}</div>}
      {!job ? (
        <Empty title="Nothing has run for this paper">Start an analysis. The environment is rebuilt at the paper's date, then the determinism audit, seed swarm, attribution and specification sweep run in order.</Empty>
      ) : (
        <div style={{ display: "grid", gap: 16 }}>
          <div className="card">
            <div className="card-title">
              <span>{job.kind === "analyze" ? "Analysis" : job.kind} {job.status === "running" ? `· ${job.stage ?? ""}` : `· ${job.status}`}</span>
              <span className="muted small">started {ago(job.started)}{job.finished ? `, took ${Math.round(job.finished - job.started)} s` : ""}</span>
            </div>
            {job.kind === "analyze" && <Stepper job={job} />}
            <div className="counter-row" style={{ marginTop: 22 }}>
              <Stat value={job.runs_done} label="runs requested" />
              <Stat value={job.runs_executed} label="runs executed" />
              <Stat value={job.cache_hits} label="served from cache" />
              <Stat value={job.rescores} label="eval re-scores, no training" />
            </div>
            {job.error && <div className="banner warn" style={{ marginTop: 16 }}>{job.error}</div>}
            {job.status === "done" && job.kind === "analyze" && (
              <div className="row" style={{ marginTop: 16 }}>
                <button className="btn" onClick={() => go("analysis")}>Open the analysis</button>
                <button className="btn ghost" onClick={() => go("claims")}>Claims</button>
              </div>
            )}
          </div>
          <div className="card">
            <div className="card-title">Log</div>
            <JobLog job={job} height={420} />
          </div>
          {jobs && jobs.length > 1 && (
            <div className="card" style={{ padding: 6 }}>
              <div className="card-title" style={{ padding: "12px 14px 0" }}>Earlier jobs</div>
              <table className="t">
                <tbody>
                  {jobs.slice(0, 8).map((j) => (
                    <tr key={j.job_id} className="clickable" onClick={() => setStarted(j.job_id)}>
                      <td className="mono muted">{j.job_id}</td><td>{j.kind}</td><td>{j.status}</td>
                      <td className="tnum">{j.runs_executed} executed, {j.cache_hits} cached</td>
                      <td className="muted small">{ago(j.started)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
