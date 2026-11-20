import { describe, expect, it } from "vitest";
import { buildCalendarEvents, calendarCoverageLabel, formatLastSyncedAt } from "@/lib/calendar";
import type { CalendarMirrorEvent } from "@/lib/calendar";
import type { Task } from "@/lib/tasks";

const COLORS = new Map([["s1", "#ffcc00"]]);

function task(overrides: Partial<Task> = {}): Task {
  return {
    id: "t1",
    audio_id: null,
    subject: { id: "s1", name: "cálculo" },
    source: "manual",
    tipo: "tarea",
    titulo: "Entrega",
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
    ...overrides,
  };
}

function event(overrides: Partial<CalendarMirrorEvent> = {}): CalendarMirrorEvent {
  return {
    id: "e1",
    calendar_id: "cal-1",
    event_id: "evt-1",
    title: "Entrega",
    source: "maulwurf",
    start_at: "2026-11-09T14:00:00Z",
    end_at: "2026-11-09T15:00:00Z",
    start_date: null,
    end_date_exclusive: null,
    timezone: "America/Bogota",
    remote_status: "confirmed",
    last_synced_at: "2026-11-03T12:00:00Z",
    ...overrides,
  };
}

describe("calendar merge", () => {
  it("draws a linked task once and skips its mirror event", () => {
    const result = buildCalendarEvents([event()], [task()], COLORS);

    expect(result.events).toHaveLength(1);
    expect(result.events[0]?.id).toBe("task:t1");
    expect(result.events[0]?.backgroundColor).toBe("#ffcc00");
    expect(result.skippedLinkedEvents).toBe(1);
    expect(result.skippedByBlock).toBe(0);
  });

  it("uses the block as fallback when the task has no event id", () => {
    const result = buildCalendarEvents(
      [event({ calendar_id: "cal-2", event_id: "other" })],
      [task({ calendar_id: null, calendar_event_id: null })],
      COLORS,
    );

    expect(result.events).toHaveLength(1);
    expect(result.events[0]?.id).toBe("task:t1");
    expect(result.skippedByBlock).toBe(1);
  });

  it("keeps unrelated Google events and independent tasks", () => {
    const result = buildCalendarEvents(
      [
        event({
          event_id: "google-1",
          title: "Clase",
          start_at: "2026-11-09T16:00:00Z",
          end_at: "2026-11-09T17:00:00Z",
        }),
      ],
      [task({ calendar_id: null, calendar_event_id: null })],
      COLORS,
    );

    expect(result.events.map((item) => item.id)).toEqual([
      "task:t1",
      "google:cal-1:google-1",
    ]);
  });

  it("does not hide the Google event of a closed task", () => {
    const result = buildCalendarEvents(
      [event()],
      [task({ status: "completed" })],
      COLORS,
    );

    expect(result.events.map((item) => item.id)).toEqual(["google:cal-1:evt-1"]);
  });

  it("counts tasks without a block and skips cancelled mirror rows", () => {
    const result = buildCalendarEvents(
      [event({ remote_status: "cancelled" })],
      [
        task({
          event_block: {
            start_at: null,
            end_at: null,
            start_date: null,
            end_date_exclusive: null,
            duration_default_editable: true,
          },
        }),
      ],
      COLORS,
    );

    expect(result.events).toEqual([]);
    expect(result.tasksWithoutBlock).toBe(1);
    expect(result.skippedCancelled).toBe(1);
  });

  it("maps all-day tasks and Google events without inventing times", () => {
    const allDayTask = task({
      all_day: true,
      calendar_id: null,
      calendar_event_id: null,
      event_block: {
        start_at: null,
        end_at: null,
        start_date: "2026-11-10",
        end_date_exclusive: "2026-11-11",
        duration_default_editable: true,
      },
    });
    const allDayEvent = event({
      calendar_id: "cal-2",
      event_id: "all-day",
      start_at: null,
      end_at: null,
      start_date: "2026-11-12",
      end_date_exclusive: "2026-11-13",
    });

    const result = buildCalendarEvents([allDayEvent], [allDayTask], COLORS);

    expect(result.events[0]).toMatchObject({
      start: "2026-11-10",
      end: "2026-11-11",
      allDay: true,
    });
    expect(result.events[1]).toMatchObject({
      start: "2026-11-12",
      end: "2026-11-13",
      allDay: true,
    });
  });
});

describe("calendar freshness labels", () => {
  it("maps every coverage notice", () => {
    expect(calendarCoverageLabel("never_synced")).toBe("nunca sincronizado");
    expect(calendarCoverageLabel("calendar_set_changed")).toContain("conjunto");
    expect(calendarCoverageLabel("outside_coverage")).toContain("ventana");
    expect(calendarCoverageLabel(null)).toBe("cobertura completa");
  });

  it("formats a missing last sync honestly", () => {
    expect(formatLastSyncedAt(null)).toBe("nunca sincronizado");
  });
});
