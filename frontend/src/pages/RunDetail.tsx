import { useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router-dom";
import { api, type RunDetail, type RunItem, type RunModel, type RunResponse, type Verdict } from "../api";
import {
  Badge, Button, ButtonLink, Card, Empty, ErrorBox, Field, LocalBadge, Notice, PageHeader, ProgressBar, Spinner,
  StatusBadge, inputCls,
} from "../components/ui";
import { useAsync } from "../lib/hooks";
import { humanize, seconds, usd, when } from "../lib/format";

const ACTIVE = ["queued", "running", "judging", "cancelling"];
const FAILURE_TAGS = ["hallucination", "arithmetic_error", "wrong_reasoning", "ignored_instruction", "wrong_refusal", "unsafe"];

export function VerdictBadge({ v }: { v: Verdict }) {
  const tone = { correct: "ok", incorrect: "bad", pending: "warn", ungraded: "muted", error: "bad" }[v.status] as
    "ok" | "bad" | "warn" | "muted";
  const label = v.status === "error" ? "error" : v.status;
  const score = v.rubric?.score !== null && v.rubric?.score !== undefined ? ` ${(v.rubric.score * 100).toFixed(0)}%` : "";
  return <Badge tone={tone}>{label}{v.method === "rubric" ? score : ""}</Badge>;
}

function VerdictDetail({ v }: { v: Verdict }) {
  const d = v.details as Record<string, unknown>;
  return (
    <div className="space-y-1.5 text-xs text-ink-300">
      {v.method === "programmatic" && (
        <div className="flex flex-wrap gap-x-3">
          {"expected" in d && <span>expected <code className="text-ink-100">{String(d.expected)}</code></span>}
          {"extracted" in d && <span>extracted <code className="text-ink-100">{String(d.extracted)}</code></span>}
          {"refused" in d && <span>refused: {String(d.refused)} (should: {String(d.should_refuse)}) · heuristic{d.matched ? ` · matched “${String(d.matched)}”` : ""}</span>}
          {Array.isArray(d.checks) && (d.checks as { kind: string; passed: boolean; note: string }[]).map((c) => (
            <span key={c.kind} className={c.passed ? "text-emerald-300" : "text-rose-300"}>{c.passed ? "✓" : "✗"} {c.note}</span>
          ))}
          {d.answer_line_present === false && <span className="text-amber-300">no “Answer:” line</span>}
        </div>
      )}
      {v.rubric && (
        <ul className="space-y-0.5">
          {v.rubric.outcomes.map((o) => (
            <li key={o.criterion_id} className="flex flex-wrap gap-1.5">
              <span className={o.met === null ? "text-ink-500" : (o.kind === "penalty" ? !o.met : o.met) ? "text-emerald-300" : "text-rose-300"}>
                {o.met === null ? "○" : o.met ? "●" : "✗"}
              </span>
              <span>{o.kind}</span>
              <span className="text-ink-100">{o.text}</span>
              <span className="text-ink-500">({o.source}{o.note ? ` · ${o.note}` : ""})</span>
            </li>
          ))}
        </ul>
      )}
      {v.auto_tags.length > 0 && <div>auto tags: {v.auto_tags.map(humanize).join(", ")}</div>}
    </div>
  );
}

function TagEditor({ resp, onSaved }: { resp: RunResponse; onSaved: (tags: string[]) => void }) {
  const [tags, setTags] = useState(resp.tags);
  async function toggle(t: string) {
    const next = tags.includes(t) ? tags.filter((x) => x !== t) : [...tags, t];
    setTags(next);
    await api.put(`/responses/${resp.id}/tags`, { tags: next });
    onSaved(next);
  }
  return (
    <div className="flex flex-wrap gap-1" role="group" aria-label="Failure tags">
      {FAILURE_TAGS.map((t) => (
        <button key={t} type="button" aria-pressed={tags.includes(t)} onClick={() => toggle(t)}
          className={`rounded px-1.5 py-0.5 text-[11px] ring-1 ring-inset ${tags.includes(t) ? "bg-rose-500/20 text-rose-200 ring-rose-500/40" : "text-ink-400 ring-ink-700 hover:text-ink-200"}`}>
          {humanize(t)}
        </button>
      ))}
    </div>
  );
}

function ItemRow({ item, models, rep }: { item: RunItem; models: RunModel[]; rep: number }) {
  const [open, setOpen] = useState(false);
  const byModel = (m: RunModel) => item.responses.find((r) => r.run_model_id === m.id && r.repetition === rep);
  return (
    <li className="rounded-md border border-ink-700/70 bg-ink-900">
      <button type="button" aria-expanded={open} onClick={() => setOpen((o) => !o)}
        className="flex w-full flex-wrap items-center gap-2 px-3 py-2 text-left hover:bg-ink-850">
        <span className="w-20 shrink-0 font-mono text-xs text-indigo-300">{item.key}</span>
        <Badge tone="accent">{item.category}</Badge>
        <Badge>{item.difficulty}</Badge>
        <span className="min-w-0 flex-1 truncate text-sm text-ink-300">{item.prompt}</span>
        <span className="flex flex-wrap gap-1">
          {models.map((m) => {
            const r = byModel(m);
            return <span key={m.id} title={m.label}>{r ? <VerdictBadge v={r.verdict} /> : <Badge>…</Badge>}</span>;
          })}
        </span>
      </button>
      {open && (
        <div className="border-t border-ink-700/70 p-3">
          <pre className="response-text mb-3 rounded bg-ink-950 p-2 font-mono text-ink-300">{item.prompt}</pre>
          <div className={`grid gap-3 ${models.length > 2 ? "lg:grid-cols-3" : "lg:grid-cols-2"}`}>
            {models.map((m) => {
              const r = byModel(m);
              return (
                <div key={m.id} className="rounded border border-ink-700 p-2">
                  <div className="mb-1 flex flex-wrap items-center gap-2 text-xs">
                    <span className="font-medium text-ink-100">{m.label}</span>
                    <span className="text-ink-500">{m.role}</span>
                    {r && <VerdictBadge v={r.verdict} />}
                    {r && <span className="text-ink-500">{seconds(r.latency_ms)} · {r.output_tokens ?? "–"} tok{r.cached ? " · cached" : ""}</span>}
                    {r?.truncated && <Badge tone="warn" title="The model hit the max-token limit; the answer may be cut off">hit max tokens</Badge>}
                  </div>
                  {!r ? <p className="text-xs text-ink-500">Not run yet.</p> : r.status === "error" ? (
                    <Notice tone="bad">{r.error}</Notice>
                  ) : (
                    <>
                      <div className="response-text max-h-72 overflow-y-auto rounded bg-ink-950 p-2">{r.text}</div>
                      <div className="mt-2"><VerdictDetail v={r.verdict} /></div>
                      <div className="mt-2"><TagEditor resp={r} onSaved={(t) => (r.tags = t)} /></div>
                    </>
                  )}
                </div>
              );
            })}
          </div>
        </div>
      )}
    </li>
  );
}

export default function RunDetailPage() {
  const { id } = useParams();
  const runId = Number(id);
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const evidence = params.get("items")?.split(",").map(Number).filter(Boolean) ?? null;
  const why = params.get("why");
  const [category, setCategory] = useState("all");
  const [onlyDiff, setOnlyDiff] = useState(false);
  const [rep, setRep] = useState(0);

  const run = useAsync<RunDetail>(() => api.get(`/runs/${runId}`), [runId], {
    intervalMs: 2500, while: (d) => !!d && ACTIVE.includes(d.status),
  });
  const items = useAsync<RunItem[]>(() => api.get(`/runs/${runId}/items`), [runId, run.data?.done, run.data?.status]);

  const shown = useMemo(() => {
    let list = items.data ?? [];
    if (evidence) list = list.filter((i) => evidence.includes(i.id));
    if (category !== "all") list = list.filter((i) => i.category === category);
    if (onlyDiff)
      list = list.filter((i) => new Set(i.responses.filter((r) => r.repetition === rep).map((r) => r.verdict.status)).size > 1);
    return list;
  }, [items.data, evidence, category, onlyDiff, rep]);

  if (run.loading && !run.data) return <Spinner />;
  if (run.error) return <ErrorBox message={run.error} onRetry={run.reload} />;
  const r = run.data!;
  const active = ACTIVE.includes(r.status);

  async function act(path: string) {
    await api.post(`/runs/${runId}/${path}`);
    void run.reload();
  }
  async function remove() {
    if (!window.confirm("Delete this run and all its responses and votes?")) return;
    await api.del(`/runs/${runId}`);
    navigate("/history");
  }

  return (
    <>
      <PageHeader
        title={`Run #${r.id} · ${r.name}`}
        subtitle={<>{r.suite} v{r.suite_version} · {r.config.repetitions} repetition(s) · created {when(r.created_at)} · cost {usd(r.cost_usd)} (cap {usd(r.config.budget_usd, 2)})</>}
        actions={
          <>
            <ButtonLink to={`/report/${r.id}`} variant="primary">Gap report</ButtonLink>
            <ButtonLink to={`/review?run=${r.id}`}>Review</ButtonLink>
            {active && <Button onClick={() => act("cancel")}>Cancel</Button>}
            {["interrupted", "cancelled", "budget_exceeded", "failed"].includes(r.status) && <Button onClick={() => act("resume")}>Resume</Button>}
            {!active && <Button variant="ghost" onClick={remove}>Delete</Button>}
          </>
        }
      />
      <Card className="mb-4">
        <div className="flex flex-wrap items-center gap-3">
          <StatusBadge status={r.status} />
          <div className="min-w-[240px] flex-1">
            <ProgressBar value={r.done} total={r.total} label={active ? "responses (local models run one at a time)" : "responses"} />
          </div>
          {r.errors > 0 && <Badge tone="bad">{r.errors} errors</Badge>}
        </div>
        {r.error && <div className="mt-3"><Notice tone={r.status === "completed" ? "warn" : "bad"}>{r.error}</Notice></div>}
        <div className="mt-3 grid gap-2 md:grid-cols-2 xl:grid-cols-3">
          {r.models.map((m) => (
            <div key={m.id} className="rounded border border-ink-700 p-2 text-xs">
              <div className="flex flex-wrap items-center gap-2">
                <span className="text-sm font-medium text-ink-100">{m.label}</span>
                <Badge tone={m.role === "candidate" ? "accent" : "muted"}>{m.role}</Badge>
                <LocalBadge local={m.destination.local} />
              </div>
              <div className="mt-1 text-ink-400">
                {m.snapshot.provider} · <span className="font-mono">{m.snapshot.model}</span> · temp {String(m.snapshot.temperature)} · think {String(m.snapshot.think)} · max {String(m.snapshot.max_tokens)}
              </div>
              <div className="mt-1 truncate text-ink-500" title={m.snapshot.system_prompt}>system: {m.snapshot.system_prompt || "none"}</div>
            </div>
          ))}
        </div>
      </Card>

      {evidence && (
        <div className="mb-3">
          <Notice tone="info">
            Showing {shown.length} evidence item(s){why ? ` for: ${why}` : ""}.{" "}
            <button className="underline" onClick={() => setParams({})}>Show all items</button>
          </Notice>
        </div>
      )}
      <div className="mb-3 flex flex-wrap items-end gap-3">
        <Field label="Category" htmlFor="rd-cat">
          <select id="rd-cat" className={inputCls} value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="all">all</option>
            {[...new Set(items.data?.map((i) => i.category) ?? [])].map((c) => <option key={c}>{c}</option>)}
          </select>
        </Field>
        {r.config.repetitions > 1 && (
          <Field label="Repetition" htmlFor="rd-rep">
            <select id="rd-rep" className={inputCls} value={rep} onChange={(e) => setRep(Number(e.target.value))}>
              {Array.from({ length: r.config.repetitions }, (_, i) => <option key={i} value={i}>#{i + 1}</option>)}
            </select>
          </Field>
        )}
        <label className="flex items-center gap-2 pb-1.5 text-sm">
          <input type="checkbox" className="h-4 w-4 accent-indigo-500" checked={onlyDiff} onChange={(e) => setOnlyDiff(e.target.checked)} />
          Only items where models disagree
        </label>
        <span className="pb-1.5 text-xs text-ink-500">Verdict badges are in model order: {r.models.map((m) => m.label).join(" · ")}</span>
      </div>
      {items.loading && !items.data ? <Spinner /> : items.error ? <ErrorBox message={items.error} /> : shown.length === 0 ? (
        <Empty title="No items to show">{active ? "Responses will appear as the run progresses." : "Try clearing filters."}</Empty>
      ) : (
        <ul className="space-y-2">
          {shown.map((i) => <ItemRow key={i.id} item={i} models={r.models} rep={rep} />)}
        </ul>
      )}
      <p className="mt-4 text-xs text-ink-500">
        Refusal verdicts use a keyword heuristic. <Link className="underline" to="/review">Blind review</Link> resolves human-type criteria.
      </p>
    </>
  );
}
