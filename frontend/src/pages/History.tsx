import { useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { api, type HistoryRun, type RunSummary } from "../api";
import { LineChart, type Series } from "../components/charts";
import { Card, Empty, ErrorBox, Field, PageHeader, Spinner, StatusBadge, Table, inputCls, td, th } from "../components/ui";
import { useAsync } from "../lib/hooks";
import { pct, usd, when } from "../lib/format";

export function buildSeries(history: HistoryRun[], category: string): { series: Series[]; xLabels: string[] } {
  const xLabels = history.map((h) => `#${h.run_id}`);
  const byLabel = new Map<string, Series>();
  for (const h of history) {
    for (const m of h.models) {
      const s = byLabel.get(m.label) ?? { label: m.label, points: [] };
      const y = category === "overall" ? m.acc : m.by_category[category] ?? null;
      s.points.push({ x: `#${h.run_id}`, y, low: category === "overall" ? m.ci_low : null, high: category === "overall" ? m.ci_high : null });
      byLabel.set(m.label, s);
    }
  }
  return { series: [...byLabel.values()], xLabels };
}

export default function HistoryPage() {
  const history = useAsync<HistoryRun[]>(() => api.get("/history"));
  const runs = useAsync<RunSummary[]>(() => api.get("/runs"));
  const [category, setCategory] = useState("overall");
  const categories = useMemo(
    () => [...new Set((history.data ?? []).flatMap((h) => h.models.flatMap((m) => Object.keys(m.by_category))))],
    [history.data],
  );
  const chart = useMemo(() => buildSeries(history.data ?? [], category), [history.data, category]);

  return (
    <>
      <PageHeader title="History" subtitle="Score trends per endpoint across runs. Lines follow endpoint names, so name versions clearly (e.g. 'my-bot v1', 'my-bot v2')." />
      <Card title="Accuracy over runs" className="mb-5"
        actions={
          <Field label="Category" htmlFor="h-cat">
            <select id="h-cat" className={inputCls} value={category} onChange={(e) => setCategory(e.target.value)}>
              <option value="overall">overall</option>
              {categories.map((c) => <option key={c}>{c}</option>)}
            </select>
          </Field>
        }>
        {history.loading && !history.data ? <Spinner /> : history.error ? <ErrorBox message={history.error} /> : !history.data?.length ? (
          <Empty title="No finished runs yet" />
        ) : (
          <>
            <LineChart series={chart.series} xLabels={chart.xLabels} />
            <p className="mt-2 text-xs text-ink-500">Runs may use different suites or item subsets; compare like with like (see the table).</p>
          </>
        )}
      </Card>
      {history.data && history.data.length > 0 && (
        <Card title="Per-run accuracy" className="mb-5">
          <Table>
            <thead><tr><th className={th}>Run</th><th className={th}>Suite</th><th className={th}>Model</th><th className={th}>Role</th><th className={th}>Overall (95% CI)</th>{categories.map((c) => <th key={c} className={th}>{c}</th>)}</tr></thead>
            <tbody>
              {history.data.slice().reverse().flatMap((h) => h.models.map((m, i) => (
                <tr key={`${h.run_id}-${m.run_model_id}`}>
                  <td className={td}>{i === 0 && <Link className="text-indigo-300 hover:underline" to={`/report/${h.run_id}`}>#{h.run_id}</Link>}</td>
                  <td className={`${td} text-ink-400`}>{i === 0 ? `${h.suite} v${h.suite_version}` : ""}</td>
                  <td className={td}>{m.label}</td>
                  <td className={`${td} text-ink-400`}>{m.role}</td>
                  <td className={`${td} tabular-nums`}>{pct(m.acc)} <span className="text-xs text-ink-500">[{pct(m.ci_low, 0)}–{pct(m.ci_high, 0)}] n={m.n}</span></td>
                  {categories.map((c) => <td key={c} className={`${td} tabular-nums`}>{pct(m.by_category[c] ?? null, 0)}</td>)}
                </tr>
              )))}
            </tbody>
          </Table>
        </Card>
      )}
      <Card title="All runs">
        {runs.loading && !runs.data ? <Spinner /> : !runs.data?.length ? <Empty title="No runs yet" /> : (
          <Table>
            <thead><tr><th className={th}>Run</th><th className={th}>Status</th><th className={th}>Models</th><th className={th}>Progress</th><th className={th}>Cost</th><th className={th}>Created</th></tr></thead>
            <tbody>
              {runs.data.map((r) => (
                <tr key={r.id}>
                  <td className={td}><Link className="text-indigo-300 hover:underline" to={`/runs/${r.id}`}>#{r.id} {r.name}</Link></td>
                  <td className={td}><StatusBadge status={r.status} /></td>
                  <td className={`${td} text-ink-300`}>{r.models?.map((m) => `${m.label}${m.role === "candidate" ? " (cand.)" : ""}`).join(", ")}</td>
                  <td className={`${td} tabular-nums`}>{r.done}/{r.total}</td>
                  <td className={`${td} tabular-nums`}>{usd(r.cost_usd)}</td>
                  <td className={`${td} text-ink-400`}>{when(r.created_at)}</td>
                </tr>
              ))}
            </tbody>
          </Table>
        )}
      </Card>
    </>
  );
}
