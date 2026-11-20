// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ClassSummary } from "@/components/transcript/ClassSummary";
import type { LibraryAudioDetail } from "@/lib/library";
import type { ReaderTranscript } from "@/lib/reader";

const DETAIL: LibraryAudioDetail = {
  id: "a1",
  subject: { id: "sub1", name: "materia", color: "#112233" },
  title: "clase",
  class_date: "2026-11-04",
  class_timezone: "America/Bogota",
  language_code: "es",
  duration_seconds: 20,
  created_at: "2026-11-04T12:00:00Z",
  attempt: null,
  stages: { index: "succeeded", analyze: "succeeded" },
  aggregate_status: "ready",
  audio_retained: false,
  analysis_backfill_available: false,
  transcript: {
    version: 1,
    timestamp_precision: "segment",
    char_count: 20,
    quality_warnings: [{ code: "low_confidence", count: 2, total: 5 }],
    summary: "resumen persistido",
    topics: ["tema uno", "tema dos"],
    analysis_version: 2,
  },
  exports: { txt: true, srt: true },
};

const TRANSCRIPT: ReaderTranscript = {
  audio_id: "a1",
  version: 1,
  timestamp_precision: "segment",
  char_count: 20,
  summary: "resumen persistido",
  topics: ["tema uno", "tema dos"],
  analysis_version: 2,
  segments: [],
};

afterEach(() => {
  cleanup();
});

describe("ClassSummary", () => {
  it("shows the persisted summary, topics, version and ASR warning", () => {
    render(<ClassSummary detail={DETAIL} transcript={TRANSCRIPT} />);

    expect(screen.getByText("resumen persistido")).toBeTruthy();
    expect(screen.getByText("tema uno")).toBeTruthy();
    expect(screen.getByText("tema dos")).toBeTruthy();
    expect(screen.getByText("análisis v2")).toBeTruthy();
    expect(screen.getByText(/baja confianza \(2\/5\)/)).toBeTruthy();
  });

  it("does not invent a summary when it was not requested", () => {
    render(
      <ClassSummary
        detail={{
          ...DETAIL,
          stages: { index: "succeeded", analyze: "not_requested" },
          transcript: { ...DETAIL.transcript!, summary: null, topics: [], analysis_version: null },
        }}
        transcript={{ ...TRANSCRIPT, summary: null, topics: [], analysis_version: null }}
      />,
    );

    expect(screen.getByText(/resumen no solicitado/)).toBeTruthy();
  });

  it("collapses long topic lists behind a toggle", () => {
    const manyTopics = Array.from({ length: 15 }, (_, index) => `tema ${index + 1}`);
    render(
      <ClassSummary
        detail={{ ...DETAIL, transcript: { ...DETAIL.transcript!, topics: manyTopics } }}
        transcript={{ ...TRANSCRIPT, topics: manyTopics }}
      />,
    );

    expect(screen.getByText("tema 1")).toBeTruthy();
    expect(screen.queryByText("tema 15")).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "ver 3 temas más" }));
    expect(screen.getByText("tema 15")).toBeTruthy();
  });
});
