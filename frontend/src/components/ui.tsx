import type { ButtonHTMLAttributes, ReactNode } from "react";
import { Link } from "react-router-dom";
import { STATUS_TONE, humanize } from "../lib/format";

type Tone = "ok" | "bad" | "warn" | "info" | "muted" | "accent";

const TONES: Record<Tone, string> = {
  ok: "bg-emerald-500/10 text-emerald-300 ring-emerald-500/30",
  bad: "bg-rose-500/10 text-rose-300 ring-rose-500/30",
  warn: "bg-amber-500/10 text-amber-300 ring-amber-500/30",
  info: "bg-sky-500/10 text-sky-300 ring-sky-500/30",
  muted: "bg-ink-700/40 text-ink-300 ring-ink-600/50",
  accent: "bg-indigo-500/10 text-indigo-300 ring-indigo-500/30",
};

export function Badge({ tone = "muted", children, title }: { tone?: Tone; children: ReactNode; title?: string }) {
  return (
    <span
      title={title}
      className={`inline-flex items-center gap-1 whitespace-nowrap rounded px-1.5 py-0.5 text-[11px] font-medium ring-1 ring-inset ${TONES[tone]}`}
    >
      {children}
    </span>
  );
}

export function StatusBadge({ status }: { status: string }) {
  return <Badge tone={STATUS_TONE[status] ?? "muted"}>{humanize(status)}</Badge>;
}

export function LocalBadge({ local }: { local: boolean }) {
  return local ? (
    <Badge tone="ok" title="Runs on this machine; no data leaves it">local</Badge>
  ) : (
    <Badge tone="warn" title="Prompts are sent to an external company and may cost money">paid · external</Badge>
  );
}

type BtnVariant = "primary" | "secondary" | "ghost" | "danger";
const BTN: Record<BtnVariant, string> = {
  primary: "bg-indigo-500 text-white hover:bg-indigo-400 disabled:bg-indigo-500/40",
  secondary: "bg-ink-800 text-ink-100 ring-1 ring-inset ring-ink-600 hover:bg-ink-700 disabled:opacity-50",
  ghost: "text-ink-300 hover:bg-ink-800 hover:text-ink-100 disabled:opacity-50",
  danger: "bg-rose-600/90 text-white hover:bg-rose-500 disabled:opacity-50",
};

export function Button({
  variant = "secondary",
  className = "",
  ...props
}: ButtonHTMLAttributes<HTMLButtonElement> & { variant?: BtnVariant }) {
  return (
    <button
      type="button"
      {...props}
      className={`inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors disabled:cursor-not-allowed ${BTN[variant]} ${className}`}
    />
  );
}

export function ButtonLink({ to, variant = "secondary", children }: { to: string; variant?: BtnVariant; children: ReactNode }) {
  return (
    <Link to={to} className={`inline-flex items-center justify-center gap-1.5 rounded-md px-3 py-1.5 text-sm font-medium transition-colors ${BTN[variant]}`}>
      {children}
    </Link>
  );
}

export function Card({
  title,
  actions,
  children,
  className = "",
  subtitle,
}: {
  title?: ReactNode;
  actions?: ReactNode;
  children: ReactNode;
  className?: string;
  subtitle?: ReactNode;
}) {
  return (
    <section className={`rounded-lg border border-ink-700/70 bg-ink-900 ${className}`}>
      {(title || actions) && (
        <header className="flex flex-wrap items-start justify-between gap-2 border-b border-ink-700/70 px-4 py-2.5">
          <div>
            {title && <h2 className="text-sm font-semibold text-ink-100">{title}</h2>}
            {subtitle && <p className="mt-0.5 text-xs text-ink-400">{subtitle}</p>}
          </div>
          {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
        </header>
      )}
      <div className="p-4">{children}</div>
    </section>
  );
}

export function PageHeader({ title, subtitle, actions }: { title: string; subtitle?: ReactNode; actions?: ReactNode }) {
  return (
    <div className="mb-5 flex flex-wrap items-end justify-between gap-3">
      <div>
        <h1 className="text-xl font-semibold tracking-tight text-ink-100">{title}</h1>
        {subtitle && <p className="mt-1 text-sm text-ink-400">{subtitle}</p>}
      </div>
      {actions && <div className="flex flex-wrap gap-2">{actions}</div>}
    </div>
  );
}

export function Spinner({ label = "Loading" }: { label?: string }) {
  return (
    <div role="status" className="flex items-center gap-2 py-6 text-sm text-ink-400">
      <span className="h-4 w-4 animate-spin rounded-full border-2 border-ink-600 border-t-indigo-400" aria-hidden />
      {label}…
    </div>
  );
}

export function ErrorBox({ message, onRetry }: { message: string; onRetry?: () => void }) {
  return (
    <div role="alert" className="flex flex-wrap items-center justify-between gap-2 rounded-md border border-rose-500/40 bg-rose-500/10 px-3 py-2 text-sm text-rose-200">
      <span>{message}</span>
      {onRetry && (
        <Button variant="ghost" onClick={onRetry}>
          Retry
        </Button>
      )}
    </div>
  );
}

export function Notice({ tone = "info", children }: { tone?: "info" | "warn" | "ok" | "bad"; children: ReactNode }) {
  const cls = {
    info: "border-sky-500/30 bg-sky-500/5 text-sky-200",
    warn: "border-amber-500/30 bg-amber-500/5 text-amber-200",
    ok: "border-emerald-500/30 bg-emerald-500/5 text-emerald-200",
    bad: "border-rose-500/30 bg-rose-500/5 text-rose-200",
  }[tone];
  return <div className={`rounded-md border px-3 py-2 text-sm ${cls}`}>{children}</div>;
}

export function Empty({ title, children }: { title: string; children?: ReactNode }) {
  return (
    <div className="rounded-md border border-dashed border-ink-700 px-4 py-8 text-center">
      <p className="text-sm font-medium text-ink-200">{title}</p>
      {children && <div className="mt-1 text-sm text-ink-400">{children}</div>}
    </div>
  );
}

export function Stat({ label, value, hint, to }: { label: string; value: ReactNode; hint?: ReactNode; to?: string }) {
  const body = (
    <>
      <div className="text-xs uppercase tracking-wide text-ink-400">{label}</div>
      <div className="mt-1 text-2xl font-semibold tabular-nums text-ink-100">{value}</div>
      {hint && <div className="mt-0.5 text-xs text-ink-400">{hint}</div>}
    </>
  );
  const cls = "block rounded-lg border border-ink-700/70 bg-ink-900 px-4 py-3";
  return to ? (
    <Link to={to} className={`${cls} hover:border-indigo-500/50`}>
      {body}
    </Link>
  ) : (
    <div className={cls}>{body}</div>
  );
}

export function ProgressBar({ value, total, label }: { value: number; total: number; label?: string }) {
  const ratio = total > 0 ? Math.min(1, value / total) : 0;
  return (
    <div>
      <div
        className="h-2 w-full overflow-hidden rounded-full bg-ink-800"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={total}
        aria-valuenow={value}
        aria-label={label ?? "Progress"}
      >
        <div className="h-full bg-indigo-400 transition-all" style={{ width: `${ratio * 100}%` }} />
      </div>
      <div className="mt-1 text-xs tabular-nums text-ink-400">
        {value} / {total} {label ? `· ${label}` : ""}
      </div>
    </div>
  );
}

/** A number that links to the run-detail view filtered to its evidence items. */
export function Evidence({ runId, ids, label, children }: { runId: number; ids: number[]; label: string; children: ReactNode }) {
  if (!ids.length) return <span className="tabular-nums">{children}</span>;
  const q = new URLSearchParams({ items: ids.join(","), why: label });
  return (
    <Link
      to={`/runs/${runId}?${q.toString()}`}
      className="tabular-nums text-indigo-300 underline decoration-indigo-500/40 underline-offset-2 hover:text-indigo-200"
      title={`Show ${ids.length} evidence item(s): ${label}`}
    >
      {children}
    </Link>
  );
}

export function Field({ label, htmlFor, hint, children }: { label: string; htmlFor: string; hint?: ReactNode; children: ReactNode }) {
  return (
    <div className="space-y-1">
      <label htmlFor={htmlFor} className="block text-xs font-medium text-ink-300">
        {label}
      </label>
      {children}
      {hint && <p className="text-xs text-ink-500">{hint}</p>}
    </div>
  );
}

export const inputCls =
  "w-full rounded-md border border-ink-600 bg-ink-950 px-2.5 py-1.5 text-sm text-ink-100 placeholder:text-ink-500 focus:border-indigo-400 focus:outline-none";

export function Kbd({ children }: { children: ReactNode }) {
  return (
    <kbd className="rounded border border-ink-600 bg-ink-800 px-1.5 py-0.5 font-mono text-[11px] text-ink-200">
      {children}
    </kbd>
  );
}

export function Table({ children }: { children: ReactNode }) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full border-collapse text-left text-sm">{children}</table>
    </div>
  );
}

export const th = "border-b border-ink-700 px-2 py-1.5 text-xs font-medium uppercase tracking-wide text-ink-400";
export const td = "border-b border-ink-800 px-2 py-1.5 align-top";
