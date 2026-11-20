// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useSearchParams } from "react-router-dom";
import { SettingsPage } from "@/pages/SettingsPage";
import type { GoogleStatus } from "@/lib/integrations";
import type { NotificationPreferences } from "@/lib/preferences";

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

function preferences(): NotificationPreferences {
  return {
    gmail_opt_in: false,
    digest_daily_enabled: true,
    digest_daily_time: "07:00",
    digest_weekly_enabled: true,
    digest_weekday: 0,
    reminder_offsets_hours: [48, 24, 2],
    reminder_allday_offsets_days: [3, 1],
    updated_at: null,
  };
}

function installFetch(status: GoogleStatus) {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    if (url === "/integrations/status") return json({ google: status });
    if (url === "/me/notifications") return json(preferences());
    if (url.startsWith("/notifications?")) return json({ items: [], next_cursor: null });
    if (url === "/me" && method === "PATCH") {
      const parsed = JSON.parse(String(init?.body ?? "{}")) as { timezone?: string };
      return json({
        id: "u-1",
        email: "ana@example.test",
        display_name: null,
        timezone: parsed.timezone ?? "America/Bogota",
        csrf_token: "tok-a",
      });
    }
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
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/me/notifications") return json(preferences());
        if (url.startsWith("/notifications?")) return json({ items: [], next_cursor: null });
        return json({ error: { code: "internal_error" } }, 500);
      }),
    );

    renderPage("/settings");

    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText(/no se pudo comprobar el estado de Google Calendar/)).toBeTruthy();
  });

  it("handles gmail consent notice and updates timezone via PATCH /me without display_name", async () => {
    const fetchMock = installFetch(
      google({
        status: "connected",
        scopes: ["https://www.googleapis.com/auth/gmail.send"],
      }),
    );

    renderPage("/settings?gmail=connected");

    expect(await screen.findByText(/permiso gmail\.send conectado/i)).toBeTruthy();
    expect(await screen.findByText("permiso gmail.send concedido")).toBeTruthy();

    const select = screen.getByLabelText(/cambiar zona horaria/i);
    fireEvent.change(select, { target: { value: "Europe/Madrid" } });
    fireEvent.click(screen.getByRole("button", { name: "guardar zona" }));

    await waitFor(() =>
      expect(screen.getByText(/zona horaria actualizada/i)).toBeTruthy(),
    );

    const patchCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === "/me" && init?.method === "PATCH",
    );
    expect(patchCall).toBeDefined();
    expect(JSON.parse(String(patchCall?.[1]?.body))).toEqual({ timezone: "Europe/Madrid" });
  });

  it("explains failed gmail consent with a closed reason", async () => {
    installFetch(google());

    renderPage("/settings?gmail=error&reason=scope_required");

    const alert = await screen.findByRole("alert");
    expect(alert.textContent).toContain("No se concedió el permiso gmail.send.");
  });
});
