import { api } from "../api";
import { Empty, PageHead, PaperPicker, VerdictChip, fmt, useApp, useAsync } from "../ui";

export default function Claims() {
  const { paperId, papers, openClaim, analysisVersion, refreshPapers } = useApp();
  const paper = papers.find((p) => p.paper_id === paperId);
  const { data: claims, reload } = useAsync(() => (paperId ? api.claims(paperId) : Promise.resolve(null)), [paperId, analysisVersion]);
  const { data: analysis } = useAsync(() => (paperId ? api.analysis(paperId).catch(() => null) : Promise.resolve(null)), [paperId, analysisVersion]);

  const setSource = async (s: string) => {
    if (!paperId) return;
    await api.setClaimsSource(paperId, s);
    reload();
    refreshPapers();
  };

  return (
    <div>
      <PageHead
        title="Claims"
        sub="Every numerical claim carries the page and words that print it. Select a row to open the evidence: the PDF location, the code that produces the number, and the run log."
        right={<PaperPicker />}
      />
      {paper?.parent && <div className="banner" style={{ marginBottom: 16 }}>This is a disclosed control. Claim values are the unmodified repository's measured means, so any gap comes from the control itself.</div>}
      {paper && paper.has_hand_claims && (
        <div className="row" style={{ marginBottom: 14 }}>
          <div className="seg" role="group" aria-label="Claim source">
            {(["hand", "extracted"] as const).map((s) => (
              <button key={s} aria-pressed={claims?.source === s} onClick={() => setSource(s)}>{s === "hand" ? "Hand-authored ground truth" : "Extracted by the model"}</button>
            ))}
          </div>
          {claims?.extraction_report && (
            <span className="muted small">
              extraction kept {String(claims.extraction_report.kept_claims)} of {String(claims.extraction_report.proposed_claims)} proposed claims after span verification
            </span>
          )}
        </div>
      )}
      {!claims ? null : !claims.claims.length ? (
        <Empty title="No claims yet">
          {claims.source === "extracted" ? "Run extraction from the Papers tab. Only values found among the words of their cited page are kept." : "This paper has no claims."}
        </Empty>
      ) : (
        <div className="card" style={{ padding: 6 }}>
          <div className="table-wrap">
            <table className="t">
              <thead>
                <tr><th>ID</th><th>Claim</th><th className="num">Reported</th><th>Seed band</th><th>Mapping</th><th className="num">SCI</th><th>Verdict</th></tr>
              </thead>
              <tbody>
                {claims.claims.map((c) => {
                  const info = analysis?.claims[c.claim_id];
                  const v = info?.verdict;
                  const b = info?.seed_band;
                  const sci = info?.sci;
                  return (
                    <tr key={c.claim_id} className="clickable" onClick={() => openClaim(c.claim_id)}>
                      <td className="mono muted">{c.claim_id}</td>
                      <td>
                        <div>{c.text}</div>
                        <div className="muted small">p. {c.source.page}{c.source.table ? ` · ${c.source.table}` : ""} · {c.dataset} · {c.metric}{c.n_seeds_reported ? ` · mean of ${c.n_seeds_reported} runs` : ""}</div>
                      </td>
                      <td className="num">{c.value_text}{c.dispersion ? <span className="muted"> ± {c.dispersion}</span> : null}</td>
                      <td className="tnum small">{b ? `[${fmt(b.lo)}, ${fmt(b.hi)}]` : <span className="muted">n/a</span>}</td>
                      <td className="small">{info?.mapping?.status ? info.mapping.status.replace("_", " ") : <span className="muted">pending</span>}</td>
                      <td className="num small">{sci ? fmt(sci.weighted ?? sci.plain) : "n/a"}</td>
                      <td><VerdictChip verdict={v?.verdict} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      )}
      <p className="muted small" style={{ marginTop: 14 }}>
        SCI is the Specification Completeness Index: the share of the parameters a claim requires that the paper states, weighted by measured sensitivity when a specification sweep has run.
      </p>
    </div>
  );
}
