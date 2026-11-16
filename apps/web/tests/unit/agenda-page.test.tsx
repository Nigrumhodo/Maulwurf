// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";

vi.mock("@/components/calendar/CalendarView", () => ({
  CalendarView: () => <div data-testid="calendar-view" />,
}));

import { AgendaPage } from "@/pages/AgendaPage";

afterEach(() => {
  cleanup();
});

describe("AgendaPage", () => {
  it("renders the real calendar component", () => {
    render(<AgendaPage />);

    expect(screen.getByRole("heading", { name: "agenda" })).toBeTruthy();
    expect(screen.getByTestId("calendar-view")).toBeTruthy();
    expect(screen.getByText(/cada tarea enlazada se dibuja una sola vez/)).toBeTruthy();
  });
});
