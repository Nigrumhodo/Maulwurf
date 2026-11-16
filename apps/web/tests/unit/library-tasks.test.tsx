// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { ActivityFeed } from "@/components/dashboard/ActivityFeed";
import { TaskLog } from "@/components/dashboard/TaskLog";
import { BibliotecaPage } from "@/pages/BibliotecaPage";
import { TareasPage } from "@/pages/TareasPage";

const AUDIO = {
  id: "a1",
  subject: { id: "s1", name: "cálculo i", color: "#6366f1" },
  title: "clase 03",
  class_date: "2026-10-29",
  class_timezone: "America/Bogota",
  language_code: "es",
  duration_seconds: 90,
  created_at: "2026-10-29T12:00:00Z",
  attempt: {
    attempt_id: "t1",
    status: "succeeded",
    received_bytes: 1200,
    fragments_done: 2,
    fragments_total: 2,
    cleanup_status: "verified",
  },
  stages: { index: "succeeded", analyze: "not_requested" },
  aggregate_status: "ready",
};

const TASK = {
  id: "task-1",
  audio_id: "a1",
  subject: { id: "s1", name: "cálculo i" },
  source: "analysis",
  tipo: "entrega",
  titulo: "taller de integrales",
  detalle: null,
  due_date: "2026-11-20",
  due_at: null,
  timezone: "America/Bogota",
  all_day: true,
  date_status: "explicit",
  date_text: null,
  date_reason: null,
  confidence_score: null,
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
  sources: [],
  evidence: [],
  pending_revision: null,
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("biblioteca y radar", () => {
  it("muestra la clase real y no un porcentaje inventado", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        if (String(input) === "/audios") return json({ items: [AUDIO] });
        return json({});
      }),
    );

    render(
      <MemoryRouter>
        <BibliotecaPage />
        <ActivityFeed />
      </MemoryRouter>,
    );

    expect((await screen.findAllByText(/cálculo i/)).length).toBeGreaterThan(0);
    expect(screen.getAllByText("clase 03").length).toBeGreaterThan(0);
    expect(screen.getByText("verified")).toBeDefined();
    expect(screen.getAllByText("ready").length).toBeGreaterThan(0);
    expect(screen.queryByText(/98%/)).toBeNull();
    expect(screen.queryByText(/72%/)).toBeNull();
    expect(screen.queryByText(/resumen ia/i)).toBeNull();
  });

  it("comparte el vacío cuando no hay clases", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ items: [] })));

    render(
      <MemoryRouter>
        <BibliotecaPage />
        <ActivityFeed />
      </MemoryRouter>,
    );

    expect((await screen.findAllByText("aún no hay clases")).length).toBe(2);
  });
});

describe("tareas", () => {
  it("el tablero y la página muestran la misma tarea", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ items: [TASK], next_cursor: null })));

    render(
      <MemoryRouter>
        <TaskLog />
        <TareasPage />
      </MemoryRouter>,
    );

    expect((await screen.findAllByText("taller de integrales")).length).toBe(2);
    expect(screen.getAllByText(/cálculo i/).length).toBe(2);
    expect(screen.queryByText(/backprop/)).toBeNull();
  });

  it("cada superficie muestra su vacío honesto", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ items: [], next_cursor: null })));

    render(
      <MemoryRouter>
        <TaskLog />
        <TareasPage />
      </MemoryRouter>,
    );

    expect(await screen.findByText("aún no hay tareas")).toBeTruthy();
    expect(await screen.findByText("no hay tareas en este estado")).toBeTruthy();
  });
});
