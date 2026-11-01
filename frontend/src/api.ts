// Typed client for the Arbiter backend. All requests go through the Vite
// proxy at /api, so the browser only ever talks to its own origin.

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

function detailMessage(body: unknown, fallback: string): string {
  if (body && typeof body === "object" && "detail" in body) {
    const d = (body as { detail: unknown }).detail;
    if (typeof d === "string") return d;
    if (Array.isArray(d))
      return d
        .map((e: { loc?: string[]; msg?: string }) => `${(e.loc ?? []).slice(1).join(".")}: ${e.msg}`)
        .join("; ");
  }
  return fallback;
}

export async function request<T>(path: string, init: RequestInit = {}): Promise<T> {
  const res = await fetch(`/api${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...(init.headers ?? {}) },
  });
  if (res.status === 204) return undefined as T;
  const text = await res.text();
  let body: unknown = null;
  try {
    body = text ? JSON.parse(text) : null;
  } catch {
    body = text;
  }
  if (!res.ok) throw new ApiError(res.status, detailMessage(body, `Request failed (${res.status})`));
  return body as T;
}

export const api = {
  get: <T>(p: string) => request<T>(p),
  post: <T>(p: string, body?: unknown) =>
    request<T>(p, { method: "POST", body: body === undefined ? undefined : JSON.stringify(body) }),
  put: <T>(p: string, body: unknown) => request<T>(p, { method: "PUT", body: JSON.stringify(body) }),
  del: <T>(p: string) => request<T>(p, { method: "DELETE" }),
};

export function downloadUrl(path: string): string {
  return `/api${path}`;
}

// ---------------------------------------------------------------- types

export type Provider = "ollama" | "openai" | "anthropic" | "gemini" | "openai_compatible";

export interface KeyStatus {
  has_key: boolean;
  masked: string | null;
  store_error: boolean;
}

export interface Endpoint {
  id: number;
  name: string;
  provider: Provider;
  model: string;
  base_url: string;
  system_prompt: string;
  temperature: number;
  max_tokens: number;
  think: boolean | null;
  price_in: number;
  price_out: number;
  timeout_s: number;
  destination: { company: string; host: string; local: boolean };
  needs_key: boolean;
  key: KeyStatus;
}

export type EndpointInput = Omit<Endpoint, "id" | "destination" | "needs_key" | "key">;

export interface SuiteVersionMeta {
  id: number;
  suite_id: number;
  version: number;
  locked: number;
  notes: string;
  created_at: string;
  items: number;
}

export interface Suite {
  id: number;
  name: string;
  description: string;
  created_at: string;
  versions: SuiteVersionMeta[];
  latest_version_id: number | null;
}

export interface Constraint {
  kind: string;
  value?: unknown;
  required_keys?: string[] | null;
}

export interface Grader {
  type: string;
  answer?: unknown;
  aliases?: string[];
  tolerance?: number;
  relative?: boolean;
  pattern?: string | null;
  flags?: string;
  must_include?: string[];
  must_not_include?: string[];
  constraints?: Constraint[];
  should_refuse?: boolean | null;
  pass_threshold?: number;
  must_cap?: number;
}

export interface Criterion {
  id: number;
  key: string;
  text: string;
  kind: "must" | "nice" | "penalty";
  weight: number;
  check_type: "keywords" | "regex" | "human" | "judge";
  config: { keywords?: string[]; match?: "any" | "all"; pattern?: string };
  failure_tag: string | null;
}

export interface Item {
  id: number;
  key: string;
  category: string;
  difficulty: "easy" | "medium" | "hard";
  prompt: string;
  grader: Grader;
  open_ended: boolean;
  notes: string;
  criteria: Criterion[];
}

export interface SuiteVersionDetail {
  version: SuiteVersionMeta & { suite_name: string };
  items: Item[];
  runs: number;
}

export interface RunSummary {
  id: number;
  name: string;
  status: string;
  done: number;
  total: number;
  cost_usd: number;
  created_at: string;
  finished_at?: string | null;
  error?: string | null;
  suite?: string;
  suite_version?: number;
  models?: { id: number; role: string; label: string }[];
}

export interface RunModel {
  id: number;
  role: "candidate" | "baseline";
  label: string;
  endpoint_id: number | null;
  snapshot: Record<string, unknown> & { provider: Provider; model: string; system_prompt: string };
  destination: { company: string; host: string; local: boolean };
}

export interface RunDetail extends RunSummary {
  config: {
    repetitions: number;
    budget_usd: number;
    item_ids: number[];
    judge: { enabled: boolean; pairwise: boolean; endpoint_id: number | null };
  };
  models: RunModel[];
  suite: string;
  suite_version: number;
  errors: number;
  suite_version_id: number;
}

export interface CriterionOutcome {
  criterion_id: number;
  key: string;
  text: string;
  kind: string;
  weight: number;
  check_type: string;
  met: boolean | null;
  source: string;
  note: string;
  failure_tag: string | null;
}

export interface Verdict {
  status: "correct" | "incorrect" | "pending" | "ungraded" | "error";
  correct: boolean | null;
  method: string;
  details: Record<string, unknown>;
  rubric: {
    score: number | null;
    coverage: number | null;
    pending: boolean;
    must_missed: boolean;
    penalties: string[];
    outcomes: CriterionOutcome[];
  } | null;
  auto_tags: string[];
}

export interface RunResponse {
  id: number;
  run_model_id: number;
  repetition: number;
  status: string;
  text: string | null;
  error: string | null;
  latency_ms: number | null;
  input_tokens: number | null;
  output_tokens: number | null;
  cost_usd: number;
  cached: boolean;
  truncated: boolean;
  verdict: Verdict;
  tags: string[];
}

export interface RunItem {
  id: number;
  key: string;
  category: string;
  difficulty: string;
  prompt: string;
  grader: Grader;
  open_ended: boolean;
  responses: RunResponse[];
}

export interface Estimate {
  items: number;
  repetitions: number;
  endpoints: {
    endpoint_id: number;
    name: string;
    role: string;
    model: string;
    provider: string;
    company: string;
    host: string;
    local: boolean;
    calls: number;
    est_input_tokens: number;
    est_cost_typical: number;
    est_cost_max: number;
    key_missing: boolean;
  }[];
  total_typical: number;
  total_max: number;
  budget_usd: number;
  warnings: string[];
}

export interface Acc {
  k: number;
  n: number;
  acc: number | null;
  ci_low: number | null;
  ci_high: number | null;
  evidence: { correct: number[]; incorrect: number[] };
}

export interface Paired {
  n: number;
  counts: { both_right: number; only_candidate: number; only_baseline: number; both_wrong: number };
  evidence: { both_right: number[]; only_candidate: number[]; only_baseline: number[]; both_wrong: number[] };
  p_value: number;
  verdict: "better" | "worse" | "no_difference" | "insufficient";
  label: string;
  candidate_acc: number | null;
  baseline_acc: number | null;
  gap_points: number | null;
}

export interface ReportModel {
  id: number;
  label: string;
  role: "candidate" | "baseline";
  provider: string;
  model: string;
  local: boolean;
  company: string;
  system_prompt: string;
  temperature: number;
  think: boolean | null;
  max_tokens: number;
}

export interface VoteStats {
  total: number;
  voted: number;
  counts: { win: number; loss: number; tie: number; both_bad: number };
  win_rate: number | null;
  decisive_win_rate: number | null;
  ci_low: number | null;
  ci_high: number | null;
  sign_p: number;
  significant: boolean;
  reasons_when_candidate_won: Record<string, number>;
  reasons_when_candidate_lost: Record<string, number>;
  evidence: { win: number[]; loss: number[]; tie: number[]; both_bad: number[] };
}

export interface Report {
  run: {
    id: number;
    name: string;
    status: string;
    created_at: string;
    finished_at: string | null;
    suite: string;
    suite_version: number;
    repetitions: number;
    cost_usd: number;
    items: number;
  };
  models: ReportModel[];
  candidate_id: number;
  categories: string[];
  difficulties: string[];
  min_paired_n: number;
  accuracy: Record<string, { overall: Acc; by_category: Record<string, Acc>; by_difficulty: Record<string, Acc> }>;
  paired: Record<string, { overall: Paired; by_category: Record<string, Paired> }>;
  flips: Record<string, { regressions: number[]; improvements: number[] }>;
  votes: Record<string, VoteStats>;
  rubric: Record<string, {
    scored: number;
    mean_score: number | null;
    mean_coverage: number | null;
    pending_items: number[];
    excluded_criteria: number;
    most_missed: { key: string; text: string; kind: string; item_id: number; missed: number; checked: number }[];
    penalties: { key: string; text: string; item_id: number; count: number }[];
  }>;
  compliance: Record<string, {
    constraint_n: number;
    constraint_rate: number | null;
    format_n: number;
    format_rate: number | null;
    evidence: { constraint_failed: number[]; format_missing: number[] };
  }>;
  consistency: Record<string, { n: number; consistent: number; rate: number | null; evidence: { inconsistent: number[] } }>;
  failures: Record<string, { counts: Record<string, number>; evidence: Record<string, number[]> }>;
  efficiency: Record<string, {
    responses: number;
    errors: number;
    cached: number;
    latency_mean_ms: number | null;
    latency_median_ms: number | null;
    input_tokens: number;
    output_tokens: number;
    output_tokens_mean: number | null;
    cost_usd: number;
    correct_items: number;
    cost_per_correct: number | null;
    truncated: number;
    evidence: { errors: number[]; truncated: number[] };
  }>;
  judge: {
    enabled: boolean;
    min_n: number;
    min_kappa: number;
    criteria: { key: string; n: number; kappa: number | null; agreement: number | null; calibrated: boolean }[];
    pairwise: { judged: number; consistent: number; inconsistent_items: number[] };
  };
  review: { votes: { total: number; done: number }; criteria: { total: number; done: number } };
  items_index: Record<string, { key: string; category: string; difficulty: string; prompt: string }>;
  summary: { bullets: string[] };
}

export interface GateResult {
  passed: boolean;
  baseline: string;
  rules: { rule: string; passed: boolean; detail: string }[];
}

export interface HistoryRun {
  run_id: number;
  name: string;
  created_at: string;
  suite: string;
  suite_version: number;
  models: {
    run_model_id: number;
    label: string;
    role: string;
    model: string;
    acc: number | null;
    n: number;
    ci_low: number | null;
    ci_high: number | null;
    by_category: Record<string, number | null>;
  }[];
}

export interface Overview {
  counts: { runs: number; suites: number; endpoints: number };
  recent_runs: RunSummary[];
  endpoints: Endpoint[];
  suites: Suite[];
  review: { votes_pending: number; criteria_pending: number };
}

export interface Health {
  ok: boolean;
  version: string;
  ollama: { url: string; reachable: boolean; models: string[] };
}

export interface VoteTask {
  comparison_id: number;
  prompt: string;
  category: string;
  a_text: string;
  b_text: string;
}

export interface CriteriaTask {
  response_id: number;
  prompt: string;
  category: string;
  response_text: string;
  criteria: { id: number; text: string; kind: string }[];
}

export interface Progress {
  total: number;
  done: number;
}
