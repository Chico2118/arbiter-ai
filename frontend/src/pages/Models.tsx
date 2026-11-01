import { useState, type FormEvent } from "react";
import { api, type Endpoint, type EndpointInput, type Provider } from "../api";
import {
  Badge, Button, Card, Empty, ErrorBox, Field, LocalBadge, Notice, PageHeader, Spinner, Table, inputCls, td, th,
} from "../components/ui";
import { useAsync } from "../lib/hooks";

const PROVIDERS: { value: Provider; label: string; hint: string }[] = [
  { value: "ollama", label: "Ollama (local)", hint: "Runs on this machine via http://localhost:11434" },
  { value: "openai", label: "OpenAI", hint: "api.openai.com · needs an API key · paid" },
  { value: "anthropic", label: "Anthropic", hint: "api.anthropic.com · needs an API key · paid" },
  { value: "gemini", label: "Google Gemini", hint: "generativelanguage.googleapis.com · needs an API key · paid" },
  { value: "openai_compatible", label: "OpenAI-compatible", hint: "Any server exposing /v1/chat/completions (LM Studio, vLLM, …)" },
];

const BLANK: EndpointInput = {
  name: "", provider: "ollama", model: "qwen3:4b", base_url: "", system_prompt: "", temperature: 0,
  max_tokens: 512, think: false, price_in: 0, price_out: 0, timeout_s: 300,
};

function toInput(e: Endpoint): EndpointInput {
  const { id: _id, destination: _d, needs_key: _n, key: _k, ...rest } = e;
  return rest;
}

export function EndpointForm({ initial, onSaved, onCancel }: {
  initial: Endpoint | null;
  onSaved: () => void;
  onCancel: () => void;
}) {
  const [form, setForm] = useState<EndpointInput>(initial ? toInput(initial) : BLANK);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const set = <K extends keyof EndpointInput>(k: K, v: EndpointInput[K]) => setForm((f) => ({ ...f, [k]: v }));
  const isOllama = form.provider === "ollama";

  async function submit(ev: FormEvent) {
    ev.preventDefault();
    setSaving(true);
    setError(null);
    try {
      const body = { ...form, think: isOllama ? form.think : null };
      if (initial) await api.put(`/endpoints/${initial.id}`, body);
      else await api.post("/endpoints", body);
      onSaved();
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setSaving(false);
    }
  }

  return (
    <form onSubmit={submit} className="space-y-4" aria-label={initial ? "Edit model endpoint" : "Add model endpoint"}>
      <div className="grid gap-4 md:grid-cols-3">
        <Field label="Name" htmlFor="ep-name" hint="Shown in reports, e.g. 'qwen3 v2 (strict prompt)'">
          <input id="ep-name" required maxLength={80} className={inputCls} value={form.name} onChange={(e) => set("name", e.target.value)} />
        </Field>
        <Field label="Provider" htmlFor="ep-provider" hint={PROVIDERS.find((p) => p.value === form.provider)?.hint}>
          <select id="ep-provider" className={inputCls} value={form.provider} onChange={(e) => set("provider", e.target.value as Provider)}>
            {PROVIDERS.map((p) => <option key={p.value} value={p.value}>{p.label}</option>)}
          </select>
        </Field>
        <Field label="Model" htmlFor="ep-model">
          <input id="ep-model" required maxLength={120} className={inputCls} value={form.model} onChange={(e) => set("model", e.target.value)} />
        </Field>
        <Field label="Base URL" htmlFor="ep-url" hint="Leave empty for the provider default">
          <input id="ep-url" className={inputCls} placeholder="https://…" value={form.base_url} onChange={(e) => set("base_url", e.target.value)} />
        </Field>
        <Field label="Temperature" htmlFor="ep-temp">
          <input id="ep-temp" type="number" min={0} max={2} step={0.1} className={inputCls} value={form.temperature} onChange={(e) => set("temperature", Number(e.target.value))} />
        </Field>
        <Field label="Max output tokens" htmlFor="ep-max">
          <input id="ep-max" type="number" min={1} max={32000} className={inputCls} value={form.max_tokens} onChange={(e) => set("max_tokens", Number(e.target.value))} />
        </Field>
        <Field label="Price per 1M input tokens (USD)" htmlFor="ep-pin">
          <input id="ep-pin" type="number" min={0} step={0.01} className={inputCls} value={form.price_in} onChange={(e) => set("price_in", Number(e.target.value))} />
        </Field>
        <Field label="Price per 1M output tokens (USD)" htmlFor="ep-pout">
          <input id="ep-pout" type="number" min={0} step={0.01} className={inputCls} value={form.price_out} onChange={(e) => set("price_out", Number(e.target.value))} />
        </Field>
        <Field label="Timeout (seconds)" htmlFor="ep-timeout" hint="Local CPU models are slow; 300 s is a safe default">
          <input id="ep-timeout" type="number" min={5} max={1800} className={inputCls} value={form.timeout_s} onChange={(e) => set("timeout_s", Number(e.target.value))} />
        </Field>
      </div>
      {isOllama && (
        <label className="flex items-center gap-2 text-sm text-ink-200">
          <input type="checkbox" checked={!!form.think} onChange={(e) => set("think", e.target.checked)} className="h-4 w-4 accent-indigo-500" />
          Enable thinking (<code className="text-xs">"think": true</code>). Off is much faster on CPU.
        </label>
      )}
      <Field label="System prompt" htmlFor="ep-system" hint="Part of the endpoint's identity: two endpoints with different system prompts are two 'versions'.">
        <textarea id="ep-system" rows={3} maxLength={8000} className={inputCls} value={form.system_prompt} onChange={(e) => set("system_prompt", e.target.value)} />
      </Field>
      {error && <ErrorBox message={error} />}
      <div className="flex gap-2">
        <Button type="submit" variant="primary" disabled={saving}>{saving ? "Saving…" : initial ? "Save changes" : "Add endpoint"}</Button>
        <Button onClick={onCancel}>Cancel</Button>
      </div>
    </form>
  );
}

function KeyManager({ ep, onChange }: { ep: Endpoint; onChange: () => void }) {
  const [value, setValue] = useState("");
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function save(ev: FormEvent) {
    ev.preventDefault();
    setBusy(true);
    setMsg(null);
    try {
      await api.put(`/endpoints/${ep.id}/key`, { api_key: value });
      setValue(""); // never keep the key in UI state longer than needed
      setMsg("Saved to the OS credential store.");
      onChange();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    } finally {
      setBusy(false);
    }
  }
  async function remove() {
    await api.del(`/endpoints/${ep.id}/key`);
    onChange();
  }
  return (
    <form onSubmit={save} className="flex flex-wrap items-end gap-2">
      <div className="min-w-[220px] flex-1">
        <label htmlFor={`key-${ep.id}`} className="sr-only">API key for {ep.name}</label>
        <input
          id={`key-${ep.id}`}
          type="password"
          autoComplete="off"
          spellCheck={false}
          placeholder={ep.key.has_key ? `Stored ${ep.key.masked ?? ""} · enter to replace` : "Paste API key"}
          className={inputCls}
          value={value}
          onChange={(e) => setValue(e.target.value)}
        />
      </div>
      <Button type="submit" disabled={busy || value.length < 8}>Save key</Button>
      {ep.key.has_key && <Button variant="ghost" onClick={remove}>Remove key</Button>}
      {msg && <span className="w-full text-xs text-ink-400" role="status">{msg}</span>}
    </form>
  );
}

export default function ModelsPage() {
  const { data, error, loading, reload } = useAsync<Endpoint[]>(() => api.get("/endpoints"));
  const [editing, setEditing] = useState<Endpoint | "new" | null>(null);
  const [tests, setTests] = useState<Record<number, { ok: boolean; message: string } | "pending">>({});

  async function test(ep: Endpoint) {
    setTests((t) => ({ ...t, [ep.id]: "pending" }));
    try {
      const res = await api.post<{ ok: boolean; message: string }>(`/endpoints/${ep.id}/test`);
      setTests((t) => ({ ...t, [ep.id]: res }));
    } catch (e) {
      setTests((t) => ({ ...t, [ep.id]: { ok: false, message: e instanceof Error ? e.message : String(e) } }));
    }
  }
  async function remove(ep: Endpoint) {
    if (!window.confirm(`Delete endpoint "${ep.name}"? Past runs keep their snapshot.`)) return;
    await api.del(`/endpoints/${ep.id}`);
    void reload();
  }

  return (
    <>
      <PageHeader
        title="Models"
        subtitle="Endpoints you can evaluate. Settings are snapshotted into every run for reproducibility."
        actions={<Button variant="primary" onClick={() => setEditing("new")}>Add endpoint</Button>}
      />
      <div className="mb-4">
        <Notice tone="info">
          API keys are stored in the Windows Credential Manager (via <code>keyring</code>), never in the database, logs or
          API responses. Only the last 4 characters are ever shown.
        </Notice>
      </div>
      {editing && (
        <Card title={editing === "new" ? "Add endpoint" : `Edit ${editing.name}`} className="mb-5">
          <EndpointForm
            initial={editing === "new" ? null : editing}
            onCancel={() => setEditing(null)}
            onSaved={() => {
              setEditing(null);
              void reload();
            }}
          />
        </Card>
      )}
      {loading && !data ? (
        <Spinner />
      ) : error ? (
        <ErrorBox message={error} onRetry={reload} />
      ) : !data?.length ? (
        <Empty title="No endpoints yet">
          Tip: add two Ollama endpoints for <code>qwen3:4b</code> with different system prompts to compare "v1" and "v2".
        </Empty>
      ) : (
        <div className="space-y-4">
          {data.map((ep) => {
            const t = tests[ep.id];
            return (
              <Card
                key={ep.id}
                title={
                  <span className="flex flex-wrap items-center gap-2">
                    {ep.name} <LocalBadge local={ep.destination.local} />
                    <Badge tone="muted">{ep.provider}</Badge>
                    {ep.needs_key && (ep.key.has_key ? <Badge tone="ok">key {ep.key.masked}</Badge> : <Badge tone="bad">no key</Badge>)}
                  </span>
                }
                subtitle={`Sends prompts to ${ep.destination.company} (${ep.destination.host})`}
                actions={
                  <>
                    <Button onClick={() => test(ep)} disabled={t === "pending"}>{t === "pending" ? "Testing…" : "Test connection"}</Button>
                    <Button variant="ghost" onClick={() => setEditing(ep)}>Edit</Button>
                    <Button variant="ghost" onClick={() => remove(ep)}>Delete</Button>
                  </>
                }
              >
                <Table>
                  <thead>
                    <tr>
                      <th className={th}>Model</th><th className={th}>Temp</th><th className={th}>Max tokens</th>
                      <th className={th}>Think</th><th className={th}>$/1M in · out</th><th className={th}>System prompt</th>
                    </tr>
                  </thead>
                  <tbody>
                    <tr>
                      <td className={`${td} font-mono text-xs`}>{ep.model}</td>
                      <td className={`${td} tabular-nums`}>{ep.temperature}</td>
                      <td className={`${td} tabular-nums`}>{ep.max_tokens}</td>
                      <td className={td}>{ep.think === null ? "default" : ep.think ? "on" : "off"}</td>
                      <td className={`${td} tabular-nums`}>{ep.price_in} · {ep.price_out}</td>
                      <td className={`${td} max-w-md truncate text-ink-300`} title={ep.system_prompt}>{ep.system_prompt || <span className="text-ink-500">none</span>}</td>
                    </tr>
                  </tbody>
                </Table>
                {t && t !== "pending" && (
                  <div className="mt-3"><Notice tone={t.ok ? "ok" : "bad"}>{t.message}</Notice></div>
                )}
                {(ep.needs_key || ep.provider === "openai_compatible") && (
                  <div className="mt-3"><KeyManager ep={ep} onChange={reload} /></div>
                )}
              </Card>
            );
          })}
        </div>
      )}
    </>
  );
}
