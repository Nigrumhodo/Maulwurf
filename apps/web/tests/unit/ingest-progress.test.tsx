// @vitest-environment jsdom
//
// D2.3 — U-S2-DA-03: componente de progreso. Muestra bytes y fragmentos medidos, nunca
// porcentajes; la limpieza pendiente no es éxito; el terminal cierra el transporte; el
// sondeo manual usa el detalle sin mejoras inventadas.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, cleanup, fireEvent, render, screen } from "@testing-library/react";
import { IngestProgress } from "@/components/IngestProgress";
import type { ProgressEventSource } from "@/lib/sse";

class FakeEventSource implements ProgressEventSource {
  static instances: FakeEventSource[] = [];
  readonly listeners = new Map<string, Set<(event: Event) => void>>();
  closed = false;

  constructor(readonly url: string) {
    FakeEventSource.instances.push(this);
  }

  addEventListener(type: string, listener: (event: Event) => void): void {
    const set = this.listeners.get(type) ?? new Set();
    set.add(listener);
    this.listeners.set(type, set);
  }

  close(): void {
    this.closed = true;
  }

  emit(type: string, data: unknown): void {
    for (const listener of this.listeners.get(type) ?? []) {
      listener({ data: JSON.stringify(data) } as unknown as Event);
    }
  }
}

function current(): FakeEventSource {
  const source = FakeEventSource.instances.at(-1);
  if (source === undefined) throw new Error("no se creó el EventSource falso");
  return source;
}

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal("EventSource", FakeEventSource);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("IngestProgress", () => {
  it("muestra bytes/fragmentos reales, limpieza pendiente y cierra al terminar", () => {
    const { container } = render(<IngestProgress audioId="a1" />);
    const source = current();

    act(() => {
      source.emit("snapshot", {
        attempt: {
          status: "transcript_committed_cleanup_pending",
          received_bytes: 2048,
          fragments_done: 2,
          fragments_total: 5,
        },
        stages: { index: "pending", analyze: "not_requested" },
        aggregate: "processing",
      });
    });

    expect(screen.getByText(/limpieza pendiente/i)).toBeTruthy();
    expect(screen.getByText(/recibidos 2 KiB/i)).toBeTruthy();
    expect(screen.getByText("fragmentos 2/5")).toBeTruthy();
    expect(container.textContent).not.toContain("%");

    act(() => {
      source.emit("progress", {
        received_bytes: 4096,
        fragments_done: 3,
        fragments_total: 5,
      });
    });
    expect(screen.getByText(/recibidos 4 KiB/i)).toBeTruthy();
    expect(screen.getByText("fragmentos 3/5")).toBeTruthy();

    act(() => {
      source.emit("done", {
        aggregate: "ready",
        stages: { index: "succeeded", analyze: "not_requested" },
      });
    });

    expect(screen.getByText(/marcó la clase como lista/i)).toBeTruthy();
    expect(screen.queryByText(/limpieza pendiente/i)).toBeNull();
    expect(source.closed).toBe(true);
  });

  it("un terminal de error no presenta la clase como lista", () => {
    render(<IngestProgress audioId="a1" />);
    const source = current();

    act(() => {
      source.emit("error", { code: "aggregate_failed", detail: "requires_reupload" });
    });

    expect(screen.getByText(/progreso detenido/i)).toBeTruthy();
    expect(screen.getByText("aggregate_failed")).toBeTruthy();
    expect(screen.getByText(/requires_reupload/)).toBeTruthy();
    expect(screen.queryByText(/marcó la clase como lista/i)).toBeNull();
    expect(source.closed).toBe(true);
  });

  it("activa el sondeo manual con el detalle no-store y termina sin porcentajes", async () => {
    const calls: Array<{ url: string; init: RequestInit }> = [];
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        calls.push({ url: String(input), init: init ?? {} });
        return json({
          attempt: {
            status: "succeeded",
            received_bytes: 8192,
            fragments_done: 4,
            fragments_total: 4,
          },
          stages: { index: "succeeded", analyze: "not_requested" },
          aggregate_status: "ready",
        });
      }),
    );

    render(<IngestProgress audioId="a1" />);
    const source = current();

    fireEvent.click(screen.getByRole("button", { name: "usar sondeo" }));

    expect(await screen.findByText(/marcó la clase como lista/i)).toBeTruthy();
    expect(calls[0]?.url).toBe("/audios/a1");
    expect(calls[0]?.init.cache).toBe("no-store");
    expect(source.closed).toBe(true);
    expect(screen.getByText(/recibidos 8 KiB/i)).toBeTruthy();
  });
});
