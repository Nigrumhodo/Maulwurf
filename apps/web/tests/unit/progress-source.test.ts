// D2.3 — U-S2-DA-03: transporte. SSE entrega estado y cierra con un terminal; tras fallos
// acotados activa sondeo de `GET /audios/{id}`; el sondeo agotado pide reintento explícito.
import { beforeEach, describe, expect, it, vi } from "vitest";
import { createProgressSource } from "@/lib/sse";
import type { ProgressEventSource } from "@/lib/sse";
import type { ProgressEvent, ProgressSnapshot } from "@/lib/progress";

const PROCESSING: ProgressSnapshot = {
  attempt: {
    status: "transcribing",
    received_bytes: 1024,
    fragments_done: 1,
    fragments_total: 4,
  },
  stages: { index: "pending", analyze: "not_requested" },
  aggregate: "processing",
};

const READY: ProgressSnapshot = {
  attempt: {
    status: "succeeded",
    received_bytes: 4096,
    fragments_done: 4,
    fragments_total: 4,
  },
  stages: { index: "succeeded", analyze: "not_requested" },
  aggregate: "ready",
};

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
    this.fire(type, { data: JSON.stringify(data) } as unknown as Event);
  }

  fail(): void {
    this.fire("error", new Event("error"));
  }

  private fire(type: string, event: Event): void {
    for (const listener of this.listeners.get(type) ?? []) listener(event);
  }
}

beforeEach(() => {
  FakeEventSource.instances = [];
});

function create(
  events: ProgressEvent[],
  overrides: {
    fetchSnapshot?: (audioId: string, signal: AbortSignal) => Promise<ProgressSnapshot | null>;
    pollIntervalMs?: number;
    maxPollFailures?: number;
    onTransportError?: (message: string) => void;
    transports?: string[];
  } = {},
) {
  const source = createProgressSource(
    "a1",
    {
      onEvent: (event) => events.push(event),
      onTransport: (transport) => overrides.transports?.push(transport),
      onTransportError: overrides.onTransportError,
    },
    {
      createEventSource: () => new FakeEventSource("/audios/a1/progress"),
      fetchSnapshot: overrides.fetchSnapshot ?? (async () => READY),
      pollIntervalMs: overrides.pollIntervalMs ?? 0,
      maxPollFailures: overrides.maxPollFailures ?? 5,
      random: () => 0,
    },
  );
  const fake = FakeEventSource.instances.at(-1);
  if (fake === undefined) throw new Error("no se creó el EventSource falso");
  return { source, fake };
}

describe("createProgressSource · SSE", () => {
  it("usa la URL relativa y cierra al recibir done, ignorando eventos tardíos", () => {
    const events: ProgressEvent[] = [];
    const { source, fake } = create(events);
    expect(fake.url).toBe("/audios/a1/progress");

    fake.emit("snapshot", PROCESSING);
    fake.emit("progress", {
      received_bytes: 2048,
      fragments_done: 2,
      fragments_total: 4,
    });
    fake.emit("done", {
      aggregate: "ready",
      stages: { index: "succeeded", analyze: "not_requested" },
    });
    fake.emit("progress", {
      received_bytes: 9999,
      fragments_done: 4,
      fragments_total: 4,
    });

    expect(events.map((event) => event.type)).toEqual(["snapshot", "progress", "done"]);
    expect(fake.closed).toBe(true);
    source.close();
  });

  it("ignora frames malformados sin inventar estado", () => {
    const events: ProgressEvent[] = [];
    const { source, fake } = create(events);

    fake.emit("snapshot", { aggregate: "processing" });
    fake.emit("progress", { received_bytes: -1 });
    fake.emit("error", {});

    expect(events).toEqual([]);
    source.close();
  });
});

describe("createProgressSource · fallback", () => {
  it("activa sondeo tras fallos SSE acotados y termina con el detalle", async () => {
    const events: ProgressEvent[] = [];
    const transports: string[] = [];
    const { source, fake } = create(events, { transports });

    fake.fail();
    fake.fail();

    await vi.waitFor(() => expect(events.some((event) => event.type === "done")).toBe(true));
    expect(transports).toEqual(["sse", "polling"]);
    expect(fake.closed).toBe(true);
    expect(events.at(-1)).toEqual({
      type: "done",
      aggregate: "ready",
      stages: READY.stages,
    });
    source.close();
  });

  it("permite activar el sondeo manualmente", async () => {
    const events: ProgressEvent[] = [];
    const { source, fake } = create(events);
    source.usePolling();

    await vi.waitFor(() => expect(events.some((event) => event.type === "done")).toBe(true));
    expect(fake.closed).toBe(true);
    source.close();
  });

  it("detiene el sondeo tras el máximo de fallos y pide reintento explícito", async () => {
    const events: ProgressEvent[] = [];
    let transportError: string | null = null;
    const { source, fake } = create(events, {
      fetchSnapshot: async () => {
        throw new Error("sin conexión");
      },
      maxPollFailures: 2,
      onTransportError: (message) => {
        transportError = message;
      },
    });

    fake.fail();
    fake.fail();

    await vi.waitFor(() => expect(transportError).not.toBeNull());
    expect(events).toEqual([]);
    source.close();
  });

  it("cerrar durante el sondeo aborta la petición sin publicar eventos", async () => {
    const events: ProgressEvent[] = [];
    let signal: AbortSignal | null = null;
    const { source } = create(events, {
      fetchSnapshot: (_audioId, nextSignal) =>
        new Promise<ProgressSnapshot | null>((_resolve, reject) => {
          signal = nextSignal;
          nextSignal.addEventListener("abort", () =>
            reject(new DOMException("Aborted", "AbortError")),
          );
        }),
    });

    source.usePolling();
    await vi.waitFor(() => expect(signal).not.toBeNull());
    source.close();

    expect((signal as AbortSignal | null)?.aborted).toBe(true);
    expect(events).toEqual([]);
  });
});
