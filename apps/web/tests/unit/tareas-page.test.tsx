// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { TareasPage } from "@/pages/TareasPage";
import type { LibraryAudioDetail } from "@/lib/library";
import type { Task } from "@/lib/tasks";

const AUDIO_ID = "123e4567-e89b-12d3-a456-426614174000";
const SEGMENT_ID = "223e4567-e89b-12d3-a456-426614174000";

function task(overrides: Partial<Task> = {}): Task {
  return {
    id: "323e4567-e89b-12d3-a456-426614174000",
    audio_id: AUDIO_ID,
    subject: { id: "s1", name: "cálculo" },
    source: "extraction",
    tipo: "tarea",
    titulo: "Resolver la guía",
    detalle: "Incluye integración por partes",
    due_date: "2026-11-09",
    due_at: null,
    timezone: "America/Bogota",
    all_day: true,
    date_status: "resolved",
    date_text: "el lunes 9",
    date_reason: null,
    confidence_score: 0.92,
    status: "needs_review",
    calendar_sync_status: "not_requested",
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

const DETAIL = {
  id: AUDIO_ID,
  transcript: {
    quality_warnings: [{ code: "low_confidence", count: 2, total: 5 }],
  },
} as unknown as LibraryAudioDetail;

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function installFetch(tasks: Task[]) {
  const calls: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url === "/subjects") {
      return json({ items: [{ id: "s1", name: "cálculo", color: "#000", teacher: null, class_count: 0 }] });
    }
    if (url === `/audios/${AUDIO_ID}`) return json(DETAIL);
    if (url.startsWith("/tasks?")) return json({ items: tasks, next_cursor: null });
    return json({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("TareasPage", () => {
  it("renders status, date state, confidence, evidence and ASR warning", async () => {
    installFetch([
      task(),
      task({
        id: "423e4567-e89b-12d3-a456-426614174000",
        titulo: "Examen sin fecha",
        date_status: "ambiguous",
        due_date: null,
        date_text: "el viernes",
        date_reason: "no se sabe cuál viernes",
        confidence_score: null,
        status: "pending",
      }),
    ]);
    render(
      <MemoryRouter>
        <TareasPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("Resolver la guía")).toBeTruthy();
    expect(screen.getByText("Examen sin fecha")).toBeTruthy();
    expect(screen.getAllByText("needs_review").length).toBeGreaterThan(0);
    expect(screen.getAllByText("resolved").length).toBeGreaterThan(0);
    expect(screen.getAllByText("ambiguous").length).toBeGreaterThan(0);
    expect(screen.getByText("confianza 0.92")).toBeTruthy();
    expect(screen.getByText("sin confianza")).toBeTruthy();
    expect(screen.getAllByText(/calidad ASR baja/).length).toBeGreaterThan(0);
    expect(screen.getAllByText(/baja confianza \(2\/5\)/).length).toBeGreaterThan(0);
    expect(screen.getByText("el viernes")).toBeTruthy();
    expect(screen.getByText(/no se sabe cuál viernes/)).toBeTruthy();
    const links = screen.getAllByRole("link", { name: "ver cita en la clase" });
    expect(links[0]?.getAttribute("href")).toBe(
      `/biblioteca/${AUDIO_ID}?segment=${SEGMENT_ID}`,
    );
  });

  it("requests the selected status and subject filters", async () => {
    const calls = installFetch([task()]);
    render(
      <MemoryRouter>
        <TareasPage />
      </MemoryRouter>,
    );
    await screen.findByText("Resolver la guía");

    fireEvent.change(screen.getByLabelText("estado de la tarea"), {
      target: { value: "confirmed" },
    });
    fireEvent.change(screen.getByLabelText("materia de la tarea"), {
      target: { value: "s1" },
    });

    await waitFor(() =>
      expect(
        calls.some(
          (url) => url.includes("status=confirmed") && url.includes("subject_id=s1"),
        ),
      ).toBe(true),
    );
  });

  it("shows an honest empty state", async () => {
    installFetch([]);
    render(
      <MemoryRouter>
        <TareasPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("no hay tareas en este estado")).toBeTruthy();
  });
});
