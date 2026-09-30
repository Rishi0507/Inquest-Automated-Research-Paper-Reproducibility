import { createContext, useCallback, useContext, useEffect, useRef, useState, type ReactNode } from "react";
import { motion } from "motion/react";
import type { Job, PaperSummary, Status } from "./api";

// ------------------------------------------------------------------ app context

export type Tab = "overview" | "papers" | "claims" | "runs" | "analysis" | "witness" | "ledger" | "controls" | "evaluation" | "report";
export const TABS: { id: Tab; label: string }[] = [
  { id: "overview", label: "Overview" },
  { id: "papers", label: "Papers" },
  { id: "claims", label: "Claims" },
  { id: "runs", label: "Runs" },
  { id: "analysis", label: "Analysis" },
  { id: "witness", label: "Witness" },
  { id: "ledger", label: "Ledger" },
  { id: "controls", label: "Controls" },
  { id: "evaluation", label: "Evaluation" },
  { id: "report", label: "Report" },
];

/** Five top-level sections; each holds one or more views reached through the sub-tab row. */
export type GroupId = "overview" | "papers" | "analysis" | "validation" | "report";
export const GROUPS: { id: GroupId; label: string; views: { id: Tab; label: string }[] }[] = [
  { id: "overview", label: "Overview", views: [{ id: "overview", label: "Overview" }] },
  { id: "papers", label: "Papers", views: [{ id: "claims", label: "Claims" }, { id: "papers", label: "Library" }] },
  { id: "analysis", label: "Analysis", views: [{ id: "analysis", label: "Results" }, { id: "witness", label: "Witness" }, { id: "runs", label: "Runs" }] },
  { id: "validation", label: "Validation", views: [{ id: "controls", label: "Controls" }, { id: "evaluation", label: "Evaluation" }] },
  { id: "report", label: "Report", views: [{ id: "report", label: "Dossier" }, { id: "ledger", label: "Ledger" }] },
];
export const groupOf = (tab: Tab) => GROUPS.find((g) => g.views.some((v) => v.id === tab)) ?? GROUPS[0];

type Ctx = {
  tab: Tab; go: (tab: Tab, paper?: string | null) => void;
  paperId: string | null; setPaperId: (id: string) => void;
  papers: PaperSummary[]; refreshPapers: () => void; status: Status | null;
  openClaim: (cid: string | null) => void; claimId: string | null;
  analysisVersion: number;
};
export const AppCtx = createContext<Ctx>(null as unknown as Ctx);
export const useApp = () => useContext(AppCtx);

// ------------------------------------------------------------------ data hooks

export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const run = useCallback(() => {
    const id = ++seq.current;
    setLoading(true);
    fn().then(
      (d) => { if (id === seq.current) { setData(d); setError(null); setLoading(false); } },
      (e: Error) => { if (id === seq.current) { setError(e.message); setData(null); setLoading(false); } },
    );
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(() => { run(); }, [run]);
  return { data, error, loading, reload: run };
}

export function useJob(jobId: string | null, onDone?: (j: Job) => void) {
  const [job, setJob] = useState<Job | null>(null);
  const done = useRef(onDone);
  done.current = onDone;
  useEffect(() => {
    if (!jobId) { setJob(null); return; }
    let stop = false;
    let timer: number;
    const tick = async () => {
      try {
        const r = await fetch(`/api/jobs/${jobId}`);
        if (r.ok) {
          const j = (await r.json()) as Job;
          if (stop) return;
          setJob(j);
          if (j.status === "done" || j.status === "failed" || j.status === "interrupted") { done.current?.(j); return; }
        }
      } catch { /* retry on the next tick */ }
      if (!stop) timer = window.setTimeout(tick, 1200);
    };
    tick();
    return () => { stop = true; window.clearTimeout(timer); };
  }, [jobId]);
  return job;
}

// ------------------------------------------------------------------ small components

const VERDICTS: Record<string, { label: string; tone: string }> = {
  REPRODUCED: { label: "Reproduced", tone: "good" },
  NOT_REPRODUCED_EXPLAINED: { label: "Not reproduced, explained", tone: "warning" },
  UNDERSPECIFIED: { label: "Underspecified", tone: "warning" },
  NOT_REPRODUCED_PARTIALLY_EXPLAINED: { label: "Partially explained", tone: "serious" },
  NOT_REPRODUCED_UNEXPLAINED: { label: "Not reproduced", tone: "critical" },
  NUMERICALLY_IMPOSSIBLE: { label: "Numerically impossible", tone: "critical" },
  NOT_EXECUTABLE: { label: "Not executable", tone: "serious" },
  NOT_IMPLEMENTED: { label: "Not implemented", tone: "neutral" },
  UNVERIFIABLE: { label: "Unverifiable", tone: "neutral" },
};
export const verdictLabel = (v?: string | null) => (v ? VERDICTS[v]?.label ?? v : "Not analysed");

export function VerdictChip({ verdict }: { verdict?: string | null }) {
  const meta = verdict ? VERDICTS[verdict] : null;
  return (
    <span className={`tag v-${meta?.tone ?? "neutral"}`} title={verdict ?? "not analysed"}>
      <span className="dot" />
      {meta?.label ?? "Not analysed"}
    </span>
  );
}

export function Empty({ title, children, action }: { title: string; children?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty fade-in">
      <h3>{title}</h3>
      {children && <div style={{ maxWidth: 520, margin: "0 auto" }}>{children}</div>}
      {action && <div style={{ marginTop: 18 }}>{action}</div>}
    </div>
  );
}

export function PageHead({ title, sub, right }: { title: ReactNode; sub?: ReactNode; right?: ReactNode }) {
  return (
    <div className="page-head">
      <div>
        <h1 className="page-title">{title}</h1>
        {sub && <p className="page-sub">{sub}</p>}
      </div>
      {right && <div className="row">{right}</div>}
    </div>
  );
}

export function Stat({ value, label }: { value: ReactNode; label: ReactNode }) {
  return (
    <div className="stat">
      <span className="v tnum">{value}</span>
      <span className="l">{label}</span>
    </div>
  );
}

export function Reveal({ children, delay = 0 }: { children: ReactNode; delay?: number }) {
  return (
    <motion.div initial={{ opacity: 0, y: 8 }} animate={{ opacity: 1, y: 0 }} transition={{ duration: 0.45, delay, ease: [0.22, 1, 0.36, 1] }}>
      {children}
    </motion.div>
  );
}

export function PaperPicker({ filter }: { filter?: (p: PaperSummary) => boolean }) {
  const { papers, paperId, setPaperId } = useApp();
  const list = filter ? papers.filter(filter) : papers;
  if (!list.length) return null;
  return (
    <select className="select" value={paperId ?? ""} onChange={(e) => setPaperId(e.target.value)} aria-label="Paper">
      {list.map((p) => (
        <option key={p.paper_id} value={p.paper_id}>{p.parent ? `${p.title.replace(/\s*\[.*\]$/, "")} (control: ${p.paper_id.split("~")[1]})` : p.title}</option>
      ))}
    </select>
  );
}

export const fmt = (x: number | null | undefined, d = 2) => (x === null || x === undefined || Number.isNaN(x) ? "n/a" : x.toFixed(d));
export const signed = (x: number, d = 2) => `${x >= 0 ? "+" : "−"}${Math.abs(x).toFixed(d)}`;
export const pct = (x: number | null | undefined, d = 0) => (x === null || x === undefined ? "n/a" : `${(x * 100).toFixed(d)}%`);
export const ago = (t: number) => {
  const s = Math.max(0, Date.now() / 1000 - t);
  if (s < 60) return "just now";
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return new Date(t * 1000).toLocaleDateString();
};

export function Kv({ rows }: { rows: [ReactNode, ReactNode][] }) {
  return (
    <dl className="kv">
      {rows.map(([k, v], i) => (
        <div key={i} style={{ display: "contents" }}>
          <dt>{k}</dt>
          <dd>{v}</dd>
        </div>
      ))}
    </dl>
  );
}

export function useTooltip() {
  const [tip, setTip] = useState<{ x: number; y: number; text: ReactNode } | null>(null);
  const node = tip ? (
    <div className="tooltip" style={{ left: tip.x + 14, top: tip.y + 12 }}>{tip.text}</div>
  ) : null;
  return { show: (e: React.MouseEvent, text: ReactNode) => setTip({ x: e.clientX, y: e.clientY, text }), hide: () => setTip(null), node };
}

/** A scalar as a person would write it: short numbers kept, long floats to six significant digits. */
export const scalar = (v: unknown): string => {
  if (v === null || v === undefined) return "n/a";
  if (typeof v === "number") return Number.isInteger(v) ? String(v) : String(Number(v.toPrecision(6)));
  return String(v);
};

/** A run configuration as "key=value" pairs; empty means the repository's own defaults. */
export const configLabel = (cfg: Record<string, unknown> | undefined | null): string => {
  const entries = Object.entries(cfg ?? {}).filter(([k]) => k !== "__code__");
  if (!entries.length) return "repository defaults";
  return entries.map(([k, v]) => (v === true ? k : `${k}=${scalar(v)}`)).join(" · ");
};
