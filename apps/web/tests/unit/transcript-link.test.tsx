// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { TranscriptLink } from "@/components/transcript/TranscriptLink";
import type { Task, TaskEvidence, TaskQualityState } from "@/lib/tasks";

const AUDIO_ID = "123e4567-e89b-12d3-a456-426614174000";
const SEGMENT_ID = "223e4567-e89b-12d3-a456-426614174000";

const TASK: Task = {
  id: "323e4567-e89b-12d3-a456-426614174000",
  audio_id: AUDIO_ID,
  subject: null,
  source: "extraction",
  tipo: "tarea",
  titulo: "Resolver la guía",
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
      audio_id: AUDIO_ID,
      transcript_version: 1,
      segment_id: SEGMENT_ID,
      char_start: 0,
      char_end: 7,
    },
  ],
  evidence: [],
  pending_revision: null,
};

const EVIDENCE: TaskEvidence = {
  segment_id: SEGMENT_ID,
  ordinal: 2,
  char_start: 0,
  char_end: 7,
  quote: "entrega",
  t_start: 5,
  t_end: 10,
};

const QUALITY: TaskQualityState = {
  signals: [],
  unavailable: false,
  timestamp_precision: "segment",
  duration_seconds: 20,
};

function renderLink(
  task: Task = TASK,
  evidence: TaskEvidence = EVIDENCE,
  quality: TaskQualityState | null = QUALITY,
) {
  return render(
    <MemoryRouter>
      <TranscriptLink task={task} evidence={evidence} quality={quality} />
    </MemoryRouter>,
  );
}

afterEach(() => {
  cleanup();
});

describe("TranscriptLink", () => {
  it("uses the measured time when the reader metadata makes it valid", () => {
    renderLink();

    const link = screen.getByRole("link", {
      name: "ver segmento 2 en la clase",
    });
    expect(link.textContent).toContain("0:05");
    expect(link.getAttribute("href")).toBe(
      `/biblioteca/${AUDIO_ID}?segment=${SEGMENT_ID}`,
    );
  });

  it("falls back to the honest ordinal without reliable metadata", () => {
    renderLink(TASK, EVIDENCE, {
      ...QUALITY,
      timestamp_precision: "none",
    });

    expect(screen.getByRole("link").textContent).toContain("segmento 2");
    expect(screen.queryByText("0:05")).toBeNull();
  });

  it("falls back to the ordinal when the quality read is unavailable", () => {
    renderLink(TASK, EVIDENCE, null);

    expect(screen.getByRole("link").textContent).toContain("segmento 2");
  });

  it("does not invent a link for a task without a class", () => {
    renderLink({ ...TASK, audio_id: null, sources: [] }, EVIDENCE);

    expect(screen.queryByRole("link")).toBeNull();
  });
});

