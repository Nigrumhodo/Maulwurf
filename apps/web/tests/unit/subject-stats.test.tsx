// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { SubjectStats } from "@/components/subjects/SubjectStats";
import { computeSubjectStats } from "@/lib/stats";
import type { AudioListItemOut } from "@/lib/audios";
import type { Subject } from "@/lib/subjects";
import type { Task } from "@/lib/tasks";

const SUBJECTS: Subject[] = [
  { id: "s-1", name: "cálculo", color: "#f5c542", teacher: "ana", class_count: 2 },
  { id: "s-2", name: "física", color: "#22c55e", teacher: null, class_count: 0 },
];

const AUDIOS: AudioListItemOut[] = [
  {
    id: "a-1",
    subject: { id: "s-1", name: "cálculo", color: "#f5c542" },
    title: "límites",
    class_date: "2026-11-18",
    class_timezone: "America/Bogota",
    language_code: "es",
    duration_seconds: 1840,
    created_at: "2026-11-18T12:00:00Z",
    attempt: null,
    stages: { index: "succeeded", analyze: "succeeded" },
    aggregate_status: "ready",
  },
  {
    id: "a-2",
    subject: { id: "s-1", name: "cálculo", color: "#f5c542" },
    title: "derivadas",
    class_date: "2026-11-19",
    class_timezone: "America/Bogota",
    language_code: "es",
    duration_seconds: 920,
    created_at: "2026-11-19T12:00:00Z",
    attempt: null,
    stages: {},
    aggregate_status: "partial",
  },
];

const TASKS: Task[] = [
  {
    id: "t-1",
    audio_id: "a-1",
    subject: { id: "s-1", name: "cálculo" },
    source: "extraction",
    tipo: "entrega",
    titulo: "taller 1",
    detalle: null,
    due_date: "2026-11-25",
    due_at: null,
    timezone: "America/Bogota",
    all_day: true,
    date_status: "resolved",
    date_text: null,
    date_reason: null,
    confidence_score: 0.9,
    status: "confirmed",
    calendar_sync_status: "synced",
    calendar_id: null,
    calendar_event_id: null,
    version: 1,
    due_version: 1,
    event_block: {
      start_at: null,
      end_at: null,
      start_date: null,
      end_date_exclusive: null,
      duration_default_editable: false,
    },
    sources: [],
    evidence: [],
    pending_revision: null,
  },
];

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

describe("SubjectStats (D4.4)", () => {
  it("computes total and per-subject metadata counts without rounding drift", () => {
    const completedTask: Task = { ...TASKS[0]!, id: "t-2", status: "completed" };
    const result = computeSubjectStats(SUBJECTS, AUDIOS, [...TASKS, completedTask]);
    expect(result.subjectCount).toBe(2);
    expect(result.classCount).toBe(2);
    expect(result.readyClassCount).toBe(1);
    expect(result.transcribedMinutes).toBe(46);
    expect(result.confirmedTaskCount).toBe(1);
    expect(result.completedTaskCount).toBe(1);
    expect(result.pendingTaskCount).toBe(0);
    expect(result.subjects[0]?.transcribedMinutes).toBe(46);
    expect(result.subjects[0]?.completedTasks).toBe(1);
  });

  it("renders metadata-only metrics with zero audio elements", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL) => {
        const url = String(input);
        if (url === "/audios") return json({ items: AUDIOS });
        if (url.startsWith("/tasks?")) return json({ items: TASKS, next_cursor: null });
        if (url === "/subjects") return json({ items: SUBJECTS });
        return json({});
      }),
    );

    const { container } = render(<SubjectStats subjects={SUBJECTS} />);

    await waitFor(() => expect(screen.getByText("46 min")).toBeTruthy());
    expect(screen.getByText(/solo metadatos y texto/i)).toBeTruthy();
    expect(container.querySelectorAll("audio")).toHaveLength(0);
  });
});
