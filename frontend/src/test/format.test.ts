import { describe, expect, it } from "vitest";
import { pct, pts, pval, seconds, usd } from "../lib/format";
import { buildSeries } from "../pages/History";
import type { HistoryRun } from "../api";

describe("format helpers", () => {
  it("formats percentages, points and p-values", () => {
    expect(pct(0.8123)).toBe("81.2%");
    expect(pct(null)).toBe("–");
    expect(pts(-12.34)).toBe("-12.3 pts");
    expect(pts(5)).toBe("+5.0 pts");
    expect(pval(0.0004)).toBe("p<0.001");
    expect(pval(0.04567)).toBe("p=0.046");
  });
  it("formats money and durations", () => {
    expect(usd(0)).toBe("$0");
    expect(usd(0.12345)).toBe("$0.1235");
    expect(seconds(850)).toBe("850 ms");
    expect(seconds(12500)).toBe("12.5 s");
  });
});

describe("history series", () => {
  it("builds one line per endpoint label across runs", () => {
    const history: HistoryRun[] = [1, 2].map((id) => ({
      run_id: id, name: `r${id}`, created_at: "", suite: "S", suite_version: 1,
      models: [
        { run_model_id: id * 10, label: "v2", role: "candidate", model: "m", acc: id === 1 ? 0.5 : 0.7, n: 10, ci_low: 0.2, ci_high: 0.8, by_category: { math: 0.4 } },
        { run_model_id: id * 10 + 1, label: "v1", role: "baseline", model: "m", acc: 0.6, n: 10, ci_low: 0.3, ci_high: 0.85, by_category: { math: 0.9 } },
      ],
    }));
    const { series, xLabels } = buildSeries(history, "overall");
    expect(xLabels).toEqual(["#1", "#2"]);
    expect(series.map((s) => s.label)).toEqual(["v2", "v1"]);
    expect(series[0].points.map((p) => p.y)).toEqual([0.5, 0.7]);
    expect(buildSeries(history, "math").series[1].points[0].y).toBe(0.9);
  });
});
