import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import { CriteriaQueue, VoteQueue } from "../pages/Review";
import { mockFetch, renderAt } from "./helpers";

const TASK = {
  comparison_id: 7,
  prompt: "How should passwords be stored?",
  category: "explanation",
  a_text: "Use bcrypt with a salt.",
  b_text: "Store them in a text file.",
};

describe("blind A/B vote queue", () => {
  it("shows both answers without model identity and votes with the keyboard", async () => {
    let served = 0;
    const calls = mockFetch((url, init) => {
      if (url.includes("/review/votes/next")) {
        served += 1;
        return served === 1 ? { task: TASK, progress: { total: 2, done: 0 } } : { task: null, progress: { total: 2, done: 2 } };
      }
      if (init?.method === "POST") return { ok: true };
      throw new Error(`unexpected ${url}`);
    });
    renderAt(<VoteQueue runId={3} />);
    expect(await screen.findByText("Use bcrypt with a salt.")).toBeInTheDocument();
    expect(screen.getByText("Store them in a text file.")).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "Response A" })).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/candidate|baseline|qwen/i);

    const user = userEvent.setup();
    await user.keyboard("1"); // toggle "more accurate"
    expect(screen.getByRole("button", { name: /More accurate/ })).toHaveAttribute("aria-pressed", "true");
    await user.keyboard("a");
    await waitFor(() => expect(calls.some((c) => c.init?.method === "POST")).toBe(true));
    const post = calls.find((c) => c.init?.method === "POST")!;
    expect(post.url).toBe("/api/review/votes/7");
    expect(JSON.parse(String(post.init!.body))).toEqual({ vote: "left", reason_tags: ["more_accurate"], note: "" });
    expect(await screen.findByText(/All pairs voted/)).toBeInTheDocument();
    expect(calls[0].url).toContain("run_id=3");
  });

  it("does not trigger shortcuts while typing a note", async () => {
    const calls = mockFetch(() => ({ task: TASK, progress: { total: 1, done: 0 } }));
    renderAt(<VoteQueue runId={null} />);
    const note = await screen.findByLabelText(/Note/);
    await userEvent.setup().type(note, "ab");
    expect(calls.filter((c) => c.init?.method === "POST")).toHaveLength(0);
    expect(note).toHaveValue("ab");
  });

  it("shows an empty state when there is nothing to review", async () => {
    mockFetch(() => ({ task: null, progress: { total: 0, done: 0 } }));
    renderAt(<VoteQueue runId={null} />);
    expect(await screen.findByText("No blind pairs yet")).toBeInTheDocument();
  });
});

describe("blind criteria checklist", () => {
  it("labels criteria with Y/N and submits with Enter", async () => {
    const task = {
      response_id: 11, prompt: "Explain overfitting", category: "explanation", response_text: "It memorises training data.",
      criteria: [{ id: 1, text: "Gives two ways to reduce it", kind: "must" }, { id: 2, text: "Confuses with underfitting", kind: "penalty" }],
    };
    let first = true;
    const calls = mockFetch((_url, init) => {
      if (init?.method === "POST") return { ok: true };
      if (first) {
        first = false;
        return { task, progress: { total: 1, done: 0 } };
      }
      return { task: null, progress: { total: 1, done: 1 } };
    });
    renderAt(<CriteriaQueue runId={null} />);
    expect(await screen.findByText("It memorises training data.")).toBeInTheDocument();
    const user = userEvent.setup();
    await user.keyboard("n");
    await user.keyboard("n");
    await user.keyboard("{Enter}");
    await waitFor(() => expect(calls.some((c) => c.init?.method === "POST")).toBe(true));
    const post = calls.find((c) => c.init?.method === "POST")!;
    expect(post.url).toBe("/api/review/criteria/11");
    expect(JSON.parse(String(post.init!.body))).toEqual({
      labels: [{ criterion_id: 1, met: false }, { criterion_id: 2, met: false }],
    });
  });
});
