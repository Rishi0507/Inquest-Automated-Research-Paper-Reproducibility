import { useEffect, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { api } from "../api";
import Histogram from "../components/Histogram";
import Waterfall from "../components/Waterfall";
import { Empty, PageHead, PaperPicker, VerdictChip, fmt, pct, signed, useApp, useAsync } from "../ui";

export default function AnalysisView() {
  const { paperId, analysisVersion, openClaim, go } = useApp();
  const { data: a, error } = useAsync(() => (paperId ? api.analysis(paperId) : Promise.reject(new Error("no paper"))), [paperId, analysisVersion]);
  const measured = a ? Object.entries(a.claims).filter(([, i]) => i.values?.length) : [];
  const [cid, setCid] = useState<string | null>(null);
  useEffect(() => {
    if (!a) return;
    const withAtt = measured.find(([, i]) => i.attribution?.shares?.length)?.[0];
    setCid((cur) => (cur && a.claims[cur]?.values?.length ? cur : withAtt ?? measured[0]?.[0] ?? null));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [a]);

  if (error || !a) {
    return (
      <div>
        <PageHead title="Analysis" right={<PaperPicker />} />
        <Empty title="No analysis yet" action={<button className="btn primary" onClick={() => go("runs")}>Go to runs</button>}>
          {error && error !== "no paper" ? "This paper has not been analysed. Start an analysis from the Runs tab." : null}
        </Empty>
      </div>
    );
  }
  const info = cid ? a.claims[cid] : null;
  const att = info?.attribution;
  const sel = info?.selection_aligned ?? info?.selection;

  return (
    <div>
      <PageHead
        title="Analysis"
        sub="Each claim is judged against two error bars: what this code can produce across seeds, and what the paper's text permits across the parameters it leaves unstated."
        right={<PaperPicker />}
      />
      {!measured.length ? (
        <Empty title="No executed claims">None of this paper's claims could be run. The verdicts on the Claims tab say why.</Empty>
      ) : (
        <>
          <div className="row" style={{ marginBottom: 16 }}>
            <div className="seg" role="group" aria-label="Claim">
              {measured.map(([id, i]) => (
                <button key={id} aria-pressed={cid === id} onClick={() => setCid(id)}>{id} {i.claim.dataset} {i.metric_key}</button>
              ))}
            </div>
          </div>
          <AnimatePresence mode="wait">
            {info && (
              <motion.div key={cid} initial={{ opacity: 0, y: 6 }} animate={{ opacity: 1, y: 0 }} exit={{ opacity: 0 }} transition={{ duration: 0.28 }}
                style={{ display: "grid", gap: 16 }}>
                <div className="card">
                  <div className="card-title">
                    <span>{info.claim.text}</span>
                    <span className="row"><VerdictChip verdict={info.verdict?.verdict} /><button className="btn small" onClick={() => openClaim(cid)}>Evidence</button></span>
                  </div>
                  <Histogram values={info.values ?? []} reported={info.claim.value} seedBand={info.seed_band}
                    specBand={info.spec?.spec_band ?? null} alignedBand={info.aligned_band ?? null} metric={info.metric_key} />
                  <div className="grid three" style={{ marginTop: 18 }}>
                    <div>
                      <div className="muted small">Seed band, 95% prediction interval</div>
                      <div className="tnum" style={{ fontSize: 18 }}>[{fmt(info.seed_band?.lo)}, {fmt(info.seed_band?.hi)}]</div>
                      <div className="muted small">mean {fmt(info.seed_band?.mean)}, sd {fmt(info.seed_band?.std)}, n={info.seed_band?.n}, m={info.seed_band?.m}</div>
                    </div>
                    <div>
                      <div className="muted small">Specification band</div>
                      <div className="tnum" style={{ fontSize: 18 }}>{info.spec ? `[${fmt(info.spec.spec_band[0])}, ${fmt(info.spec.spec_band[1])}]` : "not swept"}</div>
                      <div className="muted small">{info.spec ? `${info.spec.swept.length} unstated parameter(s) swept` : "no unstated parameter with a verified patch"}</div>
                    </div>
                    <div>
                      <div className="muted small">Selection signal ({sel?.distribution ?? "baseline"})</div>
                      <div className="tnum" style={{ fontSize: 18 }}>{sel ? `at least ${fmt(sel.k_lower, 1)} attempts` : "n/a"}</div>
                      <div className="muted small">{sel ? `${sel.exceed} of ${sel.n} runs reach ${info.claim.value}` : ""}</div>
                    </div>
                  </div>
                  {sel && sel.k_lower >= 10 && (
                    <div className="banner" style={{ marginTop: 14 }}>
                      Inconsistency signal, not an accusation: reaching {info.claim.value} with 50% probability would take at least {fmt(sel.k_lower, 1)} undisclosed attempts, assuming the authors' seed distribution matches this reproduction.
                    </div>
                  )}
                  {info.dispersion_check && (
                    <p className="muted small" style={{ marginTop: 12 }}>
                      The paper prints ± {info.dispersion_check.reported} ({info.dispersion_check.kind}), which implies a run-to-run deviation of {fmt(info.dispersion_check.implied_std, 3)}. This code measures {fmt(info.dispersion_check.measured_std, 3)}, a ratio of {fmt(info.dispersion_check.ratio, 1)} ({info.dispersion_check.status}).
                    </p>
                  )}
                </div>

                {att ? (
                  <div className="card">
                    <div className="card-title">
                      <span>Measured gap attribution</span>
                      <span className="muted small">gap {signed(att.gap)} pts · τ {fmt(att.tau)} · k={att.k} {att.paired ? "paired" : "unpaired"} seeds · {Object.keys(att.coalitions).length} coalitions evaluated</span>
                    </div>
                    {att.shares.length ? <Waterfall att={att} /> : <p className="ink2">No deviation survived screening against the noise floor.</p>}
                    {!att.shares_valid && <p className="muted small">The gap is within 2τ, so shares are shown in points only.</p>}
                    {att.pruned_joint_effect && <div className="banner warn" style={{ marginTop: 12 }}>Deviations pruned by screening matter jointly: aligning all of them moves the metric by more than τ beyond the survivors.</div>}
                    <div className="hr" />
                    <div className="card-title">Screening</div>
                    <div className="table-wrap">
                      <table className="t">
                        <thead><tr><th>Deviation</th><th className="num">First-order</th><th className="num">Total effect</th><th className="num">Noise floor</th><th>Outcome</th></tr></thead>
                        <tbody>
                          {Object.entries(att.effects).map(([id, e]) => {
                            const dev = a.deviations.find((d) => d.dev_id === id);
                            return (
                              <tr key={id}>
                                <td><span className="mono muted">{id}</span> {dev?.label ?? dev?.param}</td>
                                <td className="num">{fmt(e.first_order)}</td>
                                <td className="num">{fmt(e.total)}</td>
                                <td className="num">{fmt(e.floor ?? att.tau)}{dev?.phase === "eval" ? <span className="muted small"> paired</span> : null}</td>
                                <td>{att.survivors.includes(id) ? "survives, enters exact Shapley" : e.survives ? "survives, over the enumeration cap" : "within the noise floor"}</td>
                              </tr>
                            );
                          })}
                          {att.excluded.map((x) => (
                            <tr key={x.dev_id}><td><span className="mono muted">{x.dev_id}</span> {x.label}</td><td className="num muted">n/a</td><td className="num muted">n/a</td><td className="num muted">n/a</td><td className="muted">excluded: {x.reason}</td></tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </div>
                ) : (
                  <div className="card"><div className="card-title">Attribution</div>
                    <p className="ink2" style={{ margin: 0 }}>
                      {info.seed_band && info.claim.value >= info.seed_band.lo && info.claim.value <= info.seed_band.hi
                        ? "The reported value lies inside the seed band, so there is no gap to attribute."
                        : info.attribution_note ?? "No attribution for this claim."}
                    </p>
                  </div>
                )}

                {info.spec && (
                  <div className="card">
                    <div className="card-title">Cost of what the paper leaves unstated</div>
                    <div className="grid two">
                      <table className="t">
                        <thead><tr><th>Configuration</th><th className="num">Mean</th><th>Band</th></tr></thead>
                        <tbody>
                          {info.spec.configs.map((c, i) => (
                            <tr key={i}>
                              <td>{c.label}</td>
                              <td className="num">{c.mean !== undefined ? fmt(c.mean) : "n/a"}</td>
                              <td className="tnum small">{c.band ? `[${fmt(c.band.lo)}, ${fmt(c.band.hi)}]` : <span className="muted">{c.skipped}</span>}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                      <div>
                        {Object.entries(info.spec.costs).map(([k, v]) => (
                          <div key={k} style={{ marginBottom: 12 }}>
                            <div className="row small"><span>{k}</span><span className="spacer" /><span className="tnum">±{fmt(v)}</span></div>
                            <div style={{ height: 6, background: "var(--surface-3)", borderRadius: 4, overflow: "hidden" }}>
                              <motion.div initial={{ width: 0 }} animate={{ width: `${Math.min(100, (v / Math.max(...Object.values(info.spec!.costs), 0.01)) * 100)}%` }}
                                transition={{ duration: 0.7, ease: [0.22, 1, 0.36, 1] }} style={{ height: "100%", background: "var(--ink-2)" }} />
                            </div>
                          </div>
                        ))}
                        {info.sci && (
                          <p className="small ink2" style={{ marginTop: 16 }}>
                            Specification Completeness Index {fmt(info.sci.plain)}{info.sci.weighted !== null ? `, cost-weighted ${fmt(info.sci.weighted)}` : ""}. Unstated: {info.sci.missing.join(", ") || "none"}.
                          </p>
                        )}
                      </div>
                    </div>
                  </div>
                )}
                <p className="muted small">{a.standing_note} Cost of this analysis: {a.cost.runs_executed} run{a.cost.runs_executed === 1 ? "" : "s"} executed, {a.cost.cache_hits} served from cache, {a.cost.rescores} eval re-score{a.cost.rescores === 1 ? "" : "s"}. Seeds per coalition share their seed set, so {a.determinism?.deterministic ? "coalition differences are paired." : "differences would be paired only for deterministic runs; here they are not."}</p>
                {att && <p className="muted small">Attribution explains only deviations the platform can identify and toggle; unidentified causes appear as residual. Share of gap: {att.shares.map((s) => `${s.label} ${s.fraction !== null ? pct(s.fraction) : signed(s.points)}`).join(", ")}.</p>}
              </motion.div>
            )}
          </AnimatePresence>
        </>
      )}
    </div>
  );
}
