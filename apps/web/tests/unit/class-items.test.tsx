// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ClassItems } from "@/components/transcript/ClassItems";
import type { Task, TaskQualityState } from "@/lib/tasks";

const AUDIO_ID = "123e4567-e89b-12d3-a456-426614174000";
const SEGMENT_ID = "223e4567-e89b-12d3-a456-426614174000";

const QUALITY: TaskQualityState = {
  signals: [],
  unavailable: false,
  timestamp_precision: "segment",
  duration_seconds: 20,
};

function task(overrides: Partial<Task> = {}): Task {
  return {
    id: "323e4567-e89b-12d3-a456-426614174000",
    audio_id: AUDIO_ID,
    subject: null,
    source: "extraction",
    tipo: "tarea",
    titulo: "Elemento activo",
    detalle: null,
    due_date: "2026-11-09",
    due_at: null,
    timezone: "America/Bogota",
    all_day: true,
    date_status: "resolved",
    date_text: null,
    date_reason: null,
    confidence_score: null,
    status: "needs_review",
    calendar_sync_status: "not_requested",
    calendar_id: null,
    calendar_event_id: null,
    version: 1,
    due_version: 1,
    event_block: {
      start_at: null,
      end_at: null,
      start_date: null,
      end_date_exclusive: null,
      duration_default_editable: true,
    },
    sources: [
      {
        audio_id: AUDIO_ID,
        transcript_version: 1,
        segment_id: SEGMENT_ID,
        char_start: 0,
        char_end: 7,
      },
    ],
    evidence: [
      {
        segment_id: SEGMENT_ID,
        ordinal: 1,
        char_start: 0,
        char_end: 7,
        quote: "entrega",
        t_start: 5,
        t_end: 10,
      },
    ],
    pending_revision: null,
    ...overrides,
  };
}

function renderItems(
  tasks: Task[],
  options: { loading?: boolean; error?: string | null; onRefresh?: () => void } = {},
) {
  return render(
    <MemoryRouter>
      <ClassItems
        tasks={tasks}
        quality={QUALITY}
        loading={options.loading ?? false}
        error={options.error ?? null}
        onRefresh={options.onRefresh ?? vi.fn()}
      />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
});

describe("ClassItems", () => {
  it("shows active items first and keeps their evidence link", () => {
    renderItems([
      task({ id: "closed", titulo: "Elemento cerrado", status: "dismissed" }),
      task(),
    ]);

    const list = screen.getByRole("list", { name: "elementos detectados" });
    const items = within(list).getAllByRole("listitem");
    expect(items[0]?.textContent).toContain("Elemento activo");
    expect(items[1]?.textContent).toContain("Elemento cerrado");
    expect(
      within(items[0]!)
        .getByRole("link", { name: "ver segmento 1 en la clase" })
        .getAttribute("href"),
    ).toBe(`/biblioteca/${AUDIO_ID}?segment=${SEGMENT_ID}`);
  });

  it("shows an honest empty state", () => {
    renderItems([]);

    expect(screen.getByText(/no se detectaron tareas/)).toBeTruthy();
  });

  it("keeps the class page usable when the items request fails", () => {
    const onRefresh = vi.fn();
    renderItems([], { error: "no se pudieron cargar", onRefresh });

    expect(screen.getByRole("alert").textContent).toContain("no se pudieron cargar");
    fireEvent.click(screen.getByRole("button", { name: "reintentar" }));
    expect(onRefresh).toHaveBeenCalledOnce();
  });
});

