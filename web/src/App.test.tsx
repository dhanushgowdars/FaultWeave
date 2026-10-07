// @vitest-environment jsdom

import "@testing-library/jest-dom/vitest";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import App from "./App";

class EventSourceStub {
  onopen: (() => void) | null = null;
  onerror: (() => void) | null = null;
  addEventListener() {}
  removeEventListener() {}
  close() {}
}

describe("FaultWeave app shell", () => {
  afterEach(() => vi.restoreAllMocks());

  it("renders the judge-facing overview", async () => {
    vi.stubGlobal("EventSource", EventSourceStub);
    vi.stubGlobal("fetch", vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input);
      const body = path.includes("incidents/current")
        ? { active: false, incident: null }
        : path.endsWith("/incidents")
          ? { count: 0, incidents: [] }
          : { status: "ready", known_class_count: 9 };
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "Content-Type": "application/json" },
      });
    }));
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={client}>
        <MemoryRouter>
          <App />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    expect(await screen.findByRole("heading", { name: /see the fault/i })).toBeInTheDocument();
    expect(screen.getByRole("link", { name: /run live investigation/i })).toHaveAttribute(
      "href",
      "/live",
    );
  });
});
