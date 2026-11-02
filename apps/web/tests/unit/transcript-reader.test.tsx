// @vitest-environment jsdom
//
// D2.5 — C-S2-DA-04: anclaje `?segment=`, highlight/foco, tiempos condicionados, resumen
// no solicitado y exportaciones habilitadas solo por contrato.
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { TranscriptReader } from "@/components/TranscriptReader";
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
      id: "s1",
      ordinal: 1,
      text: "primer segmento",
      char_start: 0,
      char_end: 7,
      t_start: 0,
      t_end: 5,
    },
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
});

function renderReader(
  activeSegmentId: string | null,
  overrides: Partial<LibraryAudioDetail> = {},
  transcript: ReaderTranscript = TRANSCRIPT,
) {
  return render(
    <MemoryRouter initialEntries={[`/biblioteca/a1${activeSegmentId ? `?segment=${activeSegmentId}` : ""}`]}>
      <TranscriptReader
        detail={{ ...DETAIL, ...overrides }}
        transcript={transcript}
        activeSegmentId={activeSegmentId}
      />
    </MemoryRouter>,
  );
}

describe("TranscriptReader", () => {
  it("enlaza, resalta y enfoca el segmento solicitado con su tiempo válido", () => {
    renderReader("s2");

    const active = document.getElementById("segment-s2");
    expect(active).not.toBeNull();
    expect(active?.getAttribute("aria-current")).toBe("location");
    expect(screen.getByText("0:05")).toBeTruthy();
    expect(
      screen.getByRole("link", { name: "enlazar segmento 2" }).getAttribute("href"),
    ).toBe("/biblioteca/a1?segment=s2");
    expect(screen.getByText("primer segmento")).toBeTruthy();
    expect(screen.getByText("segundo segmento")).toBeTruthy();
    expect(screen.getByRole("button", { name: "volver atrás" })).toBeTruthy();
    expect(screen.getByRole("link", { name: /volver a la biblioteca/i })).toBeTruthy();
  });

  it("muestra el ordinal cuando no hay tiempos fiables", () => {
    renderReader(null, { duration_seconds: null }, { ...TRANSCRIPT, timestamp_precision: "none" });

    expect(screen.getByText("segmento 1")).toBeTruthy();
    expect(screen.getByText("segmento 2")).toBeTruthy();
    expect(screen.queryByText("0:05")).toBeNull();
  });

  it("avisa si el segmento pedido no existe sin inventar resaltado", () => {
    renderReader("missing");

    expect(screen.getByText(/segmento no encontrado/i)).toBeTruthy();
    expect(document.querySelector('[aria-current="location"]')).toBeNull();
  });

  it("mantiene el resumen vacío y condiciona las exportaciones", () => {
    renderReader(null, { exports: { txt: true, srt: false } });

    expect(screen.getByText(/resumen no solicitado para esta clase/i)).toBeTruthy();
    expect(screen.getByRole("link", { name: "descargar txt" }).getAttribute("href")).toBe(
      "/audios/a1/transcript.txt",
    );
    expect(
      (screen.getByRole("button", { name: "descargar srt" }) as HTMLButtonElement).disabled,
    ).toBe(true);
    expect(screen.getByText(/srt no disponible/i)).toBeTruthy();
  });
});
