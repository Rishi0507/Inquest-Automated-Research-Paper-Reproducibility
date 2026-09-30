import { useCallback, useEffect, useMemo, useState } from "react";
import { AnimatePresence, motion } from "motion/react";
import { api, type PaperSummary, type Status } from "./api";
import { AppCtx, GROUPS, TABS, groupOf, type GroupId, type Tab } from "./ui";
import Overview from "./views/Overview";
import Papers from "./views/Papers";
import Claims from "./views/Claims";
import Runs from "./views/Runs";
import AnalysisView from "./views/Analysis";
import Witness from "./views/Witness";
import Ledger from "./views/Ledger";
import Controls from "./views/Controls";
import Evaluation from "./views/Evaluation";
import Report from "./views/Report";
import EvidenceDrawer from "./components/EvidenceDrawer";

function readHash(): { tab: Tab; paper: string | null; claim: string | null } {
  const [path, query] = window.location.hash.replace(/^#\/?/, "").split("?");
  const params = new URLSearchParams(query ?? "");
  const tab = (TABS.find((t) => t.id === path)?.id ?? "overview") as Tab;
  return { tab, paper: params.get("paper"), claim: params.get("claim") };
}

function writeHash(tab: Tab, paper: string | null, claim: string | null) {
  const q = new URLSearchParams();
  if (paper) q.set("paper", paper);
  if (claim) q.set("claim", claim);
  const next = `#/${tab}${q.toString() ? `?${q}` : ""}`;
  if (window.location.hash !== next) window.history.pushState(null, "", next);
}

function Mark() {
  return (
    <svg viewBox="0 0 32 32" aria-hidden>
      <rect width="32" height="32" rx="8" fill="var(--ink)" />
      <path d="M9 21.5h14M11 17l3.5-5 3 3.2L21 10" fill="none" stroke="var(--bg)" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  );
}

function ThemeToggle() {
  const [theme, setTheme] = useState<string | null>(() => {
    try { return localStorage.getItem("inquest-theme"); } catch { return null; }
  });
  useEffect(() => {
    if (theme) document.documentElement.setAttribute("data-theme", theme);
    else document.documentElement.removeAttribute("data-theme");
    try { theme ? localStorage.setItem("inquest-theme", theme) : localStorage.removeItem("inquest-theme"); } catch { /* private mode */ }
  }, [theme]);
  const dark = theme ? theme === "dark" : window.matchMedia("(prefers-color-scheme: dark)").matches;
  return (
    <button className="icon-btn" aria-label="Toggle theme" title="Toggle theme" onClick={() => setTheme(dark ? "light" : "dark")}>
      {dark ? (
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><circle cx="12" cy="12" r="4.2" /><path d="M12 2.5v2.2M12 19.3v2.2M4.6 4.6l1.6 1.6M17.8 17.8l1.6 1.6M2.5 12h2.2M19.3 12h2.2M4.6 19.4l1.6-1.6M17.8 6.2l1.6-1.6" strokeLinecap="round" /></svg>
      ) : (
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"><path d="M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5Z" strokeLinejoin="round" /></svg>
      )}
    </button>
  );
}

export default function App() {
  const initial = readHash();
  const [tab, setTab] = useState<Tab>(initial.tab);
  const [paperId, setPaper] = useState<string | null>(initial.paper);
  const [claimId, setClaimId] = useState<string | null>(initial.claim);
  const [papers, setPapers] = useState<PaperSummary[]>([]);
  const [status, setStatus] = useState<Status | null>(null);
  const [analysisVersion, setAnalysisVersion] = useState(0);

  const refreshPapers = useCallback(() => {
    api.papers().then((ps) => {
      setPapers((prev) => {
        const before = JSON.stringify(prev.map((p) => p.analysis?.analysis_id));
        const after = JSON.stringify(ps.map((p) => p.analysis?.analysis_id));
        if (before !== after) setAnalysisVersion((v) => v + 1);
        return ps;
      });
      setPaper((cur) => cur ?? ps.find((p) => p.role === "hero" || p.role === "curated")?.paper_id ?? ps[0]?.paper_id ?? null);
    }).catch(() => undefined);
    api.status().then(setStatus).catch(() => undefined);
  }, []);

  useEffect(() => {
    refreshPapers();
    const t = window.setInterval(refreshPapers, 5000);
    return () => window.clearInterval(t);
  }, [refreshPapers]);

  useEffect(() => {
    const onPop = () => {
      const h = readHash();
      setTab(h.tab);
      if (h.paper) setPaper(h.paper);
      setClaimId(h.claim);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  useEffect(() => { writeHash(tab, paperId, claimId); }, [tab, paperId, claimId]);

  useEffect(() => {
    document.querySelector('.chip-tab[aria-selected="true"]')?.scrollIntoView({ inline: "center", block: "nearest", behavior: "smooth" });
  }, [tab]);

  const ctx = useMemo(() => ({
    tab,
    go: (t: Tab, p?: string | null) => {
      if (p) setPaper(p);
      setClaimId(null);
      setTab(t);
      window.scrollTo({ top: 0, behavior: "smooth" });
    },
    paperId,
    setPaperId: (id: string) => { setPaper(id); setClaimId(null); },
    papers, refreshPapers, status,
    openClaim: (cid: string | null) => setClaimId(cid),
    claimId, analysisVersion,
  }), [tab, paperId, papers, refreshPapers, status, claimId, analysisVersion]);

  const View = {
    overview: Overview, papers: Papers, claims: Claims, runs: Runs, analysis: AnalysisView, witness: Witness,
    ledger: Ledger, controls: Controls, evaluation: Evaluation, report: Report,
  }[tab];

  const running = papers.some((p) => p.active_job);
  const group = groupOf(tab);
  const [lastView, setLastView] = useState<Partial<Record<GroupId, Tab>>>({});
  useEffect(() => { setLastView((m) => ({ ...m, [group.id]: tab })); }, [tab, group.id]);

  return (
    <AppCtx.Provider value={ctx}>
      <header className="topbar">
        <div className="topbar-inner">
          <div className="wordmark" onClick={() => ctx.go("overview")}><Mark />Inquest</div>
          <nav className="chips" role="tablist" aria-label="Sections">
            {GROUPS.map((g) => (
              <button key={g.id} role="tab" className="chip-tab" aria-selected={group.id === g.id}
                onClick={() => ctx.go(lastView[g.id] ?? g.views[0].id)}>
                {group.id === g.id && <motion.i className="pill" layoutId="chip-pill" transition={{ type: "spring", stiffness: 420, damping: 36 }} />}
                <span>{g.label}{g.id === "analysis" && running ? <span className="status-dot" style={{ background: "var(--accent)", marginLeft: 7, verticalAlign: 1 }} /> : null}</span>
              </button>
            ))}
          </nav>
          <div className="topbar-right"><ThemeToggle /></div>
        </div>
      </header>
      {group.views.length > 1 && (
        <div className="subnav-wrap">
          <nav className="subnav" role="tablist" aria-label={`${group.label} views`}>
            {group.views.map((v) => (
              <button key={v.id} role="tab" className="subnav-tab" aria-selected={tab === v.id} onClick={() => ctx.go(v.id)}>
                {tab === v.id && <motion.i className="subnav-line" layoutId={`subnav-${group.id}`} transition={{ type: "spring", stiffness: 480, damping: 38 }} />}
                <span>{v.label}{v.id === "runs" && running ? <span className="status-dot" style={{ background: "var(--accent)", marginLeft: 7, verticalAlign: 1 }} /> : null}</span>
              </button>
            ))}
          </nav>
        </div>
      )}
      <AnimatePresence mode="wait" initial={false}>
        <motion.main
          key={tab}
          className="page"
          initial={{ opacity: 0, y: 10 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -6 }}
          transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
        >
          <View />
        </motion.main>
      </AnimatePresence>
      <EvidenceDrawer />
    </AppCtx.Provider>
  );
}
