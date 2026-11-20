// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { SyncStatus } from "@/components/calendar/SyncStatus";
import type { Task } from "@/lib/tasks";

const AUDIO_ID = "123e4567-e89b-12d3-a456-426614174000";
const SEGMENT_ID = "223e4567-e89b-12d3-a456-426614174000";

function task(overrides: Partial<Task> = {}): Task {
  return {
    id: "323e4567-e89b-12d3-a456-426614174000",
    audio_id: AUDIO_ID,
    subject: null,
    source: "extraction",
    tipo: "tarea",
    titulo: "Resolver la guía",
    detalle: null,
    due_date: "2026-11-09",
    due_at: null,
    timezone: "America/Bogota",
    all_day: true,
    date_status: "resolved",
    date_text: null,
    date_reason: null,
    confidence_score: 0.9,
    status: "confirmed",
    calendar_sync_status: "not_requested",
    calendar_id: null,
    calendar_event_id: null,
    version: 2,
    due_version: 1,
    event_block: {
      start_at: null,
      end_at: null,
      start_date: null,
      end_date_exclusive: null,
      duration_default_editable: true,
    },
    sources: [],
    evidence: [
      {
        segment_id: SEGMENT_ID,
        ordinal: 1,
        char_start: 0,
        char_end: 7,
        quote: "entrega",
        t_start: null,
        t_end: null,
      },
    ],
    pending_revision: null,
    ...overrides,
  };
}

afterEach(cleanup);

describe("SyncStatus", () => {
  it("explains that a task is only written after confirmation", () => {
    render(<SyncStatus task={task({ calendar_sync_status: "not_requested" })} />);
    expect(screen.getByText(/solo se escribe tras confirmar/)).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("explains a queued write without inventing a Calendar call", () => {
    render(<SyncStatus task={task({ calendar_sync_status: "pending" })} />);
    expect(screen.getByText(/envío a Calendar en cola/)).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("points a blocked task with a bound calendar to reconnect", () => {
    render(
      <SyncStatus
        task={task({ calendar_sync_status: "blocked", calendar_id: "mw-1" })}
      />,
    );
    const link = screen.getByRole("link", { name: "reconectar google calendar" });
    expect(link.getAttribute("href")).toBe("/integrations/google/reconnect");
  });

  it("points a blocked task without a calendar to connect", () => {
    render(
      <SyncStatus
        task={task({ calendar_sync_status: "blocked", calendar_id: null })}
      />,
    );
    const link = screen.getByRole("link", { name: "conectar google calendar" });
    expect(link.getAttribute("href")).toBe("/integrations/google/connect");
  });

  it("keeps a failed task info-only, with no automatic retry", () => {
    render(
      <SyncStatus
        task={task({ calendar_sync_status: "failed", calendar_id: "mw-1" })}
      />,
    );
    expect(screen.getByText(/no se reintenta automáticamente/)).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });

  it("describes a synced event and offers no action", () => {
    render(
      <SyncStatus
        task={task({ calendar_sync_status: "synced", calendar_id: "mw-1" })}
      />,
    );
    expect(screen.getByText(/evento escrito en el calendario/)).toBeTruthy();
    expect(screen.queryByRole("link")).toBeNull();
  });
});
