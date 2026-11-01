import { useEffect, useMemo, useRef, useState, type ChangeEvent, type FormEvent } from "react";
import { api, downloadUrl, type Criterion, type Item, type Suite, type SuiteVersionDetail } from "../api";
import {
  Badge, Button, Card, Empty, ErrorBox, Field, Notice, PageHeader, Spinner, Table, inputCls, td, th,
} from "../components/ui";
import { useAsync } from "../lib/hooks";
import { humanize } from "../lib/format";

const GRADER_TEMPLATES: Record<string, object> = {
  numeric: { answer: 42, tolerance: 0.01 },
  exact: { answer: "Paris", aliases: [] },
  choice: { answer: "B" },
  regex: { pattern: "\\bO\\(n\\)", flags: "i" },
  contains: { must_include: ["hash"], must_not_include: ["plain text"] },
  constraints: { constraints: [{ kind: "bullet_count", value: 3 }, { kind: "max_words", value: 60 }] },
  refusal: { should_refuse: true },
  rubric: { pass_threshold: 0.7, must_cap: 0.5 },
  none: {},
};

type CriterionDraft = {
  key?: string;
  text: string;
  kind: "must" | "nice" | "penalty";
  weight: number;
  check_type: "keywords" | "regex" | "human" | "judge";
  keywords: string;
  pattern: string;
  failure_tag: string;
};

function toDraft(c: Criterion): CriterionDraft {
  return {
    key: c.key, text: c.text, kind: c.kind, weight: c.weight, check_type: c.check_type,
    keywords: (c.config.keywords ?? []).join(", "), pattern: c.config.pattern ?? "", failure_tag: c.failure_tag ?? "",
  };
}

function graderLine(item: Item): string {
  const g = item.grader;
  switch (g.type) {
    case "numeric": return `numeric = ${g.answer} ± ${g.tolerance ?? 0}`;
    case "exact": return `exact = "${g.answer}"${g.aliases?.length ? ` (+${g.aliases.length} aliases)` : ""}`;
    case "choice": return `choice = ${g.answer}`;
    case "regex": return `regex /${g.pattern}/`;
    case "contains": return `contains ${JSON.stringify(g.must_include ?? [])}`;
    case "constraints": return (g.constraints ?? []).map((c) => `${c.kind}${c.value !== undefined && c.value !== null ? `=${c.value}` : ""}`).join(", ");
    case "refusal": return g.should_refuse ? "should refuse" : "should NOT refuse";
    case "rubric": return `hidden rubric (pass ≥ ${g.pass_threshold ?? 0.7})`;
    default: return g.type;
  }
}

function ItemEditor({ versionId, item, onSaved, onCancel }: {
  versionId: number;
  item: Item | null;
  onSaved: (newVersionId: number, created: boolean) => void;
  onCancel: () => void;
}) {
  const [key, setKey] = useState(item?.key ?? "");
  const [category, setCategory] = useState(item?.category ?? "math");
  const [difficulty, setDifficulty] = useState(item?.difficulty ?? "medium");
  const [prompt, setPrompt] = useState(item?.prompt ?? "");
  const [openEnded, setOpenEnded] = useState(item?.open_ended ?? false);
  const [gtype, setGtype] = useState(item?.grader.type ?? "numeric");
  const [gjson, setGjson] = useState(() => {
    if (!item) return JSON.stringify(GRADER_TEMPLATES.numeric, null, 2);
    const { type: _t, ...rest } = item.grader;
    return JSON.stringify(rest, null, 2);
  });
  const [criteria, setCriteria] = useState<CriterionDraft[]>(item?.criteria.map(toDraft) ?? []);
  const [error, setError] = useState<string | null>(null);

  function changeType(t: string) {
    setGtype(t);
    setGjson(JSON.stringify(GRADER_TEMPLATES[t] ?? {}, null, 2));
    if (t === "rubric") setOpenEnded(true);
  }
  const updateCrit = (i: number, patch: Partial<CriterionDraft>) =>
    setCriteria((cs) => cs.map((c, j) => (j === i ? { ...c, ...patch } : c)));

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    setError(null);
    let opts: object;
    try {
      opts = JSON.parse(gjson || "{}");
    } catch {
      setError("Grader options must be valid JSON");
      return;
    }
    const body = {
      key, category, difficulty, prompt, open_ended: openEnded, notes: item?.notes ?? "",
      grader: { type: gtype, ...opts },
      criteria: criteria.map((c) => ({
        ...(c.key ? { key: c.key } : {}),
        text: c.text, kind: c.kind, weight: c.weight, check_type: c.check_type,
        ...(c.check_type === "keywords" ? { keywords: c.keywords.split(",").map((s) => s.trim()).filter(Boolean) } : {}),
        ...(c.check_type === "regex" ? { pattern: c.pattern } : {}),
        ...(c.failure_tag ? { failure_tag: c.failure_tag } : {}),
      })),
    };
    try {
      const res = await api.put<{ version_id: number; new_version: boolean }>(`/suite-versions/${versionId}/items`, body);
      onSaved(res.version_id, res.new_version);
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" aria-label="Edit item">
      <div className="grid gap-3 md:grid-cols-4">
        <Field label="Key" htmlFor="it-key" hint="Letters, digits, - _ .">
          <input id="it-key" required pattern="[A-Za-z0-9_.\-]{1,64}" disabled={!!item} className={inputCls} value={key} onChange={(e) => setKey(e.target.value)} />
        </Field>
        <Field label="Category" htmlFor="it-cat">
          <input id="it-cat" required pattern="[a-z0-9_\-]+" className={inputCls} value={category} onChange={(e) => setCategory(e.target.value)} />
        </Field>
        <Field label="Difficulty" htmlFor="it-diff">
          <select id="it-diff" className={inputCls} value={difficulty} onChange={(e) => setDifficulty(e.target.value as Item["difficulty"])}>
            <option>easy</option><option>medium</option><option>hard</option>
          </select>
        </Field>
        <Field label="Grader" htmlFor="it-grader">
          <select id="it-grader" className={inputCls} value={gtype} onChange={(e) => changeType(e.target.value)}>
            {Object.keys(GRADER_TEMPLATES).map((t) => <option key={t}>{t}</option>)}
          </select>
        </Field>
      </div>
      <Field label="Prompt (sent to the models)" htmlFor="it-prompt" hint="For programmatic graders, ask for a final line 'Answer: …'.">
        <textarea id="it-prompt" required rows={4} maxLength={20000} className={`${inputCls} font-mono text-xs`} value={prompt} onChange={(e) => setPrompt(e.target.value)} />
      </Field>
      <div className="grid gap-3 md:grid-cols-2">
        <Field label="Grader options (JSON, never sent to models)" htmlFor="it-gjson">
          <textarea id="it-gjson" rows={5} className={`${inputCls} font-mono text-xs`} value={gjson} onChange={(e) => setGjson(e.target.value)} />
        </Field>
        <label className="flex items-start gap-2 pt-6 text-sm text-ink-200">
          <input type="checkbox" className="mt-0.5 h-4 w-4 accent-indigo-500" checked={openEnded} onChange={(e) => setOpenEnded(e.target.checked)} />
          <span>Open-ended: add to the blind A/B voting queue (candidate vs each baseline).</span>
        </label>
      </div>

      <div className="rounded-md border border-rose-500/30 bg-rose-500/5 p-3">
        <div className="mb-2 flex items-center justify-between">
          <h3 className="text-sm font-semibold text-rose-200">Hidden criteria · never shown to models</h3>
          <Button onClick={() => setCriteria((c) => [...c, { text: "", kind: "must", weight: 1, check_type: "keywords", keywords: "", pattern: "", failure_tag: "" }])}>
            Add criterion
          </Button>
        </div>
        {criteria.length === 0 && <p className="text-xs text-ink-400">No hidden criteria. Add atomic checks like "mentions salting (must)".</p>}
        <div className="space-y-2">
          {criteria.map((c, i) => (
            <fieldset key={i} className="grid gap-2 rounded border border-ink-700 p-2 md:grid-cols-12">
              <legend className="sr-only">Criterion {i + 1}</legend>
              <input aria-label="Criterion text" required placeholder="Atomic statement" className={`${inputCls} md:col-span-4`} value={c.text} onChange={(e) => updateCrit(i, { text: e.target.value })} />
              <select aria-label="Kind" className={`${inputCls} md:col-span-1`} value={c.kind} onChange={(e) => updateCrit(i, { kind: e.target.value as CriterionDraft["kind"] })}>
                <option value="must">must</option><option value="nice">nice</option><option value="penalty">penalty</option>
              </select>
              <input aria-label="Weight" type="number" min={0.1} step={0.5} className={`${inputCls} md:col-span-1`} value={c.weight} onChange={(e) => updateCrit(i, { weight: Number(e.target.value) })} />
              <select aria-label="Check type" className={`${inputCls} md:col-span-2`} value={c.check_type} onChange={(e) => updateCrit(i, { check_type: e.target.value as CriterionDraft["check_type"] })}>
                <option value="keywords">keywords</option><option value="regex">regex</option><option value="human">human checkbox</option><option value="judge">AI judge (optional)</option>
              </select>
              {c.check_type === "keywords" && (
                <input aria-label="Keywords (comma separated)" placeholder="hash, hashed" className={`${inputCls} md:col-span-3`} value={c.keywords} onChange={(e) => updateCrit(i, { keywords: e.target.value })} />
              )}
              {c.check_type === "regex" && (
                <input aria-label="Regex pattern" placeholder="bcrypt|argon2" className={`${inputCls} font-mono md:col-span-3`} value={c.pattern} onChange={(e) => updateCrit(i, { pattern: e.target.value })} />
              )}
              {(c.check_type === "human" || c.check_type === "judge") && <span className="self-center text-xs text-ink-400 md:col-span-3">Labelled blind in the Review queue</span>}
              <Button variant="ghost" className="md:col-span-1" onClick={() => setCriteria((cs) => cs.filter((_, j) => j !== i))} aria-label={`Remove criterion ${i + 1}`}>✕</Button>
            </fieldset>
          ))}
        </div>
      </div>
      {error && <ErrorBox message={error} />}
      <div className="flex gap-2">
        <Button type="submit" variant="primary">Save item</Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}

function ItemDetail({ item }: { item: Item }) {
  return (
    <div className="space-y-3">
      <div className="flex flex-wrap gap-2">
        <Badge tone="accent">{item.category}</Badge>
        <Badge>{item.difficulty}</Badge>
        <Badge tone="info">{item.grader.type}</Badge>
        {item.open_ended && <Badge tone="warn">blind vote</Badge>}
      </div>
      <div>
        <h3 className="mb-1 text-xs font-medium uppercase text-ink-400">Prompt (sent to models)</h3>
        <pre className="response-text rounded bg-ink-950 p-3 font-mono">{item.prompt}</pre>
      </div>
      <div>
        <h3 className="mb-1 text-xs font-medium uppercase text-ink-400">Grader & constraints (local only)</h3>
        <pre className="response-text rounded bg-ink-950 p-3 font-mono text-ink-300">{JSON.stringify(item.grader, (_k, v) => (v === null || (Array.isArray(v) && !v.length) || v === "" ? undefined : v), 2)}</pre>
      </div>
      {item.notes && <Notice tone="info">{item.notes}</Notice>}
      {item.criteria.length > 0 && (
        <div className="rounded-md border border-rose-500/30 bg-rose-500/5 p-3">
          <h3 className="mb-2 text-sm font-semibold text-rose-200">Hidden criteria · never shown to models</h3>
          <ul className="space-y-1.5 text-sm">
            {item.criteria.map((c) => (
              <li key={c.id} className="flex flex-wrap items-center gap-2">
                <Badge tone={c.kind === "must" ? "bad" : c.kind === "penalty" ? "warn" : "muted"}>{c.kind}</Badge>
                <span>{c.text}</span>
                <span className="text-xs text-ink-500">
                  w={c.weight} · {c.check_type}
                  {c.config.keywords ? `: ${c.config.keywords.join(", ")}` : ""}
                  {c.config.pattern ? `: /${c.config.pattern}/` : ""}
                  {c.failure_tag ? ` · tag ${humanize(c.failure_tag)}` : ""}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

export default function SuitesPage() {
  const suites = useAsync<Suite[]>(() => api.get("/suites"));
  const [suiteId, setSuiteId] = useState<number | null>(null);
  const [versionId, setVersionId] = useState<number | null>(null);
  const [category, setCategory] = useState("all");
  const [selected, setSelected] = useState<Item | null>(null);
  const [editing, setEditing] = useState<Item | "new" | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [newName, setNewName] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  const suite = suites.data?.find((s) => s.id === suiteId) ?? suites.data?.[0] ?? null;
  useEffect(() => {
    if (suite && (versionId === null || !suite.versions.some((v) => v.id === versionId))) setVersionId(suite.latest_version_id);
  }, [suite, versionId]);

  const detail = useAsync<SuiteVersionDetail | null>(
    () => (versionId ? api.get(`/suite-versions/${versionId}`) : Promise.resolve(null)),
    [versionId],
  );
  const categories = useMemo(() => [...new Set(detail.data?.items.map((i) => i.category) ?? [])], [detail.data]);
  const items = detail.data?.items.filter((i) => category === "all" || i.category === category) ?? [];

  async function onImport(ev: ChangeEvent<HTMLInputElement>) {
    const file = ev.target.files?.[0];
    ev.target.value = "";
    if (!file) return;
    if (file.size > 5 * 1024 * 1024) return setNotice("File is larger than 5 MB.");
    try {
      const suiteJson = JSON.parse(await file.text());
      const res = await api.post<{ suite_id: number }>("/suites/import", { filename: file.name, suite: suiteJson });
      setNotice(`Imported "${suiteJson.name}".`);
      await suites.reload();
      setSuiteId(res.suite_id);
    } catch (e) {
      setNotice(`Import failed: ${e instanceof Error ? e.message : String(e)}`);
    }
  }
  async function createSuite(ev: FormEvent) {
    ev.preventDefault();
    try {
      const res = await api.post<{ suite_id: number }>("/suites", { name: newName, items: [] });
      setCreating(false);
      setNewName("");
      await suites.reload();
      setSuiteId(res.suite_id);
    } catch (e) {
      setNotice(e instanceof Error ? e.message : String(e));
    }
  }
  async function deleteItem(item: Item) {
    if (!versionId || !window.confirm(`Delete item ${item.key}?`)) return;
    const res = await api.del<{ version_id: number; new_version: boolean }>(`/suite-versions/${versionId}/items/${encodeURIComponent(item.key)}`);
    if (res.new_version) setNotice(`v${detail.data?.version.version} is locked by a run, so a new version was created.`);
    setSelected(null);
    await suites.reload();
    setVersionId(res.version_id);
    void detail.reload();
  }

  if (suites.loading && !suites.data) return <Spinner />;
  if (suites.error) return <ErrorBox message={suites.error} onRetry={suites.reload} />;

  const v = detail.data?.version;
  return (
    <>
      <PageHeader
        title="Suites"
        subtitle="Benchmark items, their graders, and hidden rubric criteria. Versions lock when a run starts; edits create a new version."
        actions={
          <>
            <Button onClick={() => setCreating((c) => !c)}>New suite</Button>
            <Button onClick={() => fileRef.current?.click()}>Import JSON</Button>
            <input ref={fileRef} type="file" accept="application/json,.json" className="hidden" onChange={onImport} aria-label="Import suite JSON file" />
            {versionId && <a className="inline-flex items-center rounded-md bg-ink-800 px-3 py-1.5 text-sm ring-1 ring-inset ring-ink-600 hover:bg-ink-700" href={downloadUrl(`/suite-versions/${versionId}/export`)}>Export JSON</a>}
          </>
        }
      />
      {notice && <div className="mb-4"><Notice tone="info">{notice}</Notice></div>}
      {creating && (
        <Card title="New suite" className="mb-4">
          <form onSubmit={createSuite} className="flex flex-wrap items-end gap-2">
            <Field label="Name" htmlFor="suite-name">
              <input id="suite-name" required maxLength={80} className={inputCls} value={newName} onChange={(e) => setNewName(e.target.value)} />
            </Field>
            <Button type="submit" variant="primary">Create</Button>
          </form>
        </Card>
      )}
      <div className="mb-4 flex flex-wrap items-end gap-3">
        <Field label="Suite" htmlFor="suite-select">
          <select id="suite-select" className={inputCls} value={suite?.id ?? ""} onChange={(e) => { setSuiteId(Number(e.target.value)); setVersionId(null); setSelected(null); }}>
            {suites.data?.map((s) => <option key={s.id} value={s.id}>{s.name}</option>)}
          </select>
        </Field>
        <Field label="Version" htmlFor="version-select">
          <select id="version-select" className={inputCls} value={versionId ?? ""} onChange={(e) => { setVersionId(Number(e.target.value)); setSelected(null); }}>
            {suite?.versions.map((ver) => (
              <option key={ver.id} value={ver.id}>v{ver.version} · {ver.items} items{ver.locked ? " · locked" : ""} — {ver.notes}</option>
            ))}
          </select>
        </Field>
        <Field label="Category" htmlFor="cat-filter">
          <select id="cat-filter" className={inputCls} value={category} onChange={(e) => setCategory(e.target.value)}>
            <option value="all">all</option>
            {categories.map((c) => <option key={c}>{c}</option>)}
          </select>
        </Field>
        <Button onClick={() => { setEditing("new"); setSelected(null); }} disabled={!versionId}>Add item</Button>
        {v && (v.locked ? <Badge tone="warn">locked · {detail.data?.runs} run(s) use it</Badge> : <Badge tone="ok">editable draft</Badge>)}
      </div>
      {editing && versionId && (
        <Card title={editing === "new" ? "New item" : `Edit ${editing.key}`} className="mb-4"
          subtitle={v?.locked ? "This version is locked: saving creates a new version." : undefined}>
          <ItemEditor
            versionId={versionId}
            item={editing === "new" ? null : editing}
            onCancel={() => setEditing(null)}
            onSaved={async (vid, created) => {
              setEditing(null);
              setNotice(created ? "Saved as a new suite version (the previous one is locked by a run)." : "Item saved.");
              await suites.reload();
              setVersionId(vid);
              void detail.reload();
            }}
          />
        </Card>
      )}
      <div className="grid gap-4 xl:grid-cols-5">
        <Card title={`Items (${items.length})`} className="xl:col-span-3">
          {detail.loading && !detail.data ? <Spinner /> : detail.error ? <ErrorBox message={detail.error} /> : items.length === 0 ? (
            <Empty title="No items">Add an item or import a suite JSON.</Empty>
          ) : (
            <Table>
              <thead><tr><th className={th}>Key</th><th className={th}>Category</th><th className={th}>Diff.</th><th className={th}>Grader</th><th className={th}>Hidden</th></tr></thead>
              <tbody>
                {items.map((i) => (
                  <tr key={i.id} className={selected?.id === i.id ? "bg-indigo-500/10" : "hover:bg-ink-850"}>
                    <td className={td}>
                      <button className="font-mono text-xs text-indigo-300 hover:underline" onClick={() => { setSelected(i); setEditing(null); }}>{i.key}</button>
                    </td>
                    <td className={td}>{i.category}</td>
                    <td className={td}>{i.difficulty}</td>
                    <td className={`${td} max-w-xs truncate text-xs text-ink-300`} title={graderLine(i)}>{graderLine(i)}</td>
                    <td className={`${td} tabular-nums`}>{i.criteria.length || ""}</td>
                  </tr>
                ))}
              </tbody>
            </Table>
          )}
        </Card>
        <Card title={selected ? selected.key : "Item detail"} className="xl:col-span-2"
          actions={selected && (<><Button onClick={() => setEditing(selected)}>Edit</Button><Button variant="ghost" onClick={() => deleteItem(selected)}>Delete</Button></>)}>
          {selected ? <ItemDetail item={selected} /> : <p className="text-sm text-ink-400">Select an item to see its prompt, grader and hidden criteria.</p>}
        </Card>
      </div>
    </>
  );
}
