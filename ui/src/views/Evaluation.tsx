import { useState } from "react";
import { api } from "../api";
import JobPanel from "../components/JobPanel";
import { Empty, PageHead, fmt, pct, useAsync } from "../ui";

export default function Evaluation() {
  const { data, reload } = useAsync(() => api.evalResults(), []);
  const [jobId, setJobId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const r = data ?? {};
  const has = Object.keys(r).length > 0;

  const start = async () => {
    setError(null);
    try { setJobId((await api.evalRun()).job_id); } catch (e) { setError((e as Error).message); }
  };

  return (
    <div>
      <PageHead
        title="Evaluation"
        sub="What was measured, reported as measured. Planted faults are disclosed controls; clean controls count false positives; the seed pool calibrates the selection signal."
        right={<button className="btn primary" disabled={!!jobId} onClick={start}>{jobId ? "Running" : "Run E1 to E7"}</button>}
      />
      {error && <div className="banner warn" style={{ marginBottom: 16 }}>{error}</div>}
      {jobId && <div className="card" style={{ marginBottom: 16 }}><JobPanel jobId={jobId} compact onDone={() => { setJobId(null); reload(); }} /></div>}
      {!has ? (
        <Empty title="No evaluation results yet">Run the harness. It reuses cached runs, so it is fast once analyses exist.</Empty>
      ) : (
        <div style={{ display: "grid", gap: 16 }}>
          <div className="grid two">
            <div className="card">
              <div className="card-title">E1 · Extraction against ground truth</div>
              <table className="t"><tbody>
                {(r.E1?.rows ?? []).map((x: any) => (
                  <tr key={x.paper}><td>{x.paper}</td><td className="small">{x.status ?? `precision ${pct(x.precision)}, recall ${pct(x.recall)}, span verification ${pct(x.span_verification_rate)}`}</td></tr>
                ))}
              </tbody></table>
            </div>
            <div className="card">
              <div className="card-title">E2 · Cartographer on unseen papers</div>
              {(r.E2?.rows ?? []).length ? (
                <table className="t"><tbody>
                  {r.E2.rows.map((x: any) => (
                    <tr key={x.paper}><td>{x.paper}</td><td className="small">{x.first_attempt_ok ? "validated on the first attempt" : x.validated ? `validated after ${x.attempts} attempts` : "needs a human edit"}, {x.human_edits} edit(s)</td></tr>
                  ))}
                </tbody></table>
              ) : <p className="muted">No unseen paper has been mapped yet.</p>}
            </div>
          </div>
          <div className="card">
            <div className="card-title">E3 · Attribution recovery</div>
            <p style={{ marginTop: 0 }}>Top-1 accuracy on planted faults {pct(r.E3?.top1_accuracy)}; false-positive rate on clean controls {pct(r.E3?.false_positive_rate)}.</p>
            <table className="t">
              <thead><tr><th>Variant</th><th>Kind</th><th>Result</th><th>Share against single-fault effect</th></tr></thead>
              <tbody>
                {(r.E3?.rows ?? []).map((x: any) => (
                  <tr key={x.variant}>
                    <td className="mono small">{x.variant}</td><td>{x.kind}{x.fault ? `: ${x.fault}` : ""}</td>
                    <td className="small">{!x.analysed ? "not analysed" : x.kind === "clean" ? (x.false_positive ? "false positive" : "no fault reported") : x.kind === "historical" ? Object.entries(x.gap_detected ?? {}).map(([c, g]) => `${c} ${g ? "gap detected" : "no gap"}; ${(x.attributed_to?.[c] ?? []).join(", ") || "unexplained"}`).join(" / ") : x.top1_correct ? "ranked first" : x.detected ? "detected, not first" : "missed"}</td>
                    <td className="small tnum">{(x.share_errors ?? []).map((e: any) => `${e.claim}: Shapley ${fmt(e.shapley)} vs ${fmt(e.single_fault_effect)} (error ${fmt(e.abs_error)})`).join("; ") || "n/a"}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <div className="grid two">
            <div className="card">
              <div className="card-title">E4 · Selection-signal calibration</div>
              <p className="small ink2" style={{ marginTop: 0 }}>Pool of {r.E4?.pool_size ?? "n/a"} seeds. Reported values drawn as the best of k, estimated from 20 fresh seeds.</p>
              <table className="t">
                <thead><tr><th>k</th><th className="num">Lower bound holds</th><th className="num">Signal silent</th></tr></thead>
                <tbody>{(r.E4?.rows ?? []).map((x: any) => <tr key={x.k}><td>{x.k}</td><td className="num">{pct(x.lower_bound_coverage, 1)}</td><td className="num">{pct(x.silent_rate, 1)}</td></tr>)}</tbody>
              </table>
            </div>
            <div className="card">
              <div className="card-title">E5 · Single-run fragility</div>
              {r.E5 ? (
                <>
                  <p className="small ink2" style={{ marginTop: 0 }}>Pool of {r.E5.pool_size} runs, mean {fmt(r.E5.pool_mean)}, sd {fmt(r.E5.pool_std)}. A single run counts as reproduced within ±{r.E5.tolerance} points.</p>
                  <table className="t">
                    <thead><tr><th>Reference</th><th className="num">Pairs that disagree</th><th className="num">Single run passes</th><th>Band verdict</th></tr></thead>
                    <tbody>{r.E5.rows.map((x: any) => <tr key={x.reference}><td>{x.reference} ({fmt(x.value)})</td><td className="num">{pct(x.flip_rate, 1)}</td><td className="num">{pct(x.pass_rate, 1)}</td><td className="small">{x.band_verdict.toLowerCase().replace("_", " ")}</td></tr>)}</tbody>
                  </table>
                </>
              ) : <p className="muted">Not measured.</p>}
            </div>
          </div>
          <div className="grid two">
            <div className="card">
              <div className="card-title">E6 · Cost</div>
              <table className="t">
                <thead><tr><th>Analysis</th><th className="num">Executed</th><th className="num">Cache hits</th><th className="num">Re-scores</th><th className="num">Wall</th></tr></thead>
                <tbody>{(r.E6?.per_analysis ?? []).map((x: any) => <tr key={x.paper}><td className="small">{x.paper}</td><td className="num">{x.runs_executed}</td><td className="num">{x.cache_hits}</td><td className="num">{x.rescores}</td><td className="num">{Math.round(x.wall_seconds)} s</td></tr>)}</tbody>
              </table>
            </div>
            <div className="card">
              <div className="card-title">E7 · Witness fidelity</div>
              <p style={{ marginTop: 0 }}>Re-scoring captured predictions with the witnessed definition agrees with the printed metric in {pct(r.E7?.agreement_rate, 1)} of {(r.E7?.rows ?? []).length} checks.</p>
            </div>
          </div>
          <p className="muted small">Generated {r.generated}.</p>
        </div>
      )}
    </div>
  );
}
