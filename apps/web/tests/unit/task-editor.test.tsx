// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { TaskEditor } from "@/components/tasks/TaskEditor";
import type { Task } from "@/lib/tasks";

const TASK: Task = {
  id: "323e4567-e89b-12d3-a456-426614174000",
  audio_id: null,
  subject: null,
  source: "manual",
  tipo: "tarea",
  titulo: "Preparar entrega",
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

describe("TaskEditor", () => {
  it("keeps the agenda block separate and exposes an editable 30-minute default", async () => {
    const onSaved = vi.fn();
    const fetchMock = vi.fn().mockResolvedValue(json({ task: TASK, revision_id: null }));
    vi.stubGlobal("fetch", fetchMock);
    render(<TaskEditor task={TASK} onSaved={onSaved} onCancel={vi.fn()} />);

    expect(screen.getByText(/no proviene del docente/)).toBeTruthy();
    fireEvent.change(screen.getByLabelText("modo de agenda"), { target: { value: "timed" } });
    fireEvent.change(screen.getAllByLabelText(/inicio de agenda/)[0]!, {
      target: { value: "2026-11-10" },
    });
    fireEvent.change(screen.getByLabelText(/hora de inicio de agenda/), {
      target: { value: "10:00" },
    });
    const duration = screen.getByLabelText(/duración de agenda/) as HTMLInputElement;
    expect(duration.value).toBe("30");
    fireEvent.change(duration, { target: { value: "45" } });

    fireEvent.click(screen.getByRole("button", { name: "guardar cambios" }));

    await waitFor(() => expect(fetchMock).toHaveBeenCalledOnce());
    const body = JSON.parse(String((fetchMock.mock.calls[0]?.[1] as RequestInit).body)) as {
      due_date: string;
      event_block: { start_at: string; end_at: string };
    };
    expect(body.due_date).toBe("2026-11-09");
    expect(body.event_block.start_at).toBe("2026-11-10T15:00:00.000Z");
    expect(body.event_block.end_at).toBe("2026-11-10T15:45:00.000Z");
    expect(onSaved).toHaveBeenCalledOnce();
  });

  it("shows the pending revision without pretending the active task changed", async () => {
    const onSaved = vi.fn();
    const confirmed: Task = {
      ...TASK,
      status: "confirmed",
      pending_revision: {
        id: "revision-1",
        base_task_version: 1,
        status: "pending_confirmation",
        proposed_fields: { titulo: "Otra" },
      },
    };
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        json({
          task: confirmed,
          revision_id: "revision-1",
        }),
      ),
    );
    render(<TaskEditor task={confirmed} onSaved={onSaved} onCancel={vi.fn()} />);

    fireEvent.click(screen.getByRole("button", { name: "guardar cambios" }));

    expect(
      await screen.findByText(/cambio pendiente de confirmación/),
    ).toBeTruthy();
    expect(onSaved).toHaveBeenCalledWith({ task: confirmed, revision_id: "revision-1" });
  });

  it("rejects an all-day agenda whose end is not later than its start", () => {
    render(<TaskEditor task={TASK} onSaved={vi.fn()} onCancel={vi.fn()} />);

    fireEvent.change(screen.getByLabelText("modo de agenda"), {
      target: { value: "all_day" },
    });
    fireEvent.change(screen.getAllByLabelText(/inicio de agenda/)[0]!, {
      target: { value: "2026-11-11" },
    });
    fireEvent.change(screen.getByLabelText(/fin de agenda/), {
      target: { value: "2026-11-10" },
    });
    fireEvent.click(screen.getByRole("button", { name: "guardar cambios" }));

    expect(screen.getByRole("alert").textContent).toContain(
      "el fin de agenda debe ser posterior al inicio",
    );
  });
});
