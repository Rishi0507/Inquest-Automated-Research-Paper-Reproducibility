import { useEffect, useRef } from "react";
import { motion } from "motion/react";
import type { Job } from "../api";
import { useJob } from "../ui";

const STAGE_LABEL: Record<string, string> = {
  environment: "Environment", claims: "Claims", preflight: "Pre-flight", mapping: "Mapping", determinism: "Determinism",
  swarm: "Seed swarm", witness: "Witness", deviations: "Deviations", attribution: "Attribution",
  specification: "Specification", verdicts: "Verdicts",
};

export function Stepper({ job }: { job: Job }) {
  return (
    <div className="stepper">
      {Object.entries(job.stages).map(([name, st]) => (
        <div key={name} className={`s ${st}`}>
          <div className="bar">
            <motion.i initial={false} animate={{ scaleX: st === "pending" ? 0 : 1 }} transition={{ duration: 0.5, ease: [0.22, 1, 0.36, 1] }} />
          </div>
          <span className="label">{STAGE_LABEL[name] ?? name}</span>
        </div>
      ))}
    </div>
  );
}

export function JobLog({ job, height = 300 }: { job: Job; height?: number }) {
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => { if (ref.current) ref.current.scrollTop = ref.current.scrollHeight; }, [job.log.length]);
  return (
    <div className="log" ref={ref} style={{ maxHeight: height }}>
      {job.log.map((l, i) => (
        <div key={i}><span className="muted">{new Date(l.t * 1000).toLocaleTimeString()}</span>  {l.msg}</div>
      ))}
      {!job.log.length && <span className="muted">waiting for the first message</span>}
    </div>
  );
}

export default function JobPanel({ jobId, compact, onDone }: { jobId: string; compact?: boolean; onDone?: (j: Job) => void }) {
  const job = useJob(jobId, onDone);
  if (!job) return <span className="muted small">starting</span>;
  const last = job.log[job.log.length - 1]?.msg;
  if (compact) {
    return (
      <div style={{ display: "grid", gap: 8, width: "100%" }}>
        <div className="row small">
          <span className={`tag ${job.status === "failed" ? "v-critical" : job.status === "done" ? "v-good" : ""}`}>
            <span className="dot" style={job.status === "running" ? { background: "var(--accent)" } : undefined} />{job.kind} {job.status}
          </span>
          <span className="muted" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap", maxWidth: 420 }}>{job.error ?? last}</span>
        </div>
        {job.kind === "analyze" && <Stepper job={job} />}
      </div>
    );
  }
  return (
    <div style={{ display: "grid", gap: 16 }}>
      <Stepper job={job} />
      <JobLog job={job} />
    </div>
  );
}
