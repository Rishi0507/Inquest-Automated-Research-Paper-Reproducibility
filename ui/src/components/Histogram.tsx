import { motion } from "motion/react";
import type { Band } from "../api";
import { fmt, useTooltip } from "../ui";

type Props = {
  values: number[];
  reported: number;
  seedBand?: Band | null;
  specBand?: [number, number] | null;
  alignedBand?: Band | null;
  alignedValues?: number[] | null;
  metric?: string;
};

const W = 1040, H = 280, M = { l: 40, r: 16, t: 28, b: 34 };

function ticks(lo: number, hi: number, n = 6) {
  const span = hi - lo;
  const raw = span / n;
  const mag = 10 ** Math.floor(Math.log10(raw));
  const step = [1, 2, 2.5, 5, 10].map((s) => s * mag).find((s) => span / s <= n) ?? raw;
  const out: number[] = [];
  for (let v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(+v.toFixed(6));
  return out;
}

export default function Histogram({ values, reported, seedBand, specBand, alignedBand, alignedValues, metric }: Props) {
  const tip = useTooltip();
  if (!values.length) return null;
  // The axis is set by the runs, the reported value and the two bands. An aligned band far
  // from them is reported in text instead of stretching the axis.
  const all = [...values, reported];
  if (seedBand) all.push(seedBand.lo, seedBand.hi);
  if (specBand) all.push(specBand[0], specBand[1]);
  let lo = Math.min(...all), hi = Math.max(...all);
  const span0 = hi - lo || 1;
  const alignedFits = !!alignedBand && alignedBand.lo >= lo - span0 * 0.5 && alignedBand.hi <= hi + span0 * 0.5;
  if (alignedFits && alignedBand) { lo = Math.min(lo, alignedBand.lo); hi = Math.max(hi, alignedBand.hi); }
  const pad = (hi - lo) * 0.07 || 0.5;
  lo -= pad; hi += pad;
  const x = (v: number) => M.l + ((v - lo) / (hi - lo)) * (W - M.l - M.r);

  const nb = Math.max(6, Math.min(22, Math.round(Math.sqrt(values.length) * 2.2)));
  const vmin = Math.min(...values), vmax = Math.max(...values);
  const width = (vmax - vmin) / nb || 0.1;
  const bins = Array.from({ length: vmax === vmin ? 1 : nb }, (_, i) => ({ a: vmin + i * width, b: vmin + (i + 1) * width, n: 0, m: 0 }));
  if (vmax === vmin) { bins[0].a = vmin - 0.05; bins[0].b = vmin + 0.05; }
  values.forEach((v) => { const i = Math.min(bins.length - 1, Math.floor((v - vmin) / width) || 0); bins[i].n++; });
  if (alignedFits) (alignedValues ?? []).forEach((v) => { const i = Math.floor((v - vmin) / width); if (i >= 0 && i < bins.length) bins[i].m++; });
  const ymax = Math.max(...bins.map((b) => Math.max(b.n, b.m)), 1);
  const y = (c: number) => H - M.b - (c / ymax) * (H - M.t - M.b);
  const xt = ticks(lo, hi);
  const yt = Array.from(new Set([0, Math.ceil(ymax / 2), ymax]));

  return (
    <div>
      <div className="chart-scroll"><svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Distribution of runs across seeds with bands and the reported value">
        <g className="grid">{yt.map((t) => <line key={t} x1={M.l} x2={W - M.r} y1={y(t)} y2={y(t)} />)}</g>
        {specBand && (
          <motion.rect initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.6 }}
            x={x(specBand[0])} y={M.t - 8} width={Math.max(1, x(specBand[1]) - x(specBand[0]))} height={H - M.t - M.b + 8}
            fill="var(--spec)" opacity={0.55} rx={4}
            onMouseMove={(e) => tip.show(e, `Specification band ${fmt(specBand[0])} to ${fmt(specBand[1])}`)} onMouseLeave={tip.hide} />
        )}
        {seedBand && (
          <motion.rect initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ duration: 0.6, delay: 0.1 }}
            x={x(seedBand.lo)} y={M.t - 4} width={Math.max(1, x(seedBand.hi) - x(seedBand.lo))} height={H - M.t - M.b + 4}
            fill="var(--band)" rx={4}
            onMouseMove={(e) => tip.show(e, `Seed band ${fmt(seedBand.lo)} to ${fmt(seedBand.hi)} (95% PI, n=${seedBand.n}, m=${seedBand.m})`)} onMouseLeave={tip.hide} />
        )}
        {alignedFits && alignedBand && (
          <rect x={x(alignedBand.lo)} y={M.t - 2} width={Math.max(1, x(alignedBand.hi) - x(alignedBand.lo))} height={H - M.t - M.b + 2}
            fill="none" stroke="var(--ink-2)" strokeDasharray="4 3" rx={4} />
        )}
        {bins.map((b, i) => {
          const bx = x(b.a) + 1, bw = Math.max(1.5, x(b.b) - x(b.a) - 2);
          return (
            <g key={i}>
              {b.n > 0 && (
                <motion.rect initial={{ height: 0, y: H - M.b }} animate={{ height: H - M.b - y(b.n), y: y(b.n) }}
                  transition={{ duration: 0.55, delay: 0.15 + i * 0.02, ease: [0.22, 1, 0.36, 1] }}
                  x={bx} width={bw} rx={3} fill="var(--series)"
                  onMouseMove={(e) => tip.show(e, `${b.n} run${b.n === 1 ? "" : "s"} in ${fmt(b.a)} to ${fmt(b.b)}`)} onMouseLeave={tip.hide} />
              )}
              {b.m > 0 && <rect x={bx + bw * 0.25} width={bw * 0.5} y={y(b.m)} height={H - M.b - y(b.m)} rx={2} fill="none" stroke="var(--ink)" strokeWidth={1.2} />}
            </g>
          );
        })}
        <line x1={M.l} x2={W - M.r} y1={H - M.b} y2={H - M.b} stroke="var(--line-strong)" />
        {xt.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={H - M.b} y2={H - M.b + 4} stroke="var(--line-strong)" />
            <text x={x(t)} y={H - M.b + 17} textAnchor="middle" className="tnum">{t}</text>
          </g>
        ))}
        {yt.map((t) => <text key={t} x={M.l - 8} y={y(t) + 4} textAnchor="end">{t}</text>)}
        <motion.g initial={{ opacity: 0 }} animate={{ opacity: 1 }} transition={{ delay: 0.5 }}>
          <line x1={x(reported)} x2={x(reported)} y1={M.t - 12} y2={H - M.b} stroke="var(--ink)" strokeWidth={2} />
          <text x={x(reported) + (x(reported) > W * 0.8 ? -6 : 6)} y={M.t - 14} textAnchor={x(reported) > W * 0.8 ? "end" : "start"}
            style={{ fill: "var(--ink)", fontWeight: 600, fontSize: 12 }}>reported {reported}</text>
        </motion.g>
        {metric && <text x={W - M.r} y={H - 2} textAnchor="end">{metric}</text>}
      </svg></div>
      <div className="legend">
        <span><i style={{ background: "var(--series)" }} />runs (n={values.length})</span>
        {seedBand && <span><i style={{ background: "var(--band)" }} />seed band: what this code produces</span>}
        {specBand && <span><i style={{ background: "var(--spec)" }} />specification band: what the text permits</span>}
        {alignedBand && (alignedFits
          ? <span><i style={{ border: "1.2px dashed var(--ink-2)", background: "transparent" }} />aligned band</span>
          : <span>aligned configuration: mean {fmt(alignedBand.mean)}, band [{fmt(alignedBand.lo)}, {fmt(alignedBand.hi)}], off this axis</span>)}
      </div>
      {tip.node}
    </div>
  );
}
