import { useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import { api, type Endpoint, type Estimate, type Suite, type SuiteVersionDetail } from "../api";
import {
  Button, ButtonLink, Card, Empty, ErrorBox, Field, LocalBadge, Notice, PageHeader, Spinner, Table, inputCls, td, th,
} from "../components/ui";
import { useAsync } from "../lib/hooks";
import { usd } from "../lib/format";

export default function NewRunPage() {
  const navigate = useNavigate();
  const endpoints = useAsync<Endpoint[]>(() => api.get("/endpoints"));
  const suites = useAsync<Suite[]>(() => api.get("/suites"));
  const [versionId, setVersionId] = useState<number | null>(null);
  const [candidate, setCandidate] = useState<number | null>(null);
  const [baselines, setBaselines] = useState<number[]>([]);
  const [reps, setReps] = useState(1);
  const [budget, setBudget] = useState(1);
  const [cats, setCats] = useState<string[]>([]);
  const [limit, setLimit] = useState(0);
  const [judgeOn, setJudgeOn] = useState(false);
  const [judgeId, setJudgeId] = useState<number | null>(null);
  const [pairwise, setPairwise] = useState(false);
  const [name, setName] = useState("");
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [estError, setEstError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  useEffect(() => {
    if (versionId === null && suites.data?.[0]?.latest_version_id) setVersionId(suites.data[0].latest_version_id);
  }, [suites.data, versionId]);

  const version = useAsync<SuiteVersionDetail | null>(
    () => (versionId ? api.get(`/suite-versions/${versionId}`) : Promise.resolve(null)), [versionId]);
  const allCats = useMemo(() => [...new Set(version.data?.items.map((i) => i.category) ?? [])], [version.data]);

  const itemIds = useMemo(() => {
    let items = version.data?.items ?? [];
    if (cats.length) items = items.filter((i) => cats.includes(i.category));
    if (limit > 0) {
      // Take up to `limit` items per selected category so a small run still covers every category.
      const per: Record<string, number> = {};
      items = items.filter((i) => (per[i.category] = (per[i.category] ?? 0) + 1) <= limit);
    }
    return items.map((i) => i.id);
  }, [version.data, cats, limit]);

  const spec = useMemo(() => ({
    name, suite_version_id: versionId, candidate_id: candidate, baseline_ids: baselines, repetitions: reps,
    item_ids: itemIds, budget_usd: budget,
    judge: { enabled: judgeOn, endpoint_id: judgeOn ? judgeId : null, pairwise: judgeOn && pairwise },
  }), [name, versionId, candidate, baselines, reps, itemIds, budget, judgeOn, judgeId, pairwise]);
  const ready = !!versionId && candidate !== null && baselines.length > 0 && itemIds.length > 0 && (!judgeOn || judgeId !== null);

  useEffect(() => {
    if (!ready) {
      setEstimate(null);
      return;
    }
    const t = window.setTimeout(async () => {
      try {
        setEstimate(await api.post<Estimate>("/runs/estimate", spec));
        setEstError(null);
      } catch (e) {
        setEstError(e instanceof Error ? e.message : String(e));
      }
    }, 250);
    return () => window.clearTimeout(t);
  }, [spec, ready]);

  async function start() {
    setStarting(true);
    try {
      const res = await api.post<{ id: number }>("/runs", spec);
      navigate(`/runs/${res.id}`);
    } catch (e) {
      setEstError(e instanceof Error ? e.message : String(e));
      setStarting(false);
    }
  }

  if ((endpoints.loading && !endpoints.data) || (suites.loading && !suites.data)) return <Spinner />;
  if (endpoints.error || suites.error) return <ErrorBox message={endpoints.error ?? suites.error ?? ""} />;
  const eps = endpoints.data ?? [];
  if (eps.length < 2)
    return (
      <>
        <PageHeader title="New run" />
        <Empty title="You need at least two model endpoints">
          A run compares a candidate with one or more baselines. <ButtonLink to="/models">Add models</ButtonLink>
        </Empty>
      </>
    );
  const missingKeys = estimate?.endpoints.filter((e) => e.key_missing) ?? [];
  const external = estimate?.endpoints.filter((e) => !e.local) ?? [];

  return (
    <>
      <PageHeader title="New run" subtitle="Pick what to compare. You will see where every prompt is sent and what it may cost before anything runs." />
      <div className="grid gap-5 xl:grid-cols-5">
        <Card title="Configuration" className="xl:col-span-3">
          <div className="grid gap-4 md:grid-cols-2">
            <Field label="Suite version" htmlFor="nr-suite">
              <select id="nr-suite" className={inputCls} value={versionId ?? ""} onChange={(e) => setVersionId(Number(e.target.value))}>
                {suites.data?.flatMap((s) => s.versions.map((v) => (
                  <option key={v.id} value={v.id}>{s.name} v{v.version} ({v.items} items)</option>
                )))}
              </select>
            </Field>
            <Field label="Run name (optional)" htmlFor="nr-name">
              <input id="nr-name" maxLength={120} className={inputCls} value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. v2 prompt vs v1" />
            </Field>
            <Field label="Candidate (the model you are testing)" htmlFor="nr-cand">
              <select id="nr-cand" className={inputCls} value={candidate ?? ""} onChange={(e) => {
                const id = Number(e.target.value);
                setCandidate(id);
                setBaselines((b) => b.filter((x) => x !== id));
              }}>
                <option value="" disabled>Select…</option>
                {eps.map((e) => <option key={e.id} value={e.id}>{e.name} · {e.model}</option>)}
              </select>
            </Field>
            <fieldset>
              <legend className="mb-1 text-xs font-medium text-ink-300">Baselines (compare against)</legend>
              <div className="max-h-36 space-y-1 overflow-y-auto rounded-md border border-ink-700 p-2">
                {eps.filter((e) => e.id !== candidate).map((e) => (
                  <label key={e.id} className="flex items-center gap-2 text-sm">
                    <input type="checkbox" className="h-4 w-4 accent-indigo-500" checked={baselines.includes(e.id)}
                      onChange={(ev) => setBaselines((b) => (ev.target.checked ? [...b, e.id] : b.filter((x) => x !== e.id)))} />
                    {e.name} <LocalBadge local={e.destination.local} />
                  </label>
                ))}
              </div>
            </fieldset>
            <fieldset>
              <legend className="mb-1 text-xs font-medium text-ink-300">Categories (none selected = all)</legend>
              <div className="flex flex-wrap gap-x-3 gap-y-1">
                {allCats.map((c) => (
                  <label key={c} className="flex items-center gap-1.5 text-sm">
                    <input type="checkbox" className="h-4 w-4 accent-indigo-500" checked={cats.includes(c)}
                      onChange={(ev) => setCats((cs) => (ev.target.checked ? [...cs, c] : cs.filter((x) => x !== c)))} />
                    {c}
                  </label>
                ))}
              </div>
            </fieldset>
            <Field label="Items per category (0 = all)" htmlFor="nr-limit" hint={`${itemIds.length} item(s) selected`}>
              <input id="nr-limit" type="number" min={0} max={500} className={inputCls} value={limit} onChange={(e) => setLimit(Math.max(0, Number(e.target.value)))} />
            </Field>
            <Field label="Repetitions per item" htmlFor="nr-reps" hint="2–3 measures consistency; local models get slower">
              <select id="nr-reps" className={inputCls} value={reps} onChange={(e) => setReps(Number(e.target.value))}>
                <option value={1}>1</option><option value={2}>2</option><option value={3}>3</option>
              </select>
            </Field>
            <Field label="Budget cap (USD)" htmlFor="nr-budget" hint="The run stops before any call that could push spend past this">
              <input id="nr-budget" type="number" min={0} step={0.1} className={inputCls} value={budget} onChange={(e) => setBudget(Number(e.target.value))} />
            </Field>
          </div>
          <div className="mt-4 rounded-md border border-ink-700 p-3">
            <label className="flex items-center gap-2 text-sm">
              <input type="checkbox" className="h-4 w-4 accent-indigo-500" checked={judgeOn} onChange={(e) => setJudgeOn(e.target.checked)} />
              Use an AI judge for judge-type criteria (off by default)
            </label>
            {judgeOn && (
              <div className="mt-3 grid gap-3 md:grid-cols-2">
                <Field label="Judge endpoint" htmlFor="nr-judge">
                  <select id="nr-judge" className={inputCls} value={judgeId ?? ""} onChange={(e) => setJudgeId(Number(e.target.value))}>
                    <option value="" disabled>Select…</option>
                    {eps.map((e) => <option key={e.id} value={e.id}>{e.name} · {e.model}</option>)}
                  </select>
                </Field>
                <label className="flex items-center gap-2 pt-5 text-sm">
                  <input type="checkbox" className="h-4 w-4 accent-indigo-500" checked={pairwise} onChange={(e) => setPairwise(e.target.checked)} />
                  Also judge pairs (asks twice with positions swapped)
                </label>
                <p className="text-xs text-ink-400 md:col-span-2">
                  Judge verdicts only count after calibration against your blind human labels (n ≥ 10 and Cohen's κ ≥ 0.6 per
                  criterion). A judge never grades its own model family.
                </p>
              </div>
            )}
          </div>
        </Card>

        <Card title="Before you run" className="xl:col-span-2">
          {!ready ? (
            <p className="text-sm text-ink-400">Choose a candidate, at least one baseline and some items to see the estimate.</p>
          ) : !estimate ? <Spinner label="Estimating" /> : (
            <div className="space-y-3">
              <Table>
                <thead><tr><th className={th}>Endpoint</th><th className={th}>Sent to</th><th className={th}>Calls</th><th className={th}>Est. cost</th></tr></thead>
                <tbody>
                  {estimate.endpoints.map((e) => (
                    <tr key={`${e.role}-${e.endpoint_id}`}>
                      <td className={td}><div>{e.name}</div><div className="text-xs text-ink-500">{e.role}</div></td>
                      <td className={td}><div className="flex items-center gap-1.5">{e.company} <LocalBadge local={e.local} /></div><div className="font-mono text-xs text-ink-500">{e.host}</div></td>
                      <td className={`${td} tabular-nums`}>{e.calls}</td>
                      <td className={`${td} tabular-nums`}>{usd(e.est_cost_typical)}<div className="text-xs text-ink-500">max {usd(e.est_cost_max)}</div></td>
                    </tr>
                  ))}
                </tbody>
              </Table>
              <div className="flex justify-between text-sm">
                <span className="text-ink-400">Total estimate (worst case)</span>
                <span className="tabular-nums">{usd(estimate.total_typical)} ({usd(estimate.total_max)}) · cap {usd(estimate.budget_usd, 2)}</span>
              </div>
              {external.length > 0 ? (
                <Notice tone="warn">
                  {estimate.items * estimate.repetitions} prompt(s) will be sent to {[...new Set(external.map((e) => e.company))].join(", ")}.
                  Most providers' terms forbid using their outputs to train competing models.
                </Notice>
              ) : (
                <Notice tone="ok">All calls stay on this machine. Local CPU models are slow (~7 tokens/s for qwen3:4b), so keep runs small.</Notice>
              )}
              {estimate.warnings.map((w) => <Notice key={w} tone="warn">{w}</Notice>)}
              {missingKeys.length > 0 && <Notice tone="bad">Missing API key for {missingKeys.map((e) => e.name).join(", ")}.</Notice>}
            </div>
          )}
          {estError && <div className="mt-3"><ErrorBox message={estError} /></div>}
          <Button variant="primary" className="mt-4 w-full" disabled={!ready || !estimate || starting || missingKeys.length > 0} onClick={start}>
            {starting ? "Starting…" : "Start run"}
          </Button>
        </Card>
      </div>
    </>
  );
}
