// Small hand-rolled SVG charts (no chart library).
import { MODEL_COLORS, pct } from "../lib/format";

export interface CIBar {
  model: string;
  value: number | null;
  low: number | null;
  high: number | null;
  n: number;
}

/** Grouped horizontal bars with 95% CI whiskers, one group per category. */
export function CIBarChart({ groups, models }: { groups: { label: string; bars: CIBar[] }[]; models: string[] }) {
  const rowH = 14;
  const gap = 10;
  const labelW = 110;
  const width = 560;
  const plotW = width - labelW - 60;
  const groupH = models.length * rowH + gap;
  const height = groups.length * groupH + 24;
  const x = (v: number) => labelW + v * plotW;
  return (
    <figure>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Accuracy by category with 95% confidence intervals">
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={0} y2={height - 18} stroke="#263040" strokeDasharray={t === 0 ? "" : "2 3"} />
            <text x={x(t)} y={height - 4} fill="#8291a7" fontSize="10" textAnchor="middle">
              {t * 100}%
            </text>
          </g>
        ))}
        {groups.map((g, gi) => {
          const y0 = gi * groupH;
          return (
            <g key={g.label}>
              <text x={0} y={y0 + (models.length * rowH) / 2 + 4} fill="#cfd7e2" fontSize="11">
                {g.label}
              </text>
              {g.bars.map((b, bi) => {
                const y = y0 + bi * rowH;
                const color = MODEL_COLORS[models.indexOf(b.model) % MODEL_COLORS.length];
                return (
                  <g key={b.model}>
                    <title>{`${b.model} · ${g.label}: ${pct(b.value)} [${pct(b.low)}, ${pct(b.high)}], n=${b.n}`}</title>
                    {b.value !== null && (
                      <rect x={labelW} y={y + 2} width={Math.max(1, b.value * plotW)} height={rowH - 5} rx={2} fill={color} fillOpacity={0.75} />
                    )}
                    {b.low !== null && b.high !== null && (
                      <g stroke="#e8edf3" strokeWidth={1}>
                        <line x1={x(b.low)} x2={x(b.high)} y1={y + rowH / 2} y2={y + rowH / 2} />
                        <line x1={x(b.low)} x2={x(b.low)} y1={y + 3} y2={y + rowH - 4} />
                        <line x1={x(b.high)} x2={x(b.high)} y1={y + 3} y2={y + rowH - 4} />
                      </g>
                    )}
                    <text x={width - 56} y={y + rowH - 4} fill="#a9b5c6" fontSize="10">
                      {b.value === null ? "n/a" : `${pct(b.value, 0)} n=${b.n}`}
                    </text>
                  </g>
                );
              })}
            </g>
          );
        })}
      </svg>
      <Legend models={models} />
    </figure>
  );
}

export function Legend({ models }: { models: string[] }) {
  return (
    <figcaption className="mt-2 flex flex-wrap gap-3 text-xs text-ink-300">
      {models.map((m, i) => (
        <span key={m} className="inline-flex items-center gap-1.5">
          <span className="h-2.5 w-2.5 rounded-sm" style={{ background: MODEL_COLORS[i % MODEL_COLORS.length] }} aria-hidden />
          {m}
        </span>
      ))}
      <span className="text-ink-500">whiskers = Wilson 95% CI</span>
    </figcaption>
  );
}

/** Horizontal stacked bar for paired outcomes / vote outcomes. */
export function StackBar({ parts }: { parts: { label: string; value: number; color: string }[] }) {
  const total = parts.reduce((s, p) => s + p.value, 0);
  if (!total) return <div className="h-2.5 rounded bg-ink-800" aria-label="no data" />;
  return (
    <div className="flex h-2.5 overflow-hidden rounded bg-ink-800" role="img" aria-label={parts.map((p) => `${p.label} ${p.value}`).join(", ")}>
      {parts.map((p) =>
        p.value ? <div key={p.label} title={`${p.label}: ${p.value}`} style={{ width: `${(p.value / total) * 100}%`, background: p.color }} /> : null,
      )}
    </div>
  );
}

export interface Series {
  label: string;
  points: { x: string; y: number | null; low?: number | null; high?: number | null }[];
}

/** Line chart over runs (x is categorical, chronological). */
export function LineChart({ series, xLabels }: { series: Series[]; xLabels: string[] }) {
  const width = 640;
  const height = 240;
  const pad = { l: 40, r: 16, t: 12, b: 34 };
  const pw = width - pad.l - pad.r;
  const ph = height - pad.t - pad.b;
  const x = (i: number) => pad.l + (xLabels.length <= 1 ? pw / 2 : (i / (xLabels.length - 1)) * pw);
  const y = (v: number) => pad.t + (1 - v) * ph;
  return (
    <figure>
      <svg viewBox={`0 0 ${width} ${height}`} className="w-full" role="img" aria-label="Accuracy across runs">
        {[0, 0.25, 0.5, 0.75, 1].map((t) => (
          <g key={t}>
            <line x1={pad.l} x2={width - pad.r} y1={y(t)} y2={y(t)} stroke="#263040" strokeDasharray="2 3" />
            <text x={pad.l - 6} y={y(t) + 3} fill="#8291a7" fontSize="10" textAnchor="end">
              {t * 100}%
            </text>
          </g>
        ))}
        {xLabels.map((l, i) => (
          <text key={l + i} x={x(i)} y={height - 14} fill="#8291a7" fontSize="10" textAnchor="middle">
            {l}
          </text>
        ))}
        {series.map((s, si) => {
          const color = MODEL_COLORS[si % MODEL_COLORS.length];
          const pts = s.points
            .map((p) => ({ ...p, i: xLabels.indexOf(p.x) }))
            .filter((p) => p.y !== null && p.i >= 0);
          const d = pts.map((p, k) => `${k ? "L" : "M"}${x(p.i)},${y(p.y as number)}`).join(" ");
          return (
            <g key={s.label}>
              {pts.length > 1 && <path d={d} fill="none" stroke={color} strokeWidth={2} />}
              {pts.map((p) => (
                <g key={p.i}>
                  {p.low != null && p.high != null && (
                    <line x1={x(p.i)} x2={x(p.i)} y1={y(p.low)} y2={y(p.high)} stroke={color} strokeOpacity={0.45} strokeWidth={3} />
                  )}
                  <circle cx={x(p.i)} cy={y(p.y as number)} r={3.5} fill={color}>
                    <title>{`${s.label} @ ${p.x}: ${pct(p.y)}`}</title>
                  </circle>
                </g>
              ))}
            </g>
          );
        })}
      </svg>
      <figcaption className="mt-2 flex flex-wrap gap-3 text-xs text-ink-300">
        {series.map((s, i) => (
          <span key={s.label} className="inline-flex items-center gap-1.5">
            <span className="h-2.5 w-2.5 rounded-full" style={{ background: MODEL_COLORS[i % MODEL_COLORS.length] }} aria-hidden />
            {s.label}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}
