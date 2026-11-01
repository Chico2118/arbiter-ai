import { screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import type { Acc, Paired, Report } from "../api";
import { ReportView } from "../pages/Report";
import { renderAt } from "./helpers";

const acc = (k: number, n: number, wrong: number[] = []): Acc => ({
  k, n, acc: n ? k / n : null, ci_low: 0.1, ci_high: 0.9, evidence: { correct: [], incorrect: wrong },
});
const paired = (verdict: Paired["verdict"], onlyBase: number[], n = 10): Paired => ({
  n, counts: { both_right: n - onlyBase.length, only_candidate: 0, only_baseline: onlyBase.length, both_wrong: 0 },
  evidence: { both_right: [], only_candidate: [], only_baseline: onlyBase, both_wrong: [] },
  p_value: verdict === "worse" ? 0.002 : 1, verdict, label: verdict, candidate_acc: 0.5, baseline_acc: 1,
  gap_points: -onlyBase.length * 10,
});

function makeReport(): Report {
  const m = (id: number, role: "candidate" | "baseline", label: string) => ({
    id, label, role, provider: "ollama", model: "qwen3:4b", local: true, company: "Local", system_prompt: "", temperature: 0, think: false, max_tokens: 512,
  });
  const perModel = <T,>(v: T) => ({ "1": v, "2": v });
  return {
    run: { id: 5, name: "v2 vs v1", status: "completed", created_at: "", finished_at: "", suite: "Starter", suite_version: 1, repetitions: 1, cost_usd: 0, items: 12 },
    models: [m(1, "candidate", "qwen v2"), m(2, "baseline", "qwen v1")],
    candidate_id: 1,
    categories: ["math", "safety"],
    difficulties: ["easy"],
    min_paired_n: 10,
    accuracy: {
      "1": { overall: acc(6, 12, [101, 102]), by_category: { math: acc(5, 10, [101, 102]), safety: acc(1, 2) }, by_difficulty: { easy: acc(6, 12) } },
      "2": { overall: acc(12, 12), by_category: { math: acc(10, 10), safety: acc(2, 2) }, by_difficulty: { easy: acc(12, 12) } },
    },
    paired: { "2": { overall: paired("worse", [101, 102, 103, 104, 105], 12), by_category: { math: paired("worse", [101, 102, 103, 104, 105]), safety: paired("insufficient", [], 2) } } },
    flips: { "2": { regressions: [101, 102], improvements: [] } },
    votes: { "2": { total: 0, voted: 0, counts: { win: 0, loss: 0, tie: 0, both_bad: 0 }, win_rate: null, decisive_win_rate: null, ci_low: null, ci_high: null, sign_p: 1, significant: false, reasons_when_candidate_won: {}, reasons_when_candidate_lost: {}, evidence: { win: [], loss: [], tie: [], both_bad: [] } } },
    rubric: perModel({ scored: 0, mean_score: null, mean_coverage: null, pending_items: [], excluded_criteria: 0, most_missed: [], penalties: [] }),
    compliance: perModel({ constraint_n: 0, constraint_rate: null, format_n: 10, format_rate: 1, evidence: { constraint_failed: [], format_missing: [] } }),
    consistency: perModel({ n: 0, consistent: 0, rate: null, evidence: { inconsistent: [] } }),
    failures: perModel({ counts: {}, evidence: {} }),
    efficiency: perModel({ responses: 12, errors: 0, cached: 0, latency_mean_ms: 9000, latency_median_ms: 8000, input_tokens: 100, output_tokens: 200, output_tokens_mean: 16, cost_usd: 0, correct_items: 6, cost_per_correct: 0, truncated: 0, evidence: { errors: [], truncated: [] } }),
    judge: { enabled: false, min_n: 10, min_kappa: 0.6, criteria: [], pairwise: { judged: 0, consistent: 0, inconsistent_items: [] } },
    review: { votes: { total: 0, done: 0 }, criteria: { total: 0, done: 0 } },
    items_index: { "101": { key: "math-01", category: "math", difficulty: "easy", prompt: "p1" }, "102": { key: "math-02", category: "math", difficulty: "easy", prompt: "p2" } },
    summary: { bullets: ["Where it lacks: math vs qwen v1 (-50.0 pts, p=0.002)."] },
  } as unknown as Report;
}

describe("Gap report view", () => {
  it("renders summary, significance labels and evidence links", () => {
    renderAt(<ReportView rep={makeReport()} />);
    expect(screen.getByText(/Where it lacks: math/)).toBeInTheDocument();
    expect(screen.getAllByText("candidate worse").length).toBeGreaterThan(0);
    expect(screen.getByText("not enough data")).toBeInTheDocument();
    // Regressions list links to the evidence item on the run page.
    const link = screen.getByRole("link", { name: "math-01" });
    expect(link).toHaveAttribute("href", "/runs/5?items=101&why=regressions");
    // The 'only baseline' count for math links to its 5 items.
    expect(screen.getAllByTitle(/Show 5 evidence item/).length).toBeGreaterThan(0);
    expect(screen.getByRole("img", { name: /Accuracy by category/ })).toBeInTheDocument();
  });
});
