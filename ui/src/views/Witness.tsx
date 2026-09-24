import { useState } from "react";
import { api, pdfPage, type Span } from "../api";
import { Empty, PageHead, PaperPicker, fmt, useApp, useAsync } from "../ui";

type Row = { param: string; paper: unknown; span: Span; observed: unknown; where: string | null; via: string | null; status: string };

const STATUS: Record<string, string> = { match: "v-good", mismatch: "v-critical", "not observed": "v-neutral", "not comparable": "v-warning" };
const show = (v: unknown) => (v === null || v === undefined ? "n/a" : typeof v === "number" ? String(v) : String(v));

export default function Witness() {
  const { paperId, analysisVersion, go } = useApp();
  const { data: a } = useAsync(() => (paperId ? api.analysis(paperId).catch(() => null) : Promise.resolve(null)), [paperId, analysisVersion]);
  const { data: svo } = useAsync(() => (paperId ? fetch(`/api/papers/${encodeURIComponent(paperId)}/stated_vs_observed`).then((r) => r.json() as Promise<{ config: Record<string, unknown>; run_id: string; rows: Row[] }[]>) : Promise.resolve([])), [paperId, analysisVersion]);
  const [peek, setPeek] = useState<Span | null>(null);

  if (!a || !a.witness?.length) {
    return (
      <div>
        <PageHead title="Runtime Witness" right={<PaperPicker />} />
        <Empty title="No observations yet" action={<button className="btn primary" onClick={() => go("runs")}>Go to runs</button>}>
          The Witness records what the code does as it runs. Analyse this paper to collect observations.
        </Empty>
      </div>
    );
  }
  const det = a.determinism;

  return (
    <div>
      <PageHead
        title="Runtime Witness"
        sub="What the paper states beside what the code did when it ran. Each observation carries the file and line that made the call; nothing here is inferred from reading the source."
        right={<PaperPicker />}
      />
      <div style={{ display: "grid", gap: 16 }}>
        {(svo ?? []).map((block, bi) => (
          <div key={bi} className="card" style={{ padding: 6 }}>
            <div className="card-title" style={{ padding: "12px 14px 0" }}>
              <span>Stated against observed</span>
              <span className="muted small mono">{Object.keys(block.config).length ? JSON.stringify(block.config) : "repository defaults"} · run {block.run_id}</span>
            </div>
            <div className="table-wrap">
              <table className="t">
                <thead><tr><th>Parameter</th><th>Paper states</th><th>Code did</th><th>Observed at</th><th>Status</th></tr></thead>
                <tbody>
                  {block.rows.map((r) => (
                    <tr key={r.param}>
                      <td className="mono">{r.param}</td>
                      <td><button className="btn ghost small" style={{ padding: "2px 8px" }} onClick={() => setPeek(r.span)} title="Show where the paper states it">{show(r.paper)} <span className="muted">p.{r.span.page}</span></button></td>
                      <td className="mono">{show(r.observed)}</td>
                      <td className="small"><span className="mono">{r.where ?? ""}</span> <span className="muted">{r.via ?? ""}</span></td>
                      <td><span className={`tag ${STATUS[r.status] ?? ""}`}><span className="dot" />{r.status}</span></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        ))}

        {peek && (
          <div className="card fade-in">
            <div className="card-title"><span>Paper, page {peek.page}</span><button className="btn small" onClick={() => setPeek(null)}>Close</button></div>
            <div className="pdf-frame" style={{ maxHeight: 420 }}>
              <img src={pdfPage(paperId!, peek, 2)} alt="Paper page with the stated value highlighted"
                onLoad={(e) => { const img = e.currentTarget; const s = img.clientWidth / (img.naturalWidth / 2); img.parentElement!.scrollTo({ top: Math.max(0, peek.bbox[1] * s - 140), behavior: "smooth" }); }} />
            </div>
          </div>
        )}

        <div className="grid two">
          <div className="card">
            <div className="card-title">Determinism audit</div>
            {det ? (
              <>
                <p style={{ marginTop: 0 }}>
                  {det.deterministic ? "Two runs with the same seed agree exactly." : `Two runs with the same seed differ by ${fmt(det.delta, 4)}.`}{" "}
                  {det.hash_sensitive ? `Changing only PYTHONHASHSEED moves the metric by ${fmt(det.hash_delta, 4)}.` : det.deterministic ? "Changing only PYTHONHASHSEED leaves it unchanged." : ""}
                </p>
                <p className="ink2 small">
                  {det.deterministic ? "Coalitions share one seed set, so their differences are paired and three seeds each suffice." : "Seeds per coalition rise from three to five and the bootstrap is unpaired."}
                </p>
                {det.candidates.map((c, i) => <div key={i} className="banner" style={{ marginTop: 8 }}>Candidate cause: {c}</div>)}
              </>
            ) : <p className="muted">Not run.</p>}
          </div>
          <div className="card">
            <div className="card-title">Seed calls</div>
            {a.witness[0].summary.seed_calls.length ? (
              <table className="t"><tbody>
                {a.witness[0].summary.seed_calls.map((s) => (
                  <tr key={s.idx}><td className="mono">{s.name}({String(s.seed)})</td><td className="mono small muted">{s.caller}</td></tr>
                ))}
              </tbody></table>
            ) : <p className="ink2">No seed function was called during the run{det?.seed_arg_parsed ? ", although a seed argument was parsed" : ""}.</p>}
          </div>
        </div>

        {a.witness.map((w, i) => (
          <div key={i} className="card">
            <div className="card-title"><span>Metric provenance</span><span className="muted small mono">{Object.keys(w.config).length ? JSON.stringify(w.config) : "repository defaults"}</span></div>
            <div className="table-wrap">
              <table className="t">
                <thead><tr><th>Function</th><th>Keywords</th><th>Call site</th><th className="num">Calls</th><th className="num">Last value</th><th>Bound to</th></tr></thead>
                <tbody>
                  {w.summary.metric_sites.map((m) => {
                    const bound = Object.entries(w.metric_sources).filter(([, src]) => src === `witness:${m.caller}`).map(([k]) => k);
                    return (
                      <tr key={`${m.name}${m.caller}`}>
                        <td className="mono">{m.name}</td>
                        <td className="mono small">{Object.entries(m.kwargs).filter(([k]) => k !== "repository_function").map(([k, v]) => `${k}=${String(v)}`).join(", ") || <span className="muted">defaults</span>}</td>
                        <td className="mono small">{m.caller}</td>
                        <td className="num">{m.count}</td>
                        <td className="num">{fmt(m.value, 4)}</td>
                        <td>{bound.length ? bound.map((b) => <span key={b} className="tag">{b}</span>) : <span className="muted small">per-epoch or validation</span>}</td>
                      </tr>
                    );
                  })}
                </tbody>
              </table>
            </div>
            <div className="row small" style={{ marginTop: 10, gap: 18 }}>
              {Object.entries(w.self_check).map(([k, c]) => (
                <span key={k} className={`tag ${c.ok ? "v-good" : "v-critical"}`}><span className="dot" />self-check {k}: {c.ok ? `re-scoring with ${c.fn} reproduces ${fmt(c.witnessed, 6)}` : c.error ?? "does not reproduce the witnessed value; eval re-scoring disabled"}</span>
              ))}
            </div>
            {w.summary.optimizers.length > 0 && (
              <p className="small ink2" style={{ marginBottom: 0 }}>
                {w.summary.optimizers.map((o) => `${o.name} constructed at ${o.caller} with lr ${String(o.lr)}, weight decay ${String(o.weight_decay)}, ${o.param_groups.length} parameter group(s)`).join("; ")}.
              </p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
