// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import type { Task } from "@/lib/tasks";

vi.mock("@fullcalendar/react", () => ({
  default: ({
    events,
  }: {
    events: Array<{ id: string; title: string }>;
  }) => (
    <div data-testid="fullcalendar">
      {events.map((event) => (
        <span key={event.id} data-testid="fc-event">
          {event.title}
        </span>
      ))}
    </div>
  ),
}));

import { CalendarView } from "@/components/calendar/CalendarView";

const LINKED_TASK: Task = {
  id: "t1",
  audio_id: null,
  subject: { id: "s1", name: "cálculo" },
  source: "manual",
  tipo: "tarea",
  titulo: "Tarea enlazada",
  detalle: null,
  due_date: null,
  due_at: null,
  timezone: "America/Bogota",
  all_day: false,
  date_status: "missing",
  date_text: null,
  date_reason: null,
  confidence_score: null,
  status: "confirmed",
  calendar_sync_status: "synced",
  calendar_id: "cal-1",
  calendar_event_id: "evt-1",
  version: 1,
  due_version: 1,
  event_block: {
    start_at: "2026-11-09T14:00:00Z",
    end_at: "2026-11-09T15:00:00Z",
    start_date: null,
    end_date_exclusive: null,
    duration_default_editable: true,
  },
  sources: [],
  evidence: [],
  pending_revision: null,
};

const INDEPENDENT_TASK: Task = {
  ...LINKED_TASK,
  id: "t2",
  titulo: "Tarea independiente",
  calendar_id: null,
  calendar_event_id: null,
  event_block: {
    ...LINKED_TASK.event_block,
    start_at: "2026-11-10T14:00:00Z",
    end_at: "2026-11-10T15:00:00Z",
  },
};

const EVENTS = {
  events: [
    {
      id: "e1",
      calendar_id: "cal-1",
      event_id: "evt-1",
      title: "Tarea enlazada",
      source: "maulwurf",
      start_at: "2026-11-09T14:00:00Z",
      end_at: "2026-11-09T15:00:00Z",
      start_date: null,
      end_date_exclusive: null,
      timezone: "America/Bogota",
      remote_status: "confirmed",
      last_synced_at: "2026-11-03T12:00:00Z",
    },
    {
      id: "e2",
      calendar_id: "cal-1",
      event_id: "google-1",
      title: "Evento Google",
      source: "google",
      start_at: "2026-11-11T14:00:00Z",
      end_at: "2026-11-11T15:00:00Z",
      start_date: null,
      end_date_exclusive: null,
      timezone: "America/Bogota",
      remote_status: "confirmed",
      last_synced_at: "2026-11-03T12:00:00Z",
    },
  ],
  from: "2026-11-01",
  to: "2026-11-30",
  last_synced_at: "2026-11-03T12:00:00Z",
  coverage_ok: true,
  coverage_notice: null,
  stale: false,
};

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("CalendarView", () => {
  it("draws a linked task once, plus Google and independent tasks", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/calendar/events?")) return json(EVENTS);
      if (url.startsWith("/tasks?")) {
        return json({ items: [LINKED_TASK, INDEPENDENT_TASK], next_cursor: null });
      }
      if (url === "/subjects") {
        return json({
          items: [
            {
              id: "s1",
              name: "cálculo",
              color: "#ffcc00",
              teacher: null,
              class_count: 0,
            },
          ],
        });
      }
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CalendarView compact />);

    await waitFor(() => {
      expect(screen.getAllByTestId("fc-event")).toHaveLength(3);
    });
    const titles = screen.getAllByTestId("fc-event").map((item) => item.textContent);
    expect(titles.filter((title) => title === "Tarea enlazada")).toHaveLength(1);
    expect(titles).toContain("Evento Google");
    expect(titles).toContain("Tarea independiente");
    expect(screen.getByText(/última sincronización/)).toBeTruthy();
    expect(screen.getByText(/cobertura completa/)).toBeTruthy();
  });

  it("keeps Google events visible when the task request fails", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith("/calendar/events?")) return json(EVENTS);
      if (url.startsWith("/tasks?")) {
        return new Response(JSON.stringify({ error: { code: "internal_error" } }), {
          status: 500,
          headers: { "content-type": "application/json" },
        });
      }
      if (url === "/subjects") return json({ items: [] });
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<CalendarView compact />);

    expect(await screen.findByText(/solo se muestran eventos de Google/)).toBeTruthy();
    expect(await screen.findByText("Evento Google")).toBeTruthy();
  });
});
