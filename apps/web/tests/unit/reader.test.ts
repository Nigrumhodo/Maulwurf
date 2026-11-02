// @vitest-environment jsdom
//
// D2.5 — C-S2-DA-04 (soporte unitario): tiempos honestos, resumen condicionado,
// lectura del contrato y descarga SRT con `409 srt_unavailable` como estado, no error.
import { afterEach, describe, expect, it, vi } from "vitest";
import type { LibraryAudioDetail } from "@/lib/library";
import {
  downloadSrt,
  fetchTranscriptReader,
  formatClock,
  segmentLabel,
  summaryState,
  validSegmentTime,
} from "@/lib/reader";
import type { ReaderSegment, ReaderTranscript } from "@/lib/reader";

const SEGMENT: ReaderSegment = {
  id: "s1",
  ordinal: 1,
  text: "texto",
  char_start: 0,
  char_end: 5,
  t_start: 5.4,
  t_end: 10,
};

function detail(stages: { index: string; analyze: string }): LibraryAudioDetail {
  return {
    id: "a1",
    subject: { id: "sub1", name: "materia", color: "#112233" },
    title: "clase",
    class_date: "2026-11-04",
    class_timezone: "America/Bogota",
    language_code: "es",
    duration_seconds: 20,
    created_at: "2026-11-04T12:00:00Z",
    attempt: null,
    stages,
    aggregate_status: "ready",
    audio_retained: false,
    transcript: {
      version: 1,
      timestamp_precision: "segment",
      char_count: 5,
      quality_warnings: [],
      summary: null,
      topics: [],
      analysis_version: null,
    },
    exports: { txt: true, srt: true },
  };
}

function transcript(overrides: Partial<ReaderTranscript> = {}): ReaderTranscript {
  return {
    audio_id: "a1",
    version: 1,
    timestamp_precision: "segment",
    char_count: 5,
    summary: null,
    topics: [],
    analysis_version: null,
    segments: [SEGMENT],
    ...overrides,
  };
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("tiempos del lector", () => {
  it("solo acepta tiempos con precisión fiable, orden y dentro de la duración", () => {
    expect(validSegmentTime(SEGMENT, "segment", 20)).toBe(true);
    expect(validSegmentTime(SEGMENT, "word", 20)).toBe(true);
    expect(validSegmentTime(SEGMENT, "none", 20)).toBe(false);
    expect(validSegmentTime({ ...SEGMENT, t_start: null }, "segment", 20)).toBe(false);
    expect(validSegmentTime({ ...SEGMENT, t_start: 10, t_end: 10 }, "segment", 20)).toBe(false);
    expect(validSegmentTime({ ...SEGMENT, t_start: -1 }, "segment", 20)).toBe(false);
    expect(validSegmentTime({ ...SEGMENT, t_end: 20.5 }, "segment", 20)).toBe(false);
    expect(validSegmentTime(SEGMENT, "segment", null)).toBe(true);
  });

  it("usa mm:ss o h:mm:ss solo cuando el tiempo es válido", () => {
    expect(formatClock(5.9)).toBe("0:05");
    expect(formatClock(65)).toBe("1:05");
    expect(formatClock(3661)).toBe("1:01:01");
    expect(segmentLabel(SEGMENT, "segment", 20)).toBe("0:05");
    expect(segmentLabel(SEGMENT, "none", 20)).toBe("segmento 1");
    expect(segmentLabel({ ...SEGMENT, t_end: 99 }, "segment", 20)).toBe("segmento 1");
  });
});

describe("resumen honesto", () => {
  it("no pide contenido cuando el análisis no se solicitó", () => {
    expect(summaryState(detail({ index: "succeeded", analyze: "not_requested" }), transcript())).toEqual({
      text: null,
      note: "resumen no solicitado para esta clase",
    });
  });

  it("distingue resumen pendiente, disponible y ausente", () => {
    expect(
      summaryState(detail({ index: "succeeded", analyze: "pending" }), transcript()).note,
    ).toContain("generando");
    expect(
      summaryState(
        detail({ index: "succeeded", analyze: "succeeded" }),
        transcript({ summary: "resumen real", topics: ["uno"] }),
      ),
    ).toEqual({ text: "resumen real", note: null });
    expect(
      summaryState(detail({ index: "succeeded", analyze: "failed" }), transcript()).note,
    ).toContain("no está disponible");
  });
});

describe("contrato del lector", () => {
  it("lee el endpoint relativo con no-store", async () => {
    const reader = transcript();
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify(reader), {
        status: 200,
        headers: { "content-type": "application/json" },
      }),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchTranscriptReader("a1")).resolves.toEqual(reader);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/audios/a1/transcript");
    expect(init.cache).toBe("no-store");
  });

  it("un 409 srt_unavailable deshabilita el SRT sin lanzar error", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ error: { code: "srt_unavailable", message: "sin tiempos" } }), {
          status: 409,
          headers: { "content-type": "application/json" },
        }),
      ),
    );

    await expect(downloadSrt("a1")).resolves.toBe("unavailable");
  });

  it("descarga y revoca el object URL del SRT", async () => {
    const create = vi.fn().mockReturnValue("blob:srt");
    const revoke = vi.fn();
    vi.stubGlobal("URL", { createObjectURL: create, revokeObjectURL: revoke });
    const click = vi.spyOn(HTMLAnchorElement.prototype, "click").mockImplementation(() => {});
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response("1\n00:00:00,000 --> 00:00:01,000\nhola\n", {
          status: 200,
          headers: { "content-type": "application/x-subrip" },
        }),
      ),
    );

    await expect(downloadSrt("a1")).resolves.toBe("downloaded");
    expect(create).toHaveBeenCalledOnce();
    expect(revoke).toHaveBeenCalledWith("blob:srt");
    expect(click).toHaveBeenCalledOnce();
  });
});
