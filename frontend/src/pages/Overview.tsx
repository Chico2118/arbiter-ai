import { Link } from "react-router-dom";
import { api, type Overview } from "../api";
import { ButtonLink, Card, Empty, ErrorBox, LocalBadge, PageHeader, Spinner, Stat, StatusBadge, Table, td, th } from "../components/ui";
import { useAsync } from "../lib/hooks";
import { usd, when } from "../lib/format";

export default function OverviewPage() {
  const { data, error, loading, reload } = useAsync<Overview>(() => api.get("/overview"), [], {
    intervalMs: 5000,
    while: (d) => !!d?.recent_runs.some((r) => ["queued", "running", "judging"].includes(r.status)),
  });

  if (loading && !data) return <Spinner />;
  if (error) return <ErrorBox message={`Could not reach the backend: ${error}`} onRetry={reload} />;
  if (!data) return null;
  const pending = data.review.votes_pending + data.review.criteria_pending;

  return (
    <>
      <PageHeader
        title="Overview"
        subtitle="Compare a candidate model against baselines, find where it falls short, and catch regressions."
        actions={<ButtonLink to="/runs/new" variant="primary">New run</ButtonLink>}
      />
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <Stat label="Runs" value={data.counts.runs} to="/history" />
        <Stat label="Suites" value={data.counts.suites} to="/suites" />
        <Stat label="Connected models" value={data.counts.endpoints} to="/models" />
        <Stat
          label="Review queue"
          value={pending}
          hint={`${data.review.votes_pending} blind votes · ${data.review.criteria_pending} checklists`}
          to="/review"
        />
      </div>

      <div className="mt-5 grid gap-5 xl:grid-cols-3">
        <Card title="Recent runs" className="xl:col-span-2">
          {data.recent_runs.length === 0 ? (
            <Empty title="No runs yet">
              Add a model on the <Link className="text-indigo-300 underline" to="/models">Models</Link> page, then start a{" "}
              <Link className="text-indigo-300 underline" to="/runs/new">new run</Link>.
            </Empty>
          ) : (
            <Table>
              <thead>
                <tr>
                  <th className={th}>Run</th>
                  <th className={th}>Status</th>
                  <th className={th}>Progress</th>
                  <th className={th}>Cost</th>
                  <th className={th}>Created</th>
                </tr>
              </thead>
              <tbody>
                {data.recent_runs.map((r) => (
                  <tr key={r.id}>
                    <td className={td}>
                      <Link to={`/runs/${r.id}`} className="text-indigo-300 hover:underline">
                        #{r.id} {r.name}
                      </Link>
                    </td>
                    <td className={td}><StatusBadge status={r.status} /></td>
                    <td className={`${td} tabular-nums`}>{r.done}/{r.total}</td>
                    <td className={`${td} tabular-nums`}>{usd(r.cost_usd)}</td>
                    <td className={`${td} text-ink-400`}>{when(r.created_at)}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        <div className="space-y-5">
          <Card title="Connected models">
            {data.endpoints.length === 0 ? (
              <Empty title="No models yet"><Link className="text-indigo-300 underline" to="/models">Add one</Link></Empty>
            ) : (
              <ul className="space-y-2">
                {data.endpoints.map((e) => (
                  <li key={e.id} className="flex items-center justify-between gap-2 text-sm">
                    <span className="truncate">
                      {e.name} <span className="text-ink-500">· {e.model}</span>
                    </span>
                    <LocalBadge local={e.destination.local} />
                  </li>
                ))}
              </ul>
            )}
          </Card>
          <Card title="Suites">
            <ul className="space-y-2 text-sm">
              {data.suites.map((s) => (
                <li key={s.id} className="flex justify-between gap-2">
                  <Link to="/suites" className="truncate text-indigo-300 hover:underline">{s.name}</Link>
                  <span className="text-ink-400 tabular-nums">
                    v{s.versions[0]?.version} · {s.versions[0]?.items} items
                  </span>
                </li>
              ))}
            </ul>
          </Card>
        </div>
      </div>
    </>
  );
}
