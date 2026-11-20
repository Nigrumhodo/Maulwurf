// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TaskReviewDialog } from "@/components/tasks/TaskReviewDialog";
import type { Task } from "@/lib/tasks";

const TASK: Task = {
  id: "323e4567-e89b-12d3-a456-426614174000",
  audio_id: null,
  subject: null,
  source: "manual",
  tipo: "tarea",
  titulo: "Preparar entrega",
  detalle: "Capítulos 1-4",
  due_date: "2026-11-09",
  due_at: null,
  timezone: "America/Bogota",
  all_day: true,
  date_status: "resolved",
  date_text: null,
  date_reason: null,
  confidence_score: null,
  status: "confirmed",
  calendar_sync_status: "synced",
  calendar_id: null,
  calendar_event_id: null,
  version: 2,
  due_version: 1,
  event_block: {
    start_at: null,
    end_at: null,
    start_date: "2026-11-10",
    end_date_exclusive: "2026-11-11",
    duration_default_editable: true,
  },
  sources: [],
  evidence: [],
  pending_revision: {
    id: "revision-1",
    base_task_version: 2,
    status: "pending_confirmation",
    proposed_fields: {
      titulo: "Preparar entrega revisada",
      due_date: "2026-11-12",
      date_status: "resolved",
      all_day: true,
      event_block: {
        start_at: null,
        end_at: null,
        start_date: "2026-11-12",
        end_date_exclusive: "2026-11-13",
      },
    },
  },
};

const GOOGLE = {
  google: {
    status: "connected",
    scopes: [],
    calendar_id: "mw@group.calendar.example",
    calendar_write_ready: true,
    token_expires_at: null,
    last_refresh_at: null,
  },
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

describe("TaskReviewDialog", () => {
  it("shows the effective revision and the Calendar target before confirming", async () => {
    const onUpdated = vi.fn();
    const confirmed: Task = {
      ...TASK,
      pending_revision: null,
      calendar_sync_status: "pending",
      calendar_id: null,
      calendar_event_id: null,
      version: 3,
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === `/tasks/${TASK.id}/confirm`) {
        expect(JSON.parse(String(init?.body))).toEqual({
          expected_version: 2,
          revision_id: "revision-1",
        });
        return Promise.resolve(json({ task: confirmed, revision_id: "revision-1" }, 202));
      }
      if (url === "/integrations/status") return Promise.resolve(json(GOOGLE));
      if (url === `/tasks/${TASK.id}`) return Promise.resolve(json(TASK));
      return Promise.reject(new Error(`unexpected ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <TaskReviewDialog task={TASK} onUpdated={onUpdated} onClose={vi.fn()} />,
    );

    expect(await screen.findByText("Preparar entrega revisada")).toBeTruthy();
    expect(screen.getAllByText(/12\/11\/2026/).length).toBeGreaterThan(0);
    expect(screen.getByText(/Maulwurf/)).toBeTruthy();
    expect(screen.getByText(/se confirmará el cambio pendiente/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "confirmar" }));

    await waitFor(() => expect(onUpdated).toHaveBeenCalledWith(confirmed));
    expect(await screen.findByText(/envío a Calendar queda pendiente/)).toBeTruthy();
  });

  it("refreshes the preview after a 409 without auto-confirming", async () => {
    const onUpdated = vi.fn();
    const fresh: Task = {
      ...TASK,
      version: 3,
      titulo: "Cambió en otra pestaña",
      pending_revision: null,
    };
    let taskReads = 0;
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === `/tasks/${TASK.id}/confirm`) {
        return Promise.resolve(
          json({ error: { code: "version_conflict", message: "La tarea cambió." } }, 409),
        );
      }
      if (url === "/integrations/status") return Promise.resolve(json(GOOGLE));
      if (url === `/tasks/${TASK.id}`) {
        taskReads += 1;
        return Promise.resolve(json(taskReads === 1 ? TASK : fresh));
      }
      return Promise.reject(new Error(`unexpected ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <TaskReviewDialog task={TASK} onUpdated={onUpdated} onClose={vi.fn()} />,
    );

    expect(await screen.findByText("Preparar entrega revisada")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "confirmar" }));

    expect(await screen.findByText(/La tarea cambió/)).toBeTruthy();
    await waitFor(() => expect(onUpdated).toHaveBeenCalledWith(fresh));
    expect(screen.getByText(/Cambió en otra pestaña/)).toBeTruthy();
    const confirmCalls = fetchMock.mock.calls.filter(([url]) =>
      String(url).endsWith("/confirm"),
    );
    expect(confirmCalls).toHaveLength(1);
  });

  it("warns that a task without a ready Calendar will stay blocked", async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/integrations/status") {
        return Promise.resolve(
          json({
            google: {
              ...GOOGLE.google,
              status: "disconnected",
              calendar_id: null,
              calendar_write_ready: false,
            },
          }),
        );
      }
      if (url === `/tasks/${TASK.id}`) return Promise.resolve(json(TASK));
      return Promise.reject(new Error(`unexpected ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <TaskReviewDialog task={TASK} onUpdated={vi.fn()} onClose={vi.fn()} />,
    );

    expect(await screen.findByText(/quedará bloqueada/)).toBeTruthy();
  });

  it("dismisses with the active version and reports the kept event", async () => {
    const onUpdated = vi.fn();
    const dismissed: Task = {
      ...TASK,
      status: "dismissed",
      pending_revision: null,
      version: 3,
    };
    const fetchMock = vi.fn((input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === `/tasks/${TASK.id}/dismiss`) {
        expect(JSON.parse(String(init?.body))).toEqual({ expected_version: 2 });
        return Promise.resolve(
          json({
            task: dismissed,
            notice: "calendar_event_kept",
            cancelled_reminders: 1,
          }),
        );
      }
      if (url === "/integrations/status") return Promise.resolve(json(GOOGLE));
      if (url === `/tasks/${TASK.id}`) return Promise.resolve(json(TASK));
      return Promise.reject(new Error(`unexpected ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <TaskReviewDialog task={TASK} onUpdated={onUpdated} onClose={vi.fn()} />,
    );

    await screen.findByText("Preparar entrega revisada");
    fireEvent.click(screen.getByRole("button", { name: "descartar" }));

    await waitFor(() => expect(onUpdated).toHaveBeenCalledWith(dismissed));
    expect(await screen.findByText(/El evento de Calendar se conserva/)).toBeTruthy();
    expect(screen.queryByRole("button", { name: "completar" })).toBeNull();
  });

  it("compacts long Google Calendar IDs and fires onActionComplete on confirm", async () => {
    const onActionComplete = vi.fn();
    const confirmed: Task = {
      ...TASK,
      pending_revision: null,
      calendar_sync_status: "synced",
      version: 3,
    };
    const longCalId =
      "c_849b1a251db3441c5c1a748836da21746e5c5551b6ac7108abbec76027910ab0@group.calendar.google.com";
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === `/tasks/${TASK.id}/confirm`) {
        return Promise.resolve(json({ task: confirmed }, 202));
      }
      if (url === "/integrations/status") {
        return Promise.resolve(
          json({ google: { ...GOOGLE.google, calendar_id: longCalId } }),
        );
      }
      if (url === `/tasks/${TASK.id}`) return Promise.resolve(json(TASK));
      return Promise.reject(new Error(`unexpected ${url}`));
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <TaskReviewDialog
        task={TASK}
        onUpdated={vi.fn()}
        onActionComplete={onActionComplete}
        onClose={vi.fn()}
      />,
    );

    expect(
      await screen.findByText("Maulwurf · c_849b1a25…@group.calendar.google.com"),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "confirmar" }));
    await waitFor(() =>
      expect(onActionComplete).toHaveBeenCalledWith(confirmed, "Confirmada y sincronizada."),
    );
  });
});
