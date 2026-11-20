// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { GoogleReconnect } from "@/components/calendar/GoogleReconnect";
import type { GoogleStatus } from "@/lib/integrations";

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

afterEach(cleanup);

describe("GoogleReconnect", () => {
  it("offers connect when there is no grant", () => {
    render(<GoogleReconnect google={google()} />);
    expect(screen.getByText("disconnected")).toBeTruthy();
    expect(screen.getByText("sin escritura lista")).toBeTruthy();
    const link = screen.getByRole("link", { name: "conectar google calendar" });
    expect(link.getAttribute("href")).toBe("/integrations/google/connect");
  });

  it("offers reconnect when a calendar is bound but not write-ready", () => {
    render(
      <GoogleReconnect
        google={google({
          status: "connected",
          calendar_id: "mw-1",
          calendar_write_ready: false,
        })}
      />,
    );
    expect(screen.getByText(/no listo; quedará bloqueada/)).toBeTruthy();
    const link = screen.getByRole("link", { name: "reconectar google calendar" });
    expect(link.getAttribute("href")).toBe("/integrations/google/reconnect");
  });

  it("shows a ready target without pretending to write from this page", () => {
    render(
      <GoogleReconnect
        google={google({
          status: "connected",
          calendar_id: "mw-1",
          calendar_write_ready: true,
        })}
      />,
    );
    expect(screen.getByText("listo para escribir")).toBeTruthy();
    expect(screen.getByText("Maulwurf · mw-1")).toBeTruthy();
    expect(screen.getByRole("link", { name: "reconectar google calendar" })).toBeTruthy();
  });

  it("renders a compact banner for the task inbox", () => {
    render(<GoogleReconnect google={google()} compact />);
    expect(screen.getByRole("status")).toBeTruthy();
    expect(screen.getByText(/calendar no está listo/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "conectar google calendar" })).toBeTruthy();
  });
});
