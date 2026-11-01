import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { api, downloadUrl, type GateResult, type Paired, type Report, type RunSummary } from "../api";
import { CIBarChart, StackBar } from "../components/charts";
import {
  Badge, Button, Card, Empty, ErrorBox, Evidence, Field, LocalBadge, Notice, PageHeader, Spinner, Table, inputCls, td, th,
} from "../components/ui";
import { useAsync } from "../lib/hooks";
import { humanize, pct, pts, pval, seconds, usd } from "../lib/format";

const VERDICT_TONE = { worse: "bad", better: "ok", no_difference: "muted", insufficient: "warn" } as const;
const VERDICT_LABEL = { worse: "candidate worse", better: "candidate better", no_difference: "no significant difference", insufficient: "not enough data" };

export function SignificanceBadge({ p }: { p: Paired }) {
  return <Badge tone={VERDICT_TONE[p.verdict]} title={p.label}>{VERDICT_LABEL[p.verdict]}</Badge>;
}

function PairedTable({ rep, baselineId }: { rep: Report; baselineId: string }) {
  const pr = rep.paired[baselineId];
  const runId = rep.run.id;
  const rows = [...Object.entries(pr.by_category), ["overall", pr.overall] as [string, Paired]];
  const cell = (p: Paired, k: keyof Paired["counts"], label: string, cat: string) => (
    <Evidence runId={runId} ids={p.evidence[k]} label={`${cat}: ${label}`}>{p.counts[k]}</Evidence>
  );
  return (
    <Table>
      <thead>
        <tr>
          <th className={th}>Category</th><th className={th}>n</th><th className={`${th} w-40`}>Outcomes</th>
          <th className={th} title="both right">✓✓</th><th className={th} title="only candidate right">only cand.</th>
          <th className={th} title="only baseline right">only base.</th><th className={th} title="both wrong">✗✗</th>
          <th className={th}>Gap</th><th className={th}>McNemar</th><th className={th}>Verdict</th>
        </tr>
      </thead>
      <tbody>
        {rows.map(([cat, p]) => (
          <tr key={cat} className={cat === "overall" ? "font-medium" : ""}>
            <td className={td}>{cat}</td>
            <td className={`${td} tabular-nums`}>{p.n}</td>
            <td className={td}>
              <StackBar parts={[
                { label: "both right", value: p.counts.both_right, color: "#34d399" },
                { label: "only candidate", value: p.counts.only_candidate, color: "#818cf8" },
                { label: "only baseline", value: p.counts.only_baseline, color: "#f472b6" },
                { label: "both wrong", value: p.counts.both_wrong, color: "#475569" },
              ]} />
            </td>
            <td className={td}>{cell(p, "both_right", "both right", cat)}</td>
            <td className={td}>{cell(p, "only_candidate", "only candidate right (improvements)", cat)}</td>
            <td className={td}>{cell(p, "only_baseline", "only baseline right (regressions)", cat)}</td>
            <td className={td}>{cell(p, "both_wrong", "both wrong", cat)}</td>
            <td className={`${td} tabular-nums ${p.gap_points !== null && p.gap_points < 0 ? "text-rose-300" : p.gap_points ? "text-emerald-300" : ""}`}>{pts(p.gap_points)}</td>
            <td className={`${td} tabular-nums text-ink-300`}>{p.n ? pval(p.p_value) : "–"}</td>
            <td className={td}><SignificanceBadge p={p} /></td>
          </tr>
        ))}
      </tbody>
    </Table>
  );
}

function GatePanel({ rep }: { rep: Report }) {
  const baselines = rep.models.filter((m) => m.role === "baseline");
  const [baseline, setBaseline] = useState(baselines[0]?.id);
  const [maxDrop, setMaxDrop] = useState(5);
  const [minOverall, setMinOverall] = useState<string>("");
  const [noSig, setNoSig] = useState(true);
  const [noUnsafe, setNoUnsafe] = useState(true);
  const [result, setResult] = useState<GateResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  async function evaluate() {
    try {
      setResult(await api.post<GateResult>(`/runs/${rep.run.id}/gate`, {
        baseline_model_id: baseline, max_category_drop: maxDrop,
        min_overall: minOverall === "" ? null : Number(minOverall),
        no_significant_regression: noSig, max_unsafe: noUnsafe ? 0 : null,
      }));
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }
  return (
    <div className="space-y-3">
      <div className="grid gap-3 md:grid-cols-4">
        <Field label="Compare against" htmlFor="g-base">
          <select id="g-base" className={inputCls} value={baseline} onChange={(e) => setBaseline(Number(e.target.value))}>
            {baselines.map((b) => <option key={b.id} value={b.id}>{b.label}</option>)}
          </select>
        </Field>
        <Field label="Max category drop (points)" htmlFor="g-drop">
          <input id="g-drop" type="number" min={0} max={100} className={inputCls} value={maxDrop} onChange={(e) => setMaxDrop(Number(e.target.value))} />
        </Field>
        <Field label="Min overall accuracy % (optional)" htmlFor="g-min">
          <input id="g-min" type="number" min={0} max={100} className={inputCls} value={minOverall} onChange={(e) => setMinOverall(e.target.value)} />
        </Field>
        <div className="space-y-1 pt-5 text-sm">
          <label className="flex items-center gap-2"><input type="checkbox" className="h-4 w-4 accent-indigo-500" checked={noSig} onChange={(e) => setNoSig(e.target.checked)} />No significant regression</label>
          <label className="flex items-center gap-2"><input type="checkbox" className="h-4 w-4 accent-indigo-500" checked={noUnsafe} onChange={(e) => setNoUnsafe(e.target.checked)} />No unsafe responses</label>
        </div>
      </div>
      <Button variant="primary" onClick={evaluate}>Evaluate gate</Button>
      {error && <ErrorBox message={error} />}
      {result && (
        <div className="space-y-2">
          <Notice tone={result.passed ? "ok" : "bad"}>Release gate vs {result.baseline}: <strong>{result.passed ? "PASS" : "FAIL"}</strong></Notice>
          <ul className="space-y-1 text-sm">
            {result.rules.map((r) => (
              <li key={r.rule} className="flex flex-wrap gap-2">
                <Badge tone={r.passed ? "ok" : "bad"}>{r.passed ? "pass" : "fail"}</Badge>{r.rule} <span className="text-ink-400">— {r.detail}</span>
              </li>
            ))}
          </ul>
          <p className="text-xs text-ink-500">Same check in CI: <code>python -m arbiter.cli gate --run {rep.run.id} --max-drop {maxDrop}{noUnsafe ? " --no-unsafe" : ""}{noSig ? "" : " --allow-significant"}</code></p>
        </div>
      )}
    </div>
  );
}

export function ReportView({ rep }: { rep: Report }) {
  const runId = rep.run.id;
  const names = Object.fromEntries(rep.models.map((m) => [m.id, m.label]));
  const modelLabels = rep.models.map((m) => m.label);
  const cand = String(rep.candidate_id);
  const baselineIds = rep.models.filter((m) => m.role === "baseline").map((m) => String(m.id));
  const idx = rep.items_index;
  const groups = [...rep.categories, "overall"].map((cat) => ({
    label: cat,
    bars: rep.models.map((m) => {
      const a = cat === "overall" ? rep.accuracy[m.id].overall : rep.accuracy[m.id].by_category[cat];
      return { model: m.label, value: a.acc, low: a.ci_low, high: a.ci_high, n: a.n };
    }),
  }));
  const pendingReview = rep.review.votes.total - rep.review.votes.done + rep.review.criteria.total - rep.review.criteria.done;

  return (
    <div className="space-y-5">
      <div className="flex flex-wrap items-center gap-2">
        {rep.models.map((m) => (
          <span key={m.id} className="inline-flex items-center gap-1.5 rounded-md border border-ink-700 bg-ink-900 px-2 py-1 text-xs">
            <Badge tone={m.role === "candidate" ? "accent" : "muted"}>{m.role}</Badge> {m.label}
            <span className="font-mono text-ink-500">{m.model}</span> <LocalBadge local={m.local} />
          </span>
        ))}
        <span className="ml-auto flex flex-wrap gap-2">
          <a className="rounded-md bg-ink-800 px-3 py-1.5 text-sm ring-1 ring-inset ring-ink-600 hover:bg-ink-700" href={downloadUrl(`/runs/${runId}/report.md`)}>Export Markdown</a>
          <a className="rounded-md bg-ink-800 px-3 py-1.5 text-sm ring-1 ring-inset ring-ink-600 hover:bg-ink-700" href={downloadUrl(`/runs/${runId}/report.json`)}>Export JSON</a>
          <a className="rounded-md bg-ink-800 px-3 py-1.5 text-sm ring-1 ring-inset ring-ink-600 hover:bg-ink-700" href={downloadUrl(`/runs/${runId}/failed-items`)} title="Prompts the candidate failed, with this suite's own expected answers only">Export failed prompts</a>
        </span>
      </div>
      {rep.run.status !== "completed" && <Notice tone="warn">Run status is “{humanize(rep.run.status)}”; numbers cover only the responses collected so far.</Notice>}

      <Card title="Summary" subtitle="Generated deterministically from the numbers below (no AI). 'Lacks'/'better' only when McNemar p < 0.05 with enough paired items.">
        <ul className="list-disc space-y-1.5 pl-5 text-sm leading-relaxed text-ink-200">
          {rep.summary.bullets.map((b) => <li key={b}>{b}</li>)}
        </ul>
        {pendingReview > 0 && <p className="mt-3 text-sm"><Link to={`/review?run=${runId}`} className="text-indigo-300 underline">Open the review queue</Link> to finish {pendingReview} pending task(s).</p>}
      </Card>

      <div className="grid gap-5 2xl:grid-cols-2">
        <Card title="Accuracy by category" subtitle="Per item (repetitions combined), with Wilson 95% confidence intervals">
          <CIBarChart groups={groups} models={modelLabels} />
        </Card>
        <Card title="Accuracy table" subtitle="Click a number to see the items it counts">
          <Table>
            <thead><tr><th className={th}>Category</th>{rep.models.map((m) => <th key={m.id} className={th}>{m.label}</th>)}</tr></thead>
            <tbody>
              {[...rep.categories, "overall"].map((cat) => (
                <tr key={cat} className={cat === "overall" ? "font-medium" : ""}>
                  <td className={td}>{cat}</td>
                  {rep.models.map((m) => {
                    const a = cat === "overall" ? rep.accuracy[m.id].overall : rep.accuracy[m.id].by_category[cat];
                    if (!a.n) return <td key={m.id} className={`${td} text-ink-500`}>–</td>;
                    return (
                      <td key={m.id} className={`${td} tabular-nums`}>
                        <Evidence runId={runId} ids={a.evidence.incorrect} label={`${m.label} wrong on ${cat}`}>{pct(a.acc)}</Evidence>
                        <span className="ml-1 text-xs text-ink-500">{a.k}/{a.n} [{pct(a.ci_low, 0)}–{pct(a.ci_high, 0)}]</span>
                      </td>
                    );
                  })}
                </tr>
              ))}
            </tbody>
          </Table>
        </Card>
      </div>

      {baselineIds.map((bid) => (
        <Card key={bid} title={`Paired comparison: ${names[Number(cand)]} vs ${names[Number(bid)]}`}
          subtitle={`Exact McNemar test on discordant items; "significant" needs p < 0.05 and n ≥ ${rep.min_paired_n}.`}>
          <PairedTable rep={rep} baselineId={bid} />
          <div className="mt-4 grid gap-4 md:grid-cols-2">
            {(["regressions", "improvements"] as const).map((k) => (
              <div key={k}>
                <h3 className="mb-1.5 text-xs font-medium uppercase text-ink-400">
                  {k === "regressions" ? "Regressions (baseline right → candidate wrong)" : "Improvements (candidate right, baseline wrong)"} · {rep.flips[bid][k].length}
                </h3>
                {rep.flips[bid][k].length === 0 ? <p className="text-sm text-ink-500">None</p> : (
                  <ul className="space-y-1 text-sm">
                    {rep.flips[bid][k].map((iid) => (
                      <li key={iid} className="flex gap-2">
                        <Link to={`/runs/${runId}?items=${iid}&why=${k}`} className="font-mono text-xs text-indigo-300 hover:underline">{idx[iid]?.key}</Link>
                        <span className="truncate text-ink-400">{idx[iid]?.prompt}</span>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            ))}
          </div>
        </Card>
      ))}

      <div className="grid gap-5 xl:grid-cols-2">
        <Card title="Blind preference votes" subtitle="Human A/B votes on open-ended items; sign test excludes ties">
          {baselineIds.every((b) => !rep.votes[b].total) ? <Empty title="No open-ended items in this run" /> : (
            <div className="space-y-4">
              {baselineIds.map((bid) => {
                const v = rep.votes[bid];
                return (
                  <div key={bid}>
                    <div className="mb-1 flex flex-wrap items-center justify-between gap-2 text-sm">
                      <span>vs {names[Number(bid)]}</span>
                      <span className="text-xs text-ink-400">{v.voted}/{v.total} voted · {pval(v.sign_p)} {v.voted ? (v.significant ? <Badge tone="info">significant</Badge> : <Badge>not significant</Badge>) : null}</span>
                    </div>
                    <StackBar parts={[
                      { label: "candidate wins", value: v.counts.win, color: "#818cf8" },
                      { label: "ties", value: v.counts.tie, color: "#64748b" },
                      { label: "both bad", value: v.counts.both_bad, color: "#f59e0b" },
                      { label: "candidate losses", value: v.counts.loss, color: "#f472b6" },
                    ]} />
                    <div className="mt-1.5 flex flex-wrap gap-3 text-xs text-ink-300">
                      {(["win", "tie", "both_bad", "loss"] as const).map((k) => (
                        <span key={k}>{humanize(k)}: <Evidence runId={runId} ids={v.evidence[k]} label={`vote ${k} vs ${names[Number(bid)]}`}>{v.counts[k]}</Evidence></span>
                      ))}
                      <span>decisive win rate {pct(v.decisive_win_rate)} [{pct(v.ci_low, 0)}–{pct(v.ci_high, 0)}]</span>
                    </div>
                    {(Object.keys(v.reasons_when_candidate_lost).length > 0 || Object.keys(v.reasons_when_candidate_won).length > 0) && (
                      <div className="mt-1 text-xs text-ink-400">
                        Top reasons when it won: {Object.entries(v.reasons_when_candidate_won).map(([t, n]) => `${humanize(t)} (${n})`).join(", ") || "–"} ·
                        when it lost: {Object.entries(v.reasons_when_candidate_lost).map(([t, n]) => `${humanize(t)} (${n})`).join(", ") || "–"}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
        </Card>

        <Card title="Hidden-rubric coverage" subtitle="Weighted coverage minus penalties; a missed must-have caps the score">
          <Table>
            <thead><tr><th className={th}>Model</th><th className={th}>Mean score</th><th className={th}>Coverage</th><th className={th}>Scored</th><th className={th}>Pending</th></tr></thead>
            <tbody>
              {rep.models.map((m) => {
                const r = rep.rubric[m.id];
                return (
                  <tr key={m.id}>
                    <td className={td}>{m.label}</td>
                    <td className={`${td} tabular-nums`}>{pct(r.mean_score)}</td>
                    <td className={`${td} tabular-nums`}>{pct(r.mean_coverage)}</td>
                    <td className={`${td} tabular-nums`}>{r.scored}</td>
                    <td className={td}><Evidence runId={runId} ids={r.pending_items} label="awaiting human review">{r.pending_items.length}</Evidence></td>
                  </tr>
                );
              })}
            </tbody>
          </Table>
          <h3 className="mb-1 mt-4 text-xs font-medium uppercase text-ink-400">Criteria the candidate missed most</h3>
          {rep.rubric[cand].most_missed.length === 0 ? <p className="text-sm text-ink-500">None missed (or not yet reviewed).</p> : (
            <ul className="space-y-1 text-sm">
              {rep.rubric[cand].most_missed.map((c) => (
                <li key={`${c.item_id}-${c.key}`} className="flex flex-wrap gap-2">
                  <Badge tone={c.kind === "must" ? "bad" : "muted"}>{c.kind}</Badge>
                  <Evidence runId={runId} ids={[c.item_id]} label={`missed: ${c.text}`}>{c.missed}/{c.checked}</Evidence>
                  <span>{c.text}</span>
                  <span className="font-mono text-xs text-ink-500">{idx[c.item_id]?.key}</span>
                </li>
              ))}
            </ul>
          )}
          {rep.rubric[cand].penalties.length > 0 && (
            <>
              <h3 className="mb-1 mt-3 text-xs font-medium uppercase text-ink-400">Penalties triggered</h3>
              <ul className="space-y-1 text-sm">
                {rep.rubric[cand].penalties.map((p) => (
                  <li key={`${p.item_id}-${p.key}`}><Evidence runId={runId} ids={[p.item_id]} label={`penalty: ${p.text}`}>{p.count}×</Evidence> {p.text}</li>
                ))}
              </ul>
            </>
          )}
        </Card>
      </div>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card title="Instruction compliance, consistency, difficulty">
          <Table>
            <thead>
              <tr>
                <th className={th}>Model</th><th className={th}>Constraints met</th><th className={th}>“Answer:” format</th><th className={th}>Consistent across reps</th>
                {rep.difficulties.map((d) => <th key={d} className={th}>{d}</th>)}
              </tr>
            </thead>
            <tbody>
              {rep.models.map((m) => {
                const c = rep.compliance[m.id];
                const k = rep.consistency[m.id];
                return (
                  <tr key={m.id}>
                    <td className={td}>{m.label}</td>
                    <td className={`${td} tabular-nums`}><Evidence runId={runId} ids={c.evidence.constraint_failed} label="constraint failures">{pct(c.constraint_rate)}</Evidence> <span className="text-xs text-ink-500">n={c.constraint_n}</span></td>
                    <td className={`${td} tabular-nums`}><Evidence runId={runId} ids={c.evidence.format_missing} label="missing Answer: line">{pct(c.format_rate)}</Evidence> <span className="text-xs text-ink-500">n={c.format_n}</span></td>
                    <td className={`${td} tabular-nums`}>{k.n ? <Evidence runId={runId} ids={k.evidence.inconsistent} label="inconsistent across repetitions">{pct(k.rate)}</Evidence> : <span className="text-ink-500">1 rep</span>}</td>
                    {rep.difficulties.map((d) => {
                      const a = rep.accuracy[m.id].by_difficulty[d];
                      return <td key={d} className={`${td} tabular-nums`}>{pct(a.acc, 0)} <span className="text-xs text-ink-500">n={a.n}</span></td>;
                    })}
                  </tr>
                );
              })}
            </tbody>
          </Table>
        </Card>

        <Card title="Failure taxonomy" subtitle="Automatic tags (constraints, refusals, rubric penalties) + your manual tags + vote reasons">
          {rep.models.every((m) => !Object.keys(rep.failures[m.id].counts).length) ? <Empty title="No failure tags yet">Tag responses on the run page.</Empty> : (
            <Table>
              <thead><tr><th className={th}>Tag</th>{rep.models.map((m) => <th key={m.id} className={th}>{m.label}</th>)}</tr></thead>
              <tbody>
                {[...new Set(rep.models.flatMap((m) => Object.keys(rep.failures[m.id].counts)))].map((tag) => (
                  <tr key={tag}>
                    <td className={td}>{humanize(tag)}</td>
                    {rep.models.map((m) => (
                      <td key={m.id} className={`${td} tabular-nums`}>
                        <Evidence runId={runId} ids={rep.failures[m.id].evidence[tag] ?? []} label={`${m.label}: ${humanize(tag)}`}>{rep.failures[m.id].counts[tag] ?? 0}</Evidence>
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
      </div>

      <Card title="Efficiency" subtitle="Latency excludes cached responses. Cost per correct = spend / items answered correctly.">
        <Table>
          <thead><tr><th className={th}>Model</th><th className={th}>Responses</th><th className={th}>Errors</th><th className={th}>Hit max tokens</th><th className={th}>Cached</th><th className={th}>Median latency</th><th className={th}>Mean output tokens</th><th className={th}>Tokens in · out</th><th className={th}>Cost</th><th className={th}>Cost / correct</th></tr></thead>
          <tbody>
            {rep.models.map((m) => {
              const e = rep.efficiency[m.id];
              return (
                <tr key={m.id}>
                  <td className={td}>{m.label}</td>
                  <td className={`${td} tabular-nums`}>{e.responses}</td>
                  <td className={`${td} tabular-nums`}><Evidence runId={runId} ids={e.evidence.errors} label="errored responses">{e.errors}</Evidence></td>
                  <td className={`${td} tabular-nums`}><Evidence runId={runId} ids={e.evidence.truncated ?? []} label="responses cut off at max tokens">{e.truncated ?? 0}</Evidence></td>
                  <td className={`${td} tabular-nums`}>{e.cached}</td>
                  <td className={`${td} tabular-nums`}>{seconds(e.latency_median_ms)}</td>
                  <td className={`${td} tabular-nums`}>{e.output_tokens_mean === null ? "–" : e.output_tokens_mean.toFixed(0)}</td>
                  <td className={`${td} tabular-nums`}>{e.input_tokens} · {e.output_tokens}</td>
                  <td className={`${td} tabular-nums`}>{usd(e.cost_usd)}</td>
                  <td className={`${td} tabular-nums`}>{usd(e.cost_per_correct, 5)}</td>
                </tr>
              );
            })}
          </tbody>
        </Table>
      </Card>

      <div className="grid gap-5 xl:grid-cols-2">
        <Card title="AI judge calibration" subtitle={`Judge verdicts count only with n ≥ ${rep.judge.min_n} human-labelled pairs and Cohen's κ ≥ ${rep.judge.min_kappa}`}>
          {!rep.judge.criteria.length ? <p className="text-sm text-ink-500">No judge-type criteria in this run.</p> : (
            <Table>
              <thead><tr><th className={th}>Criterion</th><th className={th}>n</th><th className={th}>κ</th><th className={th}>Agreement</th><th className={th}>Status</th></tr></thead>
              <tbody>
                {rep.judge.criteria.map((c) => (
                  <tr key={c.key}>
                    <td className={`${td} font-mono text-xs`}>{c.key}</td>
                    <td className={`${td} tabular-nums`}>{c.n}</td>
                    <td className={`${td} tabular-nums`}>{c.kappa ?? "–"}</td>
                    <td className={`${td} tabular-nums`}>{pct(c.agreement)}</td>
                    <td className={td}>{c.calibrated ? <Badge tone="ok">calibrated</Badge> : <Badge tone="warn">uncalibrated · excluded</Badge>}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
          {rep.judge.pairwise.judged > 0 && (
            <p className="mt-3 text-sm">
              Pairwise judge: {rep.judge.pairwise.consistent}/{rep.judge.pairwise.judged} consistent after swapping positions.{" "}
              Inconsistent: <Evidence runId={runId} ids={rep.judge.pairwise.inconsistent_items} label="judge flipped when positions were swapped">{rep.judge.pairwise.inconsistent_items.length}</Evidence>
            </p>
          )}
          {!rep.judge.enabled && <p className="mt-2 text-xs text-ink-500">The judge was off for this run (default).</p>}
        </Card>
        <Card title="Release gate" subtitle="Pass/fail rules for shipping the candidate">
          <GatePanel rep={rep} />
        </Card>
      </div>
    </div>
  );
}

export default function ReportPage() {
  const { id } = useParams();
  const navigate = useNavigate();
  const runs = useAsync<RunSummary[]>(() => api.get("/runs"));
  const runId = id ? Number(id) : runs.data?.find((r) => r.done > 0)?.id ?? null;
  useEffect(() => {
    if (!id && runId) navigate(`/report/${runId}`, { replace: true });
  }, [id, runId, navigate]);
  const report = useAsync<Report | null>(() => (runId ? api.get(`/runs/${runId}/report`) : Promise.resolve(null)), [runId]);

  return (
    <>
      <PageHeader
        title="Gap report"
        subtitle={report.data ? `Run #${report.data.run.id} · ${report.data.run.name} · ${report.data.run.suite} v${report.data.run.suite_version} · ${report.data.run.items} items × ${report.data.run.repetitions}` : "Where does the candidate fall short, and is the difference real?"}
        actions={
          <Field label="Run" htmlFor="rep-run">
            <select id="rep-run" className={inputCls} value={runId ?? ""} onChange={(e) => navigate(`/report/${e.target.value}`)}>
              {runs.data?.map((r) => <option key={r.id} value={r.id}>#{r.id} {r.name} ({r.status})</option>)}
            </select>
          </Field>
        }
      />
      {runs.loading && !runs.data ? <Spinner /> : !runId ? (
        <Empty title="No runs with results yet"><Link to="/runs/new" className="text-indigo-300 underline">Start a run</Link></Empty>
      ) : report.loading && !report.data ? <Spinner label="Computing report" /> : report.error ? (
        <ErrorBox message={report.error} onRetry={report.reload} />
      ) : report.data ? <ReportView rep={report.data} /> : null}
    </>
  );
}
