import { render } from "@testing-library/react";
import type { ReactElement } from "react";
import { MemoryRouter } from "react-router-dom";
import { vi } from "vitest";

export type Route = (url: string, init?: RequestInit) => unknown;

/** Replace fetch with a router function; records every call. */
export function mockFetch(route: Route) {
  const calls: { url: string; init?: RequestInit }[] = [];
  vi.spyOn(globalThis, "fetch").mockImplementation(async (input, init) => {
    const url = String(input);
    calls.push({ url, init });
    const body = route(url, init);
    if (body instanceof Response) return body;
    return new Response(body === undefined ? "" : JSON.stringify(body), {
      status: body === undefined ? 204 : 200,
      headers: { "Content-Type": "application/json" },
    });
  });
  return calls;
}

export function renderAt(ui: ReactElement, path = "/") {
  return render(<MemoryRouter initialEntries={[path]} future={{ v7_startTransition: true, v7_relativeSplatPath: true }}>{ui}</MemoryRouter>);
}
