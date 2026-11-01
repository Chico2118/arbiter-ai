import { screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";
import ModelsPage from "../pages/Models";
import { mockFetch, renderAt } from "./helpers";

const EP = {
  id: 1, name: "GPT baseline", provider: "openai", model: "gpt-4o-mini", base_url: "", system_prompt: "",
  temperature: 0, max_tokens: 512, think: null, price_in: 0.15, price_out: 0.6, timeout_s: 60,
  destination: { company: "OpenAI", host: "api.openai.com", local: false }, needs_key: true,
  key: { has_key: false, masked: null, store_error: false },
};

describe("Models page", () => {
  it("marks paid endpoints, saves a key without ever displaying it", async () => {
    const secret = "sk-test-SYNTHETIC-abcdef123456";
    let hasKey = false;
    const calls = mockFetch((url, init) => {
      if (url === "/api/endpoints/1/key" && init?.method === "PUT") {
        hasKey = true;
        return { has_key: true, masked: "••••3456", store_error: false };
      }
      if (url === "/api/endpoints") return [{ ...EP, key: hasKey ? { has_key: true, masked: "••••3456", store_error: false } : EP.key }];
      throw new Error(url);
    });
    renderAt(<ModelsPage />);
    expect(await screen.findByText("paid · external")).toBeInTheDocument();
    expect(screen.getByText("no key")).toBeInTheDocument();
    expect(screen.getByText(/Sends prompts to OpenAI/)).toBeInTheDocument();

    const input = screen.getByLabelText("API key for GPT baseline");
    expect(input).toHaveAttribute("type", "password");
    const user = userEvent.setup();
    await user.type(input, secret);
    await user.click(screen.getByRole("button", { name: "Save key" }));
    await waitFor(() => expect(screen.getByText("key ••••3456")).toBeInTheDocument());
    expect(screen.getByLabelText("API key for GPT baseline")).toHaveValue("");
    expect(document.body.innerHTML).not.toContain(secret);
    const put = calls.find((c) => c.init?.method === "PUT")!;
    expect(JSON.parse(String(put.init!.body))).toEqual({ api_key: secret });
  });

  it("shows a helpful empty state", async () => {
    mockFetch(() => []);
    renderAt(<ModelsPage />);
    expect(await screen.findByText("No endpoints yet")).toBeInTheDocument();
  });

  it("surfaces backend errors", async () => {
    mockFetch(() => new Response(JSON.stringify({ detail: "boom" }), { status: 500 }));
    renderAt(<ModelsPage />);
    expect(await screen.findByRole("alert")).toHaveTextContent("boom");
  });
});
