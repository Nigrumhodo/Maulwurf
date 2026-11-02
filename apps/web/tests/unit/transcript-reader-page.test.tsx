// @vitest-environment jsdom
//
// D2.5 — C-S2-DA-04: la ruta `/biblioteca/:audioId?segment=` obtiene detalle + lector y
// entrega el `segment` al componente (destino estable de las citas).
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import { TranscriptReaderPage } from "@/pages/TranscriptReaderPage";
import type { LibraryAudioDetail } from "@/lib/library";
import type { ReaderTranscript } from "@/lib/reader";

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
      const body = url.endsWith("/transcript") ? TRANSCRIPT : DETAIL;
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
    ]);
  });
});
