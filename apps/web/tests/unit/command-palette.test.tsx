// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { useState } from "react";
import { MemoryRouter, useLocation } from "react-router-dom";
import { CommandPalette } from "@/components/search/CommandPalette";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function PaletteHarness() {
  const [open, setOpen] = useState(false);
  const location = useLocation();
  return (
    <div>
      <span data-testid="loc">{`${location.pathname}${location.search}`}</span>
      <CommandPalette open={open} onOpenChange={setOpen} />
    </div>
  );
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("CommandPalette (D4.3)", () => {
  it("opens with Ctrl+K, searches transcripts via GET /search, renders honest timestamps, and navigates on Enter", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      if (String(input).startsWith("/search?")) {
        return json({
          results: [
            {
              chunk_id: "c-1",
              audio_id: "aud-9",
              subject: { id: "s-1", name: "cálculo", color: "#f5c542" },
              class_date: "2026-11-18",
              snippet: "el parcial entra derivadas implícitas",
              segment_id: "seg-4",
              t_start: 125,
              t_end: 132,
              score_rrf: 0.9,
            },
            {
              chunk_id: "c-2",
              audio_id: "aud-10",
              subject: { id: "s-2", name: "historia", color: "#3b82f6" },
              class_date: "2026-11-17",
              snippet: "sin marca temporal en modo text-only",
              segment_id: null,
              t_start: null,
              t_end: null,
              score_rrf: 0.7,
            },
          ],
          took_ms: 14,
          next_cursor: null,
        });
      }
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(
      <MemoryRouter initialEntries={["/"]}>
        <PaletteHarness />
      </MemoryRouter>,
    );

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    const input = await screen.findByLabelText(/buscar en maulwurf/i);

    fireEvent.change(input, { target: { value: "parcial" } });

    expect(await screen.findByText(/el parcial entra derivadas implícitas/i)).toBeTruthy();
    expect(screen.getByText("2:05")).toBeTruthy();
    expect(screen.getByText("sin marca temporal")).toBeTruthy();

    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() =>
      expect(screen.getByTestId("loc").textContent).toBe("/gestion/biblioteca/aud-9?segment=seg-4"),
    );

    fireEvent.keyDown(window, { key: "k", ctrlKey: true });
    expect(await screen.findByRole("dialog")).toBeTruthy();
    fireEvent.keyDown(window, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});
