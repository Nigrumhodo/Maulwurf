// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { CalendarFreshness } from "@/components/calendar/CalendarFreshness";

afterEach(() => {
  cleanup();
});

describe("CalendarFreshness", () => {
  it("shows a complete coverage state", () => {
    render(
      <CalendarFreshness
        lastSyncedAt="2026-11-03T12:00:00Z"
        coverageOk
        coverageNotice={null}
        stale={false}
      />,
    );

    expect(screen.getByText(/última sincronización/)).toBeTruthy();
    expect(screen.getByText(/cobertura: cobertura completa/)).toBeTruthy();
    expect(screen.queryByRole("status")).toBeNull();
  });

  it("warns when the mirror was never synced", () => {
    render(
      <CalendarFreshness
        lastSyncedAt={null}
        coverageOk={false}
        coverageNotice="never_synced"
        stale={false}
      />,
    );

    expect(screen.getAllByText(/nunca sincronizado/).length).toBeGreaterThan(0);
    expect(screen.getByRole("status").textContent).toContain("cobertura parcial");
  });

  it("marks stale data explicitly", () => {
    render(
      <CalendarFreshness
        lastSyncedAt="2026-11-03T12:00:00Z"
        coverageOk
        coverageNotice="stale"
        stale
      />,
    );

    expect(screen.getByRole("status").textContent).toContain("datos desactualizados");
  });
});
