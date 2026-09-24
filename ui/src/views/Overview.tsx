import { api, type Analysis, type PaperSummary } from "../api";
import { Reveal, Stat, VerdictChip, fmt, pct, signed, useApp, useAsync } from "../ui";

const FLOW = [
  { n: "01", name: "Read", desc: "Claims and stated parameters extracted, each tied to the words that print it." },
  { n: "02", name: "Map", desc: "Each claim mapped to a command, validated by running it." },
  { n: "03", name: "Observe", desc: "The Witness records arguments, seeds, optimizers and metric calls as the code runs." },
  { n: "04", name: "Measure", desc: "Seeds run in parallel; a prediction interval says what this code produces." },
  { n: "05", name: "Attribute", desc: "Differences are removed one by one and priced with exact Shapley values." },
  { n: "06", name: "Judge", desc: "A fixed rule order issues the verdict. No language model decides it." },
];

function useHeroNumbers(papers: PaperSummary[]) {
  const withAnalysis = papers.filter((p) => p.analysis && !p.parent);
  return useAsync(async () => {
    const out: { paper: PaperSummary; analysis: Analysis }[] = [];
    for (const p of withAnalysis) {
      try { out.push({ paper: p, analysis: await api.analysis(p.paper_id) }); } catch { /* not analysed */ }
    }
    return out;
  }, [withAnalysis.map((p) => p.analysis?.analysis_id).join(",")]);
}

export default function Overview() {
  const { papers, go, status } = useApp();
  const { data } = useHeroNumbers(papers);
  const curated = papers.filter((p) => !p.parent);
  const verdicts = curated.flatMap((p) => Object.values(p.analysis?.verdicts ?? {}));
  const counters = status?.counters ?? {};

  // The most informative real attribution in the corpus, if one exists.
  let featured: { cid: string; paper: PaperSummary; a: Analysis } | null = null;
  for (const { paper, analysis } of data ?? []) {
    for (const [cid, info] of Object.entries(analysis.claims)) {
      if (info.attribution?.shares?.length && (!featured || Math.abs(info.attribution.gap) > Math.abs(featured.a.claims[featured.cid].attribution!.gap))) {
        featured = { cid, paper, a: analysis };
      }
    }
  }
  const fi = featured ? featured.a.claims[featured.cid] : null;
  const witnessLine = (() => {
    for (const { analysis } of data ?? []) {
      const d = analysis.determinism;
      if (d && !d.deterministic && d.seed_arg_parsed && !d.seed_calls_observed.length) return "seed argument parsed, no seed function ever called";
      if (d?.hash_sensitive) return `result moves ${fmt(d.hash_delta, 1)} points with PYTHONHASHSEED alone`;
    }
    return null;
  })();

  return (
    <div>
      <section className="hero">
        <Reveal>
          <div className="kicker">Forensic reproducibility for machine learning papers</div>
          <h1>Reproduction as a <em>measurement</em>, not an opinion.</h1>
          <p className="lede">
            Inquest runs a paper's own code, watches what it actually does, and prices every difference between the
            code and the text by re-executing it. Language models read. Execution and statistics judge.
          </p>
          <div className="row" style={{ marginTop: 26 }}>
            <button className="btn primary" onClick={() => go("claims")}>Open the claims</button>
            <button className="btn" onClick={() => go("papers")}>Add a paper</button>
          </div>
        </Reveal>
        <Reveal delay={0.08}>
          <div className="card" style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 22 }}>
            <Stat value={curated.length} label="papers in the corpus" />
            <Stat value={verdicts.length} label="claims with a verdict" />
            <Stat value={(counters.runs_executed ?? 0).toLocaleString()} label="sandboxed runs executed" />
            <Stat value={(counters.cache_hits ?? 0).toLocaleString()} label="runs served from the cache" />
          </div>
        </Reveal>
      </section>

      <Reveal delay={0.12}>
        <div className="grid three" style={{ marginTop: 26 }}>
          <div className="card signature" onClick={() => go("analysis", featured?.paper.paper_id)}>
            <span className="n">i.</span>
            <h3>Measured gap attribution</h3>
            <p>Two-sided screening, exact Shapley values over the survivors, and paired bootstrap intervals. Shares sum to the measured gap; what remains is labelled.</p>
            <div className="quote">
              {fi?.attribution ? (
                <>
                  {featured!.paper.title.split(":")[0]}, {featured!.cid}: gap {signed(fi.attribution.gap)} pts
                  {fi.attribution.shares.slice(0, 2).map((s) => (
                    <div key={s.dev_id}>{signed(s.points)} {s.fraction !== null ? pct(s.fraction) : ""} {s.label}</div>
                  ))}
                </>
              ) : "No attribution has run yet."}
            </div>
          </div>
          <div className="card signature" onClick={() => go("witness")}>
            <span className="n">ii.</span>
            <h3>Runtime Witness</h3>
            <p>Hooks inside the sandbox record resolved arguments, seed calls, optimizers and metric calls with the file and line that made them, and keep the predictions for re-scoring.</p>
            <div className="quote">{witnessLine ?? "Observations appear after the first analysis."}</div>
          </div>
          <div className="card signature" onClick={() => go("analysis")}>
            <span className="n">iii.</span>
            <h3>Two-band verdicts</h3>
            <p>A seed band for what the code can produce and a specification band for what the paper's text permits, measured by sweeping the parameters it leaves unstated.</p>
            <div className="quote">
              {fi?.seed_band ? <>seed band [{fmt(fi.seed_band.lo)}, {fmt(fi.seed_band.hi)}], reported {fi.claim.value}</> : "Bands appear after the first analysis."}
            </div>
          </div>
        </div>
      </Reveal>

      <Reveal delay={0.16}>
        <h2 style={{ fontFamily: "var(--serif)", fontWeight: 400, fontSize: 28, margin: "44px 0 14px" }}>How a claim is judged</h2>
        <div className="flow">
          {FLOW.map((f) => (
            <div key={f.n}>
              <div className="step">{f.n}</div>
              <div className="name">{f.name}</div>
              <div className="desc">{f.desc}</div>
            </div>
          ))}
        </div>
      </Reveal>

      <Reveal delay={0.2}>
        <h2 style={{ fontFamily: "var(--serif)", fontWeight: 400, fontSize: 28, margin: "44px 0 14px" }}>Corpus</h2>
        <div className="card" style={{ padding: 6 }}>
          <div className="table-wrap">
            <table className="t">
              <thead><tr><th>Paper</th><th>Repository</th><th>Claims</th><th>Verdicts</th></tr></thead>
              <tbody>
                {curated.map((p) => {
                  const vs = Object.values(p.analysis?.verdicts ?? {});
                  const counts = vs.reduce<Record<string, number>>((acc, v) => ({ ...acc, [v]: (acc[v] ?? 0) + 1 }), {});
                  return (
                    <tr key={p.paper_id} className="clickable" onClick={() => go("claims", p.paper_id)}>
                      <td>
                        <div style={{ fontWeight: 500 }}>{p.title}</div>
                        <div className="muted small">{p.authors} {p.venue ? `· ${p.venue}` : ""}</div>
                      </td>
                      <td className="small">
                        <span className="mono">{p.repo_url?.replace("https://github.com/", "")}</span>
                        <div className="muted">{p.provenance === "third_party" ? "third-party reimplementation" : p.provenance === "author" ? "author code" : p.provenance}</div>
                      </td>
                      <td className="tnum">{p.n_claims}</td>
                      <td>
                        <div className="row" style={{ gap: 6 }}>
                          {Object.entries(counts).map(([v, n]) => <span key={v} className="row" style={{ gap: 4 }}><VerdictChip verdict={v} />{n > 1 && <span className="muted small">{n}</span>}</span>)}
                          {!vs.length && <span className="muted small">not analysed</span>}
                        </div>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
        {status && (
          <p className="muted small" style={{ marginTop: 14 }}>
            {status.standing_note} Sandbox: {status.sandbox}. Language model: {status.llm.anthropic ? status.llm.model : status.llm.local ? status.llm.local_model : "not configured"}.
          </p>
        )}
      </Reveal>
    </div>
  );
}
