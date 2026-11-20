// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { SettingsPage } from "@/pages/SettingsPage";
import type { GoogleStatus } from "@/lib/integrations";

vi.mock("@/components/auth/AuthProvider", () => ({
  useAuth: () => ({
    user: { email: "ana@example.test", timezone: "America/Bogota" },
    loading: false,
    guest: false,
    enterAsGuest: () => {},
    logout: async () => {},
  }),
}));

function google(overrides: Partial<GoogleStatus> = {}): GoogleStatus {
  return {
    status: "disconnected",
    scopes: [],
    calendar_id: null,
    calendar_write_ready: false,
    token_expires_at: null,
    last_refresh_at: null,
    ...overrides,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function installFetch(status: GoogleStatus) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    if (url === "/integrations/status") return json({ google: status });
    return json({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

function LocationProbe() {
  const [params] = useSearchParams();
  return <output data-testid="params">{params.toString()}</output>;
}

function renderPage(entry: string) {
  return render(
    <MemoryRouter initialEntries={[entry]}>
      <SettingsPage />
      <LocationProbe />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("SettingsPage", () => {
  it("confirms a connection, consumes the query and resumes without confirming", async () => {
    installFetch(google({ status: "connected", calendar_id: "mw-1", calendar_write_ready: true }));

    renderPage("/settings?calendar=connected");

    expect(await screen.findByText(/google calendar conectado/)).toBeTruthy();
    expect(screen.getByText(/nada se confirmó automáticamente/)).toBeTruthy();
    expect(await screen.findByText("listo para escribir")).toBeTruthy();
    expect(screen.getByRole("link", { name: "reconectar google calendar" })).toBeTruthy();
    await waitFor(() => expect(screen.getByTestId("params").textContent).toBe(""));
  });

  it("explains a failed consent with a closed reason", async () => {
    installFetch(google());

    renderPage("/settings?calendar=error&reason=scope_required");

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("Faltan permisos de Calendar");
    expect(screen.queryByText(/nada se confirmó automáticamente/)).toBeNull();
  });

  it("does not echo an unknown reason", async () => {
    installFetch(google());

    renderPage("/settings?calendar=error&reason=<script>");

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("No se pudo conectar Google Calendar.");
    expect(alert.textContent).not.toContain("<script>");
  });

  it("shows the connect action when there is no query", async () => {
    installFetch(google());

    renderPage("/settings");

    expect(await screen.findByText("sin escritura lista")).toBeTruthy();
    expect(screen.getByRole("link", { name: "conectar google calendar" })).toBeTruthy();
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("reports a status read failure honestly", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => json({ error: { code: "internal_error" } }, 500)),
    );

    renderPage("/settings");

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText(/no se pudo comprobar el estado de Google Calendar/)).toBeTruthy();
  });
});
