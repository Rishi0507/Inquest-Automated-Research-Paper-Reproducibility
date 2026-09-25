import { api } from "../api";
import { Empty, PageHead, VerdictChip, fmt, useApp, useAsync } from "../ui";

export default function Ledger() {
  const { analysisVersion, go, setPaperId, openClaim } = useApp();
  const { data } = useAsync(() => api.consensus(), [analysisVersion]);
  return (
    <div>
      <PageHead
        title="Claim ledger"
        sub="Claims from different papers that share a model, dataset and metric, each reported value beside the value this platform executed and its measured band. Spread larger than three band widths is flagged. Scope is the analysed corpus."
      />
      {!data ? null : !data.length ? (
        <Empty title="No shared claims yet">A group appears when two papers report the same model, dataset and metric.</Empty>
      ) : (
        <div style={{ display: "grid", gap: 16 }}>
          {data.map((g) => (
            <div key={`${g.model}${g.dataset}${g.metric}`} className="card" style={{ padding: 6 }}>
              <div className="card-title" style={{ padding: "12px 14px 0" }}>
                <span>{g.model} · {g.dataset} · {g.metric}{g.protocol ? ` · ${g.protocol}` : ""}</span>
                <span className="row small">
                  <span className="muted">spread {fmt(g.spread)} pts{g.band_width ? `, ${fmt(g.ratio, 1)}x the narrowest band` : ""}</span>
                  {g.flag && <span className="tag v-serious"><span className="dot" />inconsistent</span>}
                </span>
              </div>
              <table className="t">
                <thead><tr><th>Paper</th><th>Claim</th><th className="num">Reported</th><th className="num">Executed mean</th><th>Band</th><th>Verdict</th></tr></thead>
                <tbody>
                  {g.entries.map((e) => (
                    <tr key={`${e.paper_id}${e.claim_id}`} className="clickable" onClick={() => { setPaperId(e.paper_id); go("claims", e.paper_id); window.setTimeout(() => openClaim(e.claim_id), 350); }}>
                      <td>{e.paper_title}</td>
                      <td className="mono muted">{e.claim_id}</td>
                      <td className="num">{e.value_text}{e.n_runs ? <span className="muted small"> (mean of {e.n_runs})</span> : null}</td>
                      <td className="num">{e.executed_mean !== null ? fmt(e.executed_mean) : <span className="muted">not executed</span>}</td>
                      <td className="tnum small">{e.band ? `[${fmt(e.band[0])}, ${fmt(e.band[1])}]` : "n/a"}</td>
                      <td><VerdictChip verdict={e.verdict} /></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
