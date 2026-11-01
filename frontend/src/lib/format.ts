export function pct(v: number | null | undefined, digits = 1): string {
  return v === null || v === undefined ? "–" : `${(v * 100).toFixed(digits)}%`;
}

export function pts(v: number | null | undefined): string {
  if (v === null || v === undefined) return "–";
  return `${v > 0 ? "+" : ""}${v.toFixed(1)} pts`;
}

export function usd(v: number | null | undefined, digits = 4): string {
  if (v === null || v === undefined) return "–";
  if (v === 0) return "$0";
  return `$${v.toFixed(digits)}`;
}

export function pval(p: number | null | undefined): string {
  if (p === null || p === undefined) return "–";
  return p < 0.001 ? "p<0.001" : `p=${p.toFixed(3)}`;
}

export function seconds(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "–";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function when(iso: string | null | undefined): string {
  if (!iso) return "–";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString();
}

export function humanize(s: string): string {
  return s.replace(/_/g, " ");
}

export const MODEL_COLORS = ["#818cf8", "#34d399", "#fbbf24", "#f472b6", "#38bdf8", "#a78bfa"];

export const STATUS_TONE: Record<string, "ok" | "bad" | "warn" | "info" | "muted"> = {
  completed: "ok",
  running: "info",
  judging: "info",
  queued: "muted",
  cancelling: "warn",
  cancelled: "muted",
  budget_exceeded: "warn",
  failed: "bad",
  interrupted: "warn",
};
