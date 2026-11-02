// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { CitationList } from "@/components/chat/Citation";
import type { ChatCitation } from "@/lib/chat";

const AUDIO = "123e4567-e89b-12d3-a456-426614174000";
const SEGMENT = "223e4567-e89b-12d3-a456-426614174000";

const CITATION: ChatCitation = {
  index: 1,
  audio_id: AUDIO,
  transcript_version: 1,
  chunk_id: "chunk-1",
  segment_id: SEGMENT,
  t_start: 0,
  t_end: 5,
  label: "[clase sintetica · 2026-11-04 · 00:05]",
  url: `/biblioteca/${AUDIO}?segment=${SEGMENT}`,
};

function LocationProbe() {
  const location = useLocation();
  return <p>{location.pathname + location.search}</p>;
}

afterEach(() => {
  cleanup();
});

describe("CitationList", () => {
  it("links a validated citation to the reader segment", () => {
    render(
      <MemoryRouter>
        <CitationList citations={[CITATION]} />
      </MemoryRouter>,
    );

    const link = screen.getByRole("link", { name: "abrir cita 1 en el lector" });
    expect(link.getAttribute("href")).toBe(`/biblioteca/${AUDIO}?segment=${SEGMENT}`);
    expect(screen.getByText("[1] [clase sintetica · 2026-11-04 · 00:05]")).toBeTruthy();
  });

  it("keeps the server segment label when there are no reliable times", () => {
    render(
      <MemoryRouter>
        <CitationList
          citations={[
            {
              ...CITATION,
              t_start: null,
              t_end: null,
              label: "[clase sintetica · 2026-11-04 · segment 3]",
            },
          ]}
        />
      </MemoryRouter>,
    );

    expect(screen.getByText(/segment 3/)).toBeTruthy();
    expect(screen.queryByText(/00:00/)).toBeNull();
  });

  it("renders an invalid citation as text instead of a link", () => {
    render(
      <MemoryRouter>
        <CitationList citations={[{ ...CITATION, url: "https://evil.example/x" }]} />
      </MemoryRouter>,
    );

    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText("[1] [clase sintetica · 2026-11-04 · 00:05]")).toBeTruthy();
  });

  it("navigates to the reader path when clicked", () => {
    render(
      <MemoryRouter initialEntries={["/gestion/chat"]}>
        <Routes>
          <Route path="/gestion/chat" element={<CitationList citations={[CITATION]} />} />
          <Route path="/biblioteca/:audioId" element={<LocationProbe />} />
        </Routes>
      </MemoryRouter>,
    );

    fireEvent.click(screen.getByRole("link", { name: "abrir cita 1 en el lector" }));
    expect(screen.getByText(`/biblioteca/${AUDIO}?segment=${SEGMENT}`)).toBeTruthy();
  });
});
