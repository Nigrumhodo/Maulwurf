// @vitest-environment jsdom
//
// D2.5 — C-S2-DA-04: la ruta `/biblioteca/:audioId?segment=` obtiene detalle + lector y
// entrega el `segment` al componente (destino estable de las citas).
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { TranscriptReaderPage } from "@/pages/TranscriptReaderPage";
import type { LibraryAudioDetail } from "@/lib/library";
import type { ReaderTranscript } from "@/lib/reader";
import type { Task } from "@/lib/tasks";

const DETAIL: LibraryAudioDetail = {
  id: "a1",
  subject: { id: "sub1", name: "materia sintetica", color: "#112233" },
  title: "clase de prueba",
  class_date: "2026-11-04",
  class_timezone: "America/Bogota",
  language_code: "es",
  duration_seconds: 10,
  created_at: "2026-11-04T12:00:00Z",
  attempt: null,
  stages: { index: "succeeded", analyze: "not_requested" },
  aggregate_status: "ready",
  audio_retained: false,
  analysis_backfill_available: false,
  transcript: {
    version: 1,
    timestamp_precision: "segment",
    char_count: 20,
    quality_warnings: [],
    summary: null,
    topics: [],
    analysis_version: null,
  },
  exports: { txt: true, srt: true },
};

const TRANSCRIPT: ReaderTranscript = {
  audio_id: "a1",
  version: 1,
  timestamp_precision: "segment",
  char_count: 20,
  summary: null,
  topics: [],
  analysis_version: null,
  segments: [
    {
      id: "s2",
      ordinal: 2,
      text: "segundo segmento",
      char_start: 7,
      char_end: 15,
      t_start: 5,
      t_end: 10,
    },
  ],
};

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("TranscriptReaderPage", () => {
  it("lee detalle y lector y resalta el segmento de la URL", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      const body = url.startsWith("/tasks?")
        ? { items: [], next_cursor: null }
        : url.endsWith("/transcript")
          ? TRANSCRIPT
          : DETAIL;
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/biblioteca/a1?segment=s2"]}>
        <Routes>
          <Route path="/biblioteca/:audioId" element={<TranscriptReaderPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText("segundo segmento")).toBeTruthy();
    expect(document.getElementById("segment-s2")?.getAttribute("aria-current")).toBe("location");
    expect(fetchMock.mock.calls.map(([url]) => String(url))).toEqual([
      "/audios/a1",
      "/audios/a1/transcript",
      "/tasks?limit=200&audio_id=a1",
    ]);
  });

  it("renders the complete class page and queues the F1 backfill", async () => {
    const detail: LibraryAudioDetail = {
      ...DETAIL,
      analysis_backfill_available: true,
      stages: { index: "succeeded", analyze: "not_requested" },
      transcript: {
        ...DETAIL.transcript!,
        summary: "resumen persistido",
        topics: ["tema uno"],
        analysis_version: 2,
        quality_warnings: [{ code: "low_confidence", count: 2, total: 5 }],
      },
    };
    const transcript: ReaderTranscript = {
      ...TRANSCRIPT,
      summary: "resumen persistido",
      topics: ["tema uno"],
      analysis_version: 2,
    };
    const task: Task = {
      id: "t1",
      audio_id: "a1",
      subject: null,
      source: "extraction",
      tipo: "tarea",
      titulo: "Elemento detectado",
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
          audio_id: "a1",
          transcript_version: 1,
          segment_id: "s2",
          char_start: 0,
          char_end: 7,
        },
      ],
      evidence: [
        {
          segment_id: "s2",
          ordinal: 2,
          char_start: 0,
          char_end: 7,
          quote: "segundo",
          t_start: 5,
          t_end: 10,
        },
      ],
      pending_revision: null,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/audios/a1/analysis/backfill") {
        return new Response(
          JSON.stringify({
            audio_id: "a1",
            status: "queued",
            transcript_version: 1,
            run_id: "r1",
            run_status: "pending",
          }),
          { status: 202, headers: { "content-type": "application/json" } },
        );
      }
      if (url.startsWith("/tasks?")) {
        return new Response(JSON.stringify({ items: [task], next_cursor: null }), {
          status: 200,
          headers: { "content-type": "application/json" },
        });
      }
      const body = url.endsWith("/transcript") ? transcript : detail;
      return new Response(JSON.stringify(body), {
        status: 200,
        headers: { "content-type": "application/json" },
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/biblioteca/a1"]}>
        <Routes>
          <Route path="/biblioteca/:audioId" element={<TranscriptReaderPage />} />
        </Routes>
      </MemoryRouter>,
    );

    expect(await screen.findByText(/resumen no solicitado/)).toBeTruthy();
    expect(screen.getByText(/baja confianza \(2\/5\)/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "analizar esta clase" })).toBeTruthy();
    expect(screen.getByText("Elemento detectado")).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "analizar esta clase" }));
    await screen.findByText(/análisis encolado/);
    expect(fetchMock.mock.calls.some(([url]) => String(url).endsWith("/analysis/backfill"))).toBe(
      true,
    );
  });
});
