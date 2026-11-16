// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
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

function installFetch(
  tasks: Task[],
  google: {
    status: "connected" | "disconnected";
    calendar_id: string | null;
    calendar_write_ready: boolean;
  } = { status: "disconnected", calendar_id: null, calendar_write_ready: false },
) {
  const calls: string[] = [];
  const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
    const url = String(input);
    calls.push(url);
    if (url === "/subjects") {
      return json({ items: [{ id: "s1", name: "cálculo", color: "#000", teacher: null, class_count: 0 }] });
    }
    if (url === `/audios/${AUDIO_ID}`) return json(DETAIL);
    if (url.startsWith("/tasks?")) return json({ items: tasks, next_cursor: null });
    if (tasks[0] !== undefined && url === `/tasks/${tasks[0].id}`) return json(tasks[0]);
    if (url === "/integrations/status") {
      return json({
        google: {
          scopes: [],
          token_expires_at: null,
          last_refresh_at: null,
          ...google,
        },
      });
    }
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
    const links = screen.getAllByRole("link", {
      name: "ver segmento 1 en la clase",
    });
    expect(links[0]?.getAttribute("href")).toBe(
      `/biblioteca/${AUDIO_ID}?segment=${SEGMENT_ID}`,
    );
    expect(links[0]?.textContent).toContain("segmento 1");
  });

  it("shows a measured time only when the reader metadata makes it valid", async () => {
    const timed = task({
      evidence: [
        {
          segment_id: SEGMENT_ID,
          ordinal: 2,
          char_start: 0,
          char_end: 7,
          quote: "entrega",
          t_start: 5,
          t_end: 10,
        },
      ],
    });
    const timedDetail = {
      ...DETAIL,
      duration_seconds: 20,
      transcript: {
        ...(DETAIL.transcript ?? {}),
        timestamp_precision: "segment",
        quality_warnings: [],
      },
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/subjects") return json({ items: [] });
      if (url.startsWith("/tasks?")) {
        return json({ items: [timed], next_cursor: null });
      }
      if (url === `/audios/${AUDIO_ID}`) return json(timedDetail);
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <TareasPage />
      </MemoryRouter>,
    );

    const link = await screen.findByRole("link", {
      name: "ver segmento 2 en la clase",
    });
    expect(link.textContent).toContain("0:05");
    expect(link.getAttribute("href")).toBe(
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

  it("opens the review dialog from the inbox card", async () => {
    installFetch([task()]);
    render(
      <MemoryRouter>
        <TareasPage />
      </MemoryRouter>,
    );

    await screen.findByText("Resolver la guía");
    fireEvent.click(screen.getByRole("button", { name: "revisar" }));

    expect(await screen.findByRole("dialog", { name: "revisar tarea" })).toBeTruthy();
    expect(screen.getByText(/qué se escribirá/)).toBeTruthy();
    expect(screen.getByText(/quedará bloqueada/)).toBeTruthy();
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

  it("refreshes after a 409 without auto-confirming or losing the editor draft", async () => {
    const base = task({ audio_id: null, sources: [] });
    const fresh = {
      ...base,
      version: 2,
      titulo: "Cambió en otra pestaña",
    };
    let taskReads = 0;
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/subjects") return json({ items: [] });
      if (url.startsWith("/tasks?")) {
        return json({ items: [base], next_cursor: null });
      }
      if (url === `/tasks/${base.id}/confirm`) {
        return json(
          {
            error: {
              code: "version_conflict",
              message: "La tarea cambió desde que la cargaste; recarga antes de continuar.",
            },
          },
          409,
        );
      }
      if (url === `/tasks/${base.id}`) {
        taskReads += 1;
        return json(taskReads === 1 ? base : fresh);
      }
      if (url === "/integrations/status") {
        return json({
          google: {
            status: "connected",
            scopes: [],
            calendar_id: "mw@group.calendar.example",
            calendar_write_ready: true,
            token_expires_at: null,
            last_refresh_at: null,
          },
        });
      }
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter>
        <TareasPage />
      </MemoryRouter>,
    );

    await screen.findByText(base.titulo);
    fireEvent.click(screen.getByRole("button", { name: "editar" }));
    const titleInput = screen.getByLabelText(/título/) as HTMLInputElement;
    fireEvent.change(titleInput, { target: { value: "Borrador local" } });

    fireEvent.click(screen.getByRole("button", { name: "revisar" }));
    const dialog = await screen.findByRole("dialog", { name: "revisar tarea" });
    await within(dialog).findByText(/qué se escribirá/);
    fireEvent.click(within(dialog).getByRole("button", { name: "confirmar" }));

    expect(
      await within(dialog).findByText(/La tarea cambió; se actualizó la vista/),
    ).toBeTruthy();
    expect(await within(dialog).findByText("Cambió en otra pestaña")).toBeTruthy();
    expect((screen.getByLabelText(/título/) as HTMLInputElement).value).toBe(
      "Borrador local",
    );
    expect(
      fetchMock.mock.calls.filter(([url]) => String(url).endsWith("/confirm")),
    ).toHaveLength(1);
  });

  it("shows the reconnect banner while Calendar is not ready", async () => {
    installFetch([task()]);
    render(
      <MemoryRouter>
        <TareasPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText(/calendar no está listo/)).toBeTruthy();
    const link = screen.getByRole("link", { name: "conectar google calendar" });
    expect(link.getAttribute("href")).toBe("/integrations/google/connect");
  });

  it("hides the reconnect banner when Calendar is write-ready", async () => {
    installFetch([task()], {
      status: "connected",
      calendar_id: "mw-1",
      calendar_write_ready: true,
    });
    render(
      <MemoryRouter>
        <TareasPage />
      </MemoryRouter>,
    );

    await screen.findByText("Resolver la guía");
    expect(screen.queryByText(/calendar no está listo/)).toBeNull();
  });
});
