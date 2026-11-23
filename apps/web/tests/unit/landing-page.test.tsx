// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { App } from "@/App";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("LandingPage & HomeRoute (D1.8)", () => {
  it("renders public landing on `/` when anonymous without calling private endpoints", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input) === "/me") {
        return json({ error: { code: "auth_required", message: "Inicia sesión." } }, 401);
      }
      throw new Error(`unexpected private request: ${String(input)}`);
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/SUBE → ENCUENTRA → DECIDE/)).toBeTruthy();
    expect(screen.getByText(/contrato de privacidad \(g1\)/i)).toBeTruthy();
    const cta = screen.getByRole("link", { name: /entrar con google/i });
    expect(cta.getAttribute("href")).toBe("/auth/google/start");

    const urls = fetchMock.mock.calls.map(([input]) => String(input));
    expect(urls).toEqual(["/me"]);
  });

  it("renders authenticated dashboard on `/` when `/me` succeeds", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/me") {
        return json({
          id: "u-1",
          email: "ana@example.test",
          display_name: "Ana",
          timezone: "America/Bogota",
          csrf_token: "tok-1",
        });
      }
      if (url.startsWith("/audios") || url.startsWith("/tasks") || url.startsWith("/calendar")) {
        return json({ items: [], events: [], from: "2026-11-16", to: "2026-11-23", last_synced_at: null, coverage_ok: false, coverage_notice: "never_synced", stale: false });
      }
      return json({ items: [] });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/"]}>
        <App />
      </MemoryRouter>,
    );

    await waitFor(() =>
      expect(screen.getByRole("heading", { name: /hola, Ana/i })).toBeTruthy(),
    );
    expect(screen.queryByText(/SUBE → ENCUENTRA → DECIDE/)).toBeNull();
  });
});
