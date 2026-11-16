// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ClassStatus } from "@/components/transcript/ClassStatus";
import type { LibraryAudioDetail } from "@/lib/library";

function detail(overrides: Partial<LibraryAudioDetail> = {}): LibraryAudioDetail {
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
    stages: { index: "succeeded", analyze: "not_requested" },
    aggregate_status: "ready",
    audio_retained: false,
    analysis_backfill_available: true,
    transcript: null,
    exports: { txt: false, srt: false },
    ...overrides,
  };
}

afterEach(() => {
  cleanup();
});

describe("ClassStatus", () => {
  it("offers the F1 backfill without reuploading audio", () => {
    const onBackfill = vi.fn();
    render(
      <ClassStatus
        detail={detail()}
        busy={false}
        onBackfill={onBackfill}
        onRetryIndex={vi.fn()}
      />,
    );

    expect(screen.getByText(/solo el texto persistido/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "analizar esta clase" }));
    expect(onBackfill).toHaveBeenCalledOnce();
  });

  it("labels a failed analysis as a retry", () => {
    render(
      <ClassStatus
        detail={detail({ stages: { index: "succeeded", analyze: "failed" } })}
        busy={false}
        onBackfill={vi.fn()}
        onRetryIndex={vi.fn()}
      />,
    );

    expect(screen.getByRole("button", { name: "reintentar análisis" })).toBeTruthy();
  });

  it("shows a separate index retry only when the index failed", () => {
    const onRetryIndex = vi.fn();
    render(
      <ClassStatus
        detail={detail({
          stages: { index: "failed", analyze: "succeeded" },
          analysis_backfill_available: false,
        })}
        busy={false}
        onBackfill={vi.fn()}
        onRetryIndex={onRetryIndex}
      />,
    );

    fireEvent.click(screen.getByRole("button", { name: "reintentar índice" }));
    expect(onRetryIndex).toHaveBeenCalledOnce();
  });

  it("shows cleanup pending as a warning", () => {
    render(
      <ClassStatus
        detail={detail({
          attempt: {
            attempt_id: "att1",
            status: "transcript_committed_cleanup_pending",
            received_bytes: 10,
            fragments_done: 1,
            fragments_total: 1,
            cleanup_status: "pending",
          },
        })}
        busy={false}
        onBackfill={vi.fn()}
        onRetryIndex={vi.fn()}
      />,
    );

    expect(screen.getByText(/limpieza pendiente/)).toBeTruthy();
  });
});
