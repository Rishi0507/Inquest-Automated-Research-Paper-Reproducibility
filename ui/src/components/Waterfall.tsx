import { motion } from "motion/react";
import type { Attribution } from "../api";
import { fmt, pct, signed, useTooltip } from "../ui";

const W = 1040, ROW = 40, M = { l: 230, r: 190, t: 12, b: 30 };

export default function Waterfall({ att }: { att: Attribution }) {
  const tip = useTooltip();
  const shares = att.shares;
  const rows = shares.length + 3;
  const H = M.t + rows * ROW + M.b;
  const points: number[] = [att.base, att.reported, att.aligned];
  let cur = att.base;
  shares.forEach((s) => { points.push(cur + s.points, cur + s.ci[0], cur + s.ci[1]); cur += s.points; });
  let lo = Math.min(...points), hi = Math.max(...points);
  const pad = (hi - lo) * 0.08 || 0.4;
  lo -= pad; hi += pad;
  const x = (v: number) => M.l + ((v - lo) / (hi - lo)) * (W - M.l - M.r);
  const yRow = (i: number) => M.t + i * ROW + ROW / 2;
  const toward = (p: number) => p * att.gap >= 0;

  let run = att.base;
  const bars = shares.map((s) => {
    const start = run;
    run += s.points;
    return { s, start, end: run };
  });

  return (
    <div>
      <div className="chart-scroll"><svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Measured attribution of the gap between the repository and the reported value">
        {[lo, (lo + hi) / 2, hi].map((t, i) => (
          <g key={i} className="grid"><line x1={x(t)} x2={x(t)} y1={M.t} y2={H - M.b} /></g>
        ))}
        {/* repository as-is */}
        <text x={M.l - 14} y={yRow(0) + 4} textAnchor="end" style={{ fill: "var(--ink)", fontSize: 12.5 }}>Repository as-is</text>
        <circle cx={x(att.base)} cy={yRow(0)} r={5} fill="var(--ink)" />
        <text x={W - M.r + 12} y={yRow(0) + 4} className="tnum">{fmt(att.base)}</text>
        <line x1={x(att.base)} x2={x(att.base)} y1={yRow(0)} y2={yRow(1) - 10} stroke="var(--line-strong)" strokeDasharray="2 3" />

        {bars.map(({ s, start, end }, i) => {
          const r = i + 1;
          const x0 = x(Math.min(start, end)), w = Math.max(2, Math.abs(x(end) - x(start)));
          const color = s.is_noise ? "var(--neutral)" : toward(s.points) ? "var(--series)" : "var(--neg)";
          return (
            <g key={s.dev_id}
              onMouseMove={(e) => tip.show(e, <span>{s.label}: {signed(s.points)} pts, 95% CI [{signed(s.ci[0])}, {signed(s.ci[1])}]{s.is_noise ? ", within noise" : ""}</span>)}
              onMouseLeave={tip.hide}>
              <rect x={0} y={yRow(r) - ROW / 2} width={W} height={ROW} fill="transparent" />
              <text x={M.l - 14} y={yRow(r) + 4} textAnchor="end" style={{ fill: "var(--ink)", fontSize: 12.5 }}>
                {s.label.length > 30 ? `${s.label.slice(0, 29)}…` : s.label}
              </text>
              <motion.rect
                initial={{ scaleX: 0 }} animate={{ scaleX: 1 }}
                transition={{ duration: 0.6, delay: 0.25 + i * 0.22, ease: [0.22, 1, 0.36, 1] }}
                style={{ transformOrigin: `${x(start)}px ${yRow(r)}px` }}
                x={x0} y={yRow(r) - 11} width={w} height={22} rx={4} fill={color} />
              <motion.g initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.55 + i * 0.22 }}>
                <line x1={x(start + s.ci[0])} x2={x(start + s.ci[1])} y1={yRow(r)} y2={yRow(r)} stroke="var(--ink)" strokeWidth={1.2} />
                <line x1={x(start + s.ci[0])} x2={x(start + s.ci[0])} y1={yRow(r) - 5} y2={yRow(r) + 5} stroke="var(--ink)" strokeWidth={1.2} />
                <line x1={x(start + s.ci[1])} x2={x(start + s.ci[1])} y1={yRow(r) - 5} y2={yRow(r) + 5} stroke="var(--ink)" strokeWidth={1.2} />
                <text x={W - M.r + 12} y={yRow(r) + 4} className="tnum" style={{ fill: "var(--ink)" }}>
                  {signed(s.points)}
                  <tspan style={{ fill: "var(--muted)" }}>{s.fraction !== null ? `  ${pct(s.fraction)}` : ""}</tspan>
                </text>
              </motion.g>
              {i < bars.length && <line x1={x(end)} x2={x(end)} y1={yRow(r) + 11} y2={yRow(r + 1) - 11} stroke="var(--line-strong)" strokeDasharray="2 3" />}
            </g>
          );
        })}

        {/* residual */}
        {(() => {
          const r = shares.length + 1;
          const start = att.aligned, end = att.reported;
          return (
            <g onMouseMove={(e) => tip.show(e, `Residual ${signed(att.residual)}: ${att.residual_in_noise ? "inside the aligned seed band" : "outside every band, unexplained"}`)} onMouseLeave={tip.hide}>
              <text x={M.l - 14} y={yRow(r) + 4} textAnchor="end" style={{ fill: "var(--ink-2)", fontSize: 12.5 }}>Residual</text>
              <motion.rect initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.35 + shares.length * 0.22 }}
                x={x(Math.min(start, end))} y={yRow(r) - 11} width={Math.max(2, Math.abs(x(end) - x(start)))} height={22} rx={4}
                fill="none" stroke="var(--ink-2)" strokeDasharray="3 3" />
              <text x={W - M.r + 12} y={yRow(r) + 4} className="tnum">{signed(att.residual)} <tspan>{att.residual_in_noise ? "noise" : "unexplained"}</tspan></text>
            </g>
          );
        })()}
        {/* reported */}
        {(() => {
          const r = shares.length + 2;
          return (
            <g>
              <text x={M.l - 14} y={yRow(r) + 4} textAnchor="end" style={{ fill: "var(--ink)", fontSize: 12.5, fontWeight: 600 }}>Reported</text>
              <rect x={x(att.reported) - 5} y={yRow(r) - 5} width={10} height={10} rx={2} fill="var(--ink)" transform={`rotate(45 ${x(att.reported)} ${yRow(r)})`} />
              <text x={W - M.r + 12} y={yRow(r) + 4} className="tnum" style={{ fill: "var(--ink)", fontWeight: 600 }}>{fmt(att.reported)}</text>
            </g>
          );
        })()}
        {[lo, (lo + hi) / 2, hi].map((t, i) => (
          <text key={i} x={x(t)} y={H - 8} textAnchor="middle" className="tnum">{t.toFixed(1)}</text>
        ))}
      </svg></div>
      <div className="legend">
        <span><i style={{ background: "var(--series)" }} />moves toward the reported value</span>
        <span><i style={{ background: "var(--neg)" }} />moves away</span>
        <span><i style={{ background: "var(--neutral)" }} />within noise (|φ| ≤ τ or CI spans zero)</span>
        <span>whiskers: 95% bootstrap interval</span>
      </div>
      {tip.node}
    </div>
  );
}
