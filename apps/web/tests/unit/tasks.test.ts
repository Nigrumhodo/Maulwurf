import { afterEach, describe, expect, it, vi } from "vitest";
import {
  addMinutes,
  confidenceLabel,
  dateOnlyAddDays,
  fetchTasks,
  formatTaskDue,
  instantToWall,
  patchTask,
  qualitySignals,
  taskEvidenceHref,
  taskStatusFilters,
  wallTimeToInstant,
} from "@/lib/tasks";
import type { Task } from "@/lib/tasks";
import type { LibraryAudioDetail } from "@/lib/library";

const AUDIO_ID = "123e4567-e89b-12d3-a456-426614174000";
const SEGMENT_ID = "223e4567-e89b-12d3-a456-426614174000";

const TASK: Task = {
  id: "323e4567-e89b-12d3-a456-426614174000",
  audio_id: AUDIO_ID,
  subject: { id: "s1", name: "cálculo" },
  source: "extraction",
  tipo: "tarea",
  titulo: "Resolver la guía",
  detalle: null,
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
};

const DETAIL = {
  transcript: {
    quality_warnings: [
      { code: "low_confidence", count: 2, total: 5 },
      { code: "unknown", count: 1 },
    ],
  },
} as unknown as LibraryAudioDetail;

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("task inbox helpers", () => {
  it("builds the server query and omits the all-status filter", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ items: [], next_cursor: null }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await fetchTasks({
      status: "all",
      subjectId: "s1",
      dueFrom: "2026-11-01",
      limit: 20,
    });

    expect(String(fetchMock.mock.calls[0]?.[0])).toBe(
      "/tasks?limit=20&subject_id=s1&due_from=2026-11-01",
    );
  });

  it("formats only dates the contract resolved", () => {
    expect(formatTaskDue(TASK)).toBe("09/11/2026");
    expect(formatTaskDue({ ...TASK, date_status: "ambiguous", due_date: null })).toBeNull();
    expect(formatTaskDue({ ...TASK, date_status: "missing", due_date: null })).toBeNull();
  });

  it("maps the known ASR warning codes and ignores unknown ones", () => {
    expect(qualitySignals(DETAIL)).toEqual([
      { code: "low_confidence", label: "baja confianza", count: 2, total: 5 },
    ]);
    expect(qualitySignals(null)).toEqual([]);
  });

  it("links evidence only through the canonical reader path", () => {
    expect(taskEvidenceHref(TASK, TASK.evidence[0]!)).toBe(
      `/biblioteca/${AUDIO_ID}?segment=${SEGMENT_ID}`,
    );
    expect(
      taskEvidenceHref(
        { ...TASK, audio_id: null, sources: [] },
        TASK.evidence[0]!,
      ),
    ).toBeNull();
  });

  it("shows confidence without turning it into a certainty", () => {
    expect(confidenceLabel(0.92)).toBe("confianza 0.92");
    expect(confidenceLabel(null)).toBe("sin confianza");
    expect(taskStatusFilters()[0]).toEqual({ value: "all", label: "todas" });
    expect(taskStatusFilters().some((item) => item.value === "needs_review")).toBe(true);
  });

  it("converts wall time in the task timezone and preserves the instant", () => {
    expect(wallTimeToInstant("2026-11-09", "09:00", "America/Bogota")).toBe(
      "2026-11-09T14:00:00.000Z",
    );
    expect(instantToWall("2026-11-09T14:00:00.000Z", "America/Bogota")).toEqual({
      date: "2026-11-09",
      time: "09:00",
    });
    expect(wallTimeToInstant("2026-03-08", "02:30", "America/New_York")).toBeNull();
    expect(wallTimeToInstant("2026-03-08", "03:30", "America/New_York")).toBe(
      "2026-03-08T07:30:00.000Z",
    );
  });

  it("builds date-only and duration defaults without inventing teacher data", () => {
    expect(dateOnlyAddDays("2026-11-09", 1)).toBe("2026-11-10");
    expect(addMinutes("2026-11-09T14:00:00.000Z", 30)).toBe(
      "2026-11-09T14:30:00.000Z",
    );
    expect(dateOnlyAddDays("not-a-date", 1)).toBeNull();
    expect(addMinutes("not-an-instant", 30)).toBeNull();
  });

  it("sends PATCH with the task version", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ task: TASK, revision_id: null }), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await patchTask(TASK.id, { version: TASK.version, titulo: "Otra" });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`/tasks/${TASK.id}`);
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({
      version: TASK.version,
      titulo: "Otra",
    });
  });
});
