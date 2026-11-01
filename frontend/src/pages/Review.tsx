import { useCallback, useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { api, type CriteriaTask, type Progress, type RunSummary, type VoteTask } from "../api";
import { Button, Card, Empty, ErrorBox, Field, Kbd, Notice, PageHeader, ProgressBar, Spinner, inputCls } from "../components/ui";
import { useAsync } from "../lib/hooks";

const REASONS = [
  { key: "more_accurate", label: "More accurate" },
  { key: "followed_instructions", label: "Followed instructions" },
  { key: "clearer", label: "Clearer" },
  { key: "hallucinated", label: "Other hallucinated" },
  { key: "unsafe", label: "Other unsafe" },
];

function isTyping(e: KeyboardEvent): boolean {
  const t = e.target as HTMLElement | null;
  return !!t && (t.tagName === "TEXTAREA" || t.tagName === "INPUT" || t.tagName === "SELECT" || t.isContentEditable);
}

export function VoteQueue({ runId }: { runId: number | null }) {
  const [task, setTask] = useState<VoteTask | null>(null);
  const [progress, setProgress] = useState<Progress>({ total: 0, done: 0 });
  const [tags, setTags] = useState<string[]>([]);
  const [note, setNote] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [flash, setFlash] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const q = runId ? `?run_id=${runId}` : "";
      const res = await api.get<{ task: VoteTask | null; progress: Progress }>(`/review/votes/next${q}`);
      setTask(res.task);
      setProgress(res.progress);
      setTags([]);
      setNote("");
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [runId]);
  useEffect(() => void load(), [load]);

  const vote = useCallback(async (choice: "left" | "right" | "tie" | "both_bad") => {
    if (!task) return;
    try {
      await api.post(`/review/votes/${task.comparison_id}`, { vote: choice, reason_tags: tags, note });
      setFlash({ left: "A better", right: "B better", tie: "Tie", both_bad: "Both bad" }[choice] + " recorded");
      void load();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }, [task, tags, note, load]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (isTyping(e) || e.ctrlKey || e.metaKey || e.altKey || !task) return;
      const k = e.key.toLowerCase();
      if (k === "a") void vote("left");
      else if (k === "b") void vote("right");
      else if (k === "t") void vote("tie");
      else if (k === "x") void vote("both_bad");
      else if (/^[1-5]$/.test(k)) {
        const r = REASONS[Number(k) - 1].key;
        setTags((ts) => (ts.includes(r) ? ts.filter((x) => x !== r) : [...ts, r]));
      } else return;
      e.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [task, vote]);

  if (loading && !task) return <Spinner />;
  if (error) return <ErrorBox message={error} onRetry={load} />;
  return (
    <div className="space-y-4">
      <ProgressBar value={progress.done} total={progress.total} label="pairs voted" />
      {flash && <p className="text-xs text-emerald-300" role="status">{flash}</p>}
      {!task ? (
        <Empty title={progress.total ? "All pairs voted. Nice work." : "No blind pairs yet"}>
          {progress.total ? "Results are in the gap report." : "Pairs are created for open-ended items when a run finishes."}
        </Empty>
      ) : (
        <>
          <Card title="Prompt" subtitle={`Category: ${task.category} · model names are hidden and positions are randomised`}>
            <pre className="response-text font-mono text-ink-200">{task.prompt}</pre>
          </Card>
          <div className="grid gap-4 lg:grid-cols-2">
            {([["A", task.a_text], ["B", task.b_text]] as const).map(([label, text]) => (
              <Card key={label} title={`Response ${label}`}>
                <div className="response-text max-h-[28rem] overflow-y-auto" aria-label={`Response ${label}`}>{text}</div>
              </Card>
            ))}
          </div>
          <Card>
            <fieldset className="mb-3">
              <legend className="mb-1.5 text-xs font-medium text-ink-300">Reasons (optional) — toggle with 1–5</legend>
              <div className="flex flex-wrap gap-1.5">
                {REASONS.map((r, i) => (
                  <button key={r.key} type="button" aria-pressed={tags.includes(r.key)}
                    onClick={() => setTags((ts) => (ts.includes(r.key) ? ts.filter((x) => x !== r.key) : [...ts, r.key]))}
                    className={`rounded px-2 py-1 text-xs ring-1 ring-inset ${tags.includes(r.key) ? "bg-indigo-500/20 text-indigo-100 ring-indigo-400/50" : "text-ink-300 ring-ink-600 hover:text-ink-100"}`}>
                    <Kbd>{i + 1}</Kbd> {r.label}
                  </button>
                ))}
              </div>
            </fieldset>
            <Field label="Note (optional)" htmlFor="vote-note">
              <textarea id="vote-note" rows={2} maxLength={1000} className={inputCls} value={note} onChange={(e) => setNote(e.target.value)} />
            </Field>
            <div className="mt-3 flex flex-wrap gap-2">
              <Button variant="primary" onClick={() => vote("left")}><Kbd>A</Kbd> A is better</Button>
              <Button variant="primary" onClick={() => vote("right")}><Kbd>B</Kbd> B is better</Button>
              <Button onClick={() => vote("tie")}><Kbd>T</Kbd> Tie</Button>
              <Button onClick={() => vote("both_bad")}><Kbd>X</Kbd> Both bad</Button>
            </div>
            <p className="mt-2 text-xs text-ink-500">"Other hallucinated / unsafe" describes the response you did <em>not</em> prefer (both, if you pick Both bad).</p>
          </Card>
        </>
      )}
    </div>
  );
}

export function CriteriaQueue({ runId }: { runId: number | null }) {
  const [task, setTask] = useState<CriteriaTask | null>(null);
  const [progress, setProgress] = useState<Progress>({ total: 0, done: 0 });
  const [labels, setLabels] = useState<Record<number, boolean>>({});
  const [focus, setFocus] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const q = runId ? `?run_id=${runId}` : "";
      const res = await api.get<{ task: CriteriaTask | null; progress: Progress }>(`/review/criteria/next${q}`);
      setTask(res.task);
      setProgress(res.progress);
      setLabels({});
      setFocus(0);
      setError(null);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setLoading(false);
    }
  }, [runId]);
  useEffect(() => void load(), [load]);

  const complete = task ? task.criteria.every((c) => c.id in labels) : false;
  const submit = useCallback(async () => {
    if (!task || !complete) return;
    await api.post(`/review/criteria/${task.response_id}`, {
      labels: task.criteria.map((c) => ({ criterion_id: c.id, met: labels[c.id] })),
    });
    void load();
  }, [task, complete, labels, load]);

  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (isTyping(e) || !task || e.ctrlKey || e.metaKey || e.altKey) return;
      const k = e.key.toLowerCase();
      const c = task.criteria[focus];
      if (k === "y" || k === "n") {
        if (!c) return;
        setLabels((l) => ({ ...l, [c.id]: k === "y" }));
        setFocus((f) => Math.min(task.criteria.length - 1, f + 1));
      } else if (k === "arrowdown" || k === "j") setFocus((f) => Math.min(task.criteria.length - 1, f + 1));
      else if (k === "arrowup" || k === "k") setFocus((f) => Math.max(0, f - 1));
      else if (k === "enter") void submit();
      else return;
      e.preventDefault();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [task, focus, submit]);

  if (loading && !task) return <Spinner />;
  if (error) return <ErrorBox message={error} onRetry={load} />;
  return (
    <div className="space-y-4">
      <ProgressBar value={progress.done} total={progress.total} label="responses checked" />
      {!task ? (
        <Empty title={progress.total ? "Checklist complete." : "Nothing to check"}>
          Human-type hidden criteria appear here after a run (judge-type ones too, for calibration).
        </Empty>
      ) : (
        <div className="grid gap-4 xl:grid-cols-2">
          <div className="space-y-4">
            <Card title="Prompt" subtitle={`Category: ${task.category}`}>
              <pre className="response-text font-mono text-ink-200">{task.prompt}</pre>
            </Card>
            <Card title="Response (model hidden)">
              <div className="response-text max-h-[28rem] overflow-y-auto">{task.response_text}</div>
            </Card>
          </div>
          <Card title="Does the response satisfy each statement?" subtitle="Penalty statements describe something bad: answer Yes if it happened.">
            <ul className="space-y-2">
              {task.criteria.map((c, i) => (
                <li key={c.id} className={`rounded border p-2 ${i === focus ? "border-indigo-400/60 bg-indigo-500/5" : "border-ink-700"}`}>
                  <div className="mb-1.5 flex items-start justify-between gap-2 text-sm">
                    <span>{c.text}</span>
                    <span className="text-[11px] uppercase text-ink-500">{c.kind}</span>
                  </div>
                  <div className="flex gap-2" role="radiogroup" aria-label={c.text}>
                    {[true, false].map((val) => (
                      <button key={String(val)} type="button" role="radio" aria-checked={labels[c.id] === val}
                        onClick={() => { setLabels((l) => ({ ...l, [c.id]: val })); setFocus(i); }}
                        className={`rounded px-2.5 py-1 text-xs ring-1 ring-inset ${labels[c.id] === val ? (val ? "bg-emerald-500/20 text-emerald-100 ring-emerald-400/50" : "bg-rose-500/20 text-rose-100 ring-rose-400/50") : "text-ink-300 ring-ink-600"}`}>
                        {val ? "Yes" : "No"}
                      </button>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
            <Button variant="primary" className="mt-3" disabled={!complete} onClick={submit}><Kbd>Enter</Kbd> Submit</Button>
            <p className="mt-2 text-xs text-ink-500"><Kbd>Y</Kbd>/<Kbd>N</Kbd> answer the highlighted statement, <Kbd>↑</Kbd>/<Kbd>↓</Kbd> move.</p>
          </Card>
        </div>
      )}
    </div>
  );
}

export default function ReviewPage() {
  const [params, setParams] = useSearchParams();
  const runParam = params.get("run");
  const runId = runParam ? Number(runParam) : null;
  const [tab, setTab] = useState<"votes" | "criteria">("votes");
  const runs = useAsync<RunSummary[]>(() => api.get("/runs"));
  return (
    <>
      <PageHeader title="Review" subtitle="Blind human evaluation. Model names are never shown here." />
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <div role="tablist" aria-label="Review type" className="flex rounded-md bg-ink-900 p-0.5 ring-1 ring-ink-700">
          {(["votes", "criteria"] as const).map((t) => (
            <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}
              className={`rounded px-3 py-1.5 text-sm ${tab === t ? "bg-indigo-500/20 text-indigo-100" : "text-ink-300 hover:text-ink-100"}`}>
              {t === "votes" ? "Blind A/B votes" : "Criteria checklist"}
            </button>
          ))}
        </div>
        <Field label="Run" htmlFor="rv-run">
          <select id="rv-run" className={inputCls} value={runId ?? ""} onChange={(e) => setParams(e.target.value ? { run: e.target.value } : {})}>
            <option value="">All runs</option>
            {runs.data?.map((r) => <option key={r.id} value={r.id}>#{r.id} {r.name}</option>)}
          </select>
        </Field>
      </div>
      {tab === "votes" ? (
        <>
          <div className="mb-3"><Notice tone="info">Shortcuts: <Kbd>A</Kbd> A better · <Kbd>B</Kbd> B better · <Kbd>T</Kbd> tie · <Kbd>X</Kbd> both bad · <Kbd>1</Kbd>–<Kbd>5</Kbd> reason tags</Notice></div>
          <VoteQueue key={`v${runId}`} runId={runId} />
        </>
      ) : (
        <CriteriaQueue key={`c${runId}`} runId={runId} />
      )}
    </>
  );
}
