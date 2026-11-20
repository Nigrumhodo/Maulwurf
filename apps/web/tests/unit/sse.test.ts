import { afterEach, describe, expect, it, vi } from "vitest";
import { parseProgressFrame, streamAudioProgress } from "@/lib/audios";
import { clearCsrfToken } from "@/lib/csrf";
import { streamSse } from "@/lib/sse";
import type { SseFrame } from "@/lib/sse";
import { MAX_BUFFER_CHARS } from "@/lib/stream";

const encoder = new TextEncoder();

function sseResponse(chunks: string[], hang = false): Response {
  const stream = new ReadableStream<Uint8Array>({
    start(controller) {
      for (const chunk of chunks) controller.enqueue(encoder.encode(chunk));
      if (!hang) controller.close();
    },
  });
  return new Response(stream, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

afterEach(() => {
  clearCsrfToken();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("streamSse", () => {
  it("junta fragmentos, ignora :ping y entrega los frames", async () => {
    const chunks = [
      "event: snapshot\nid: 1\ndata: {\"ok\":true}\n\n: ping\n\n",
      "event: delta\nid: 2\ndata: {\"text\":\"ho\"}\n\n",
    ];
    vi.stubGlobal("fetch", vi.fn().mockResolvedValue(sseResponse(chunks)));

    const frames: SseFrame[] = [];
    for await (const frame of streamSse("/audios/a1/progress")) frames.push(frame);

    expect(frames).toEqual([
      { event: "snapshot", id: "1", data: { ok: true } },
      { event: "delta", id: "2", data: { text: "ho" } },
    ]);
  });

  it("cancela el cuerpo si el consumidor se detiene", async () => {
    let cancelled = false;
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(
          encoder.encode('event: snapshot\nid: 1\ndata: {"aggregate":"processing"}\n\n'),
        );
      },
      cancel() {
        cancelled = true;
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(stream, {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        }),
      ),
    );

    const frames = streamSse("/audios/a1/progress");
    await frames.next();
    await frames.return(undefined);

    expect(cancelled).toBe(true);
  });

  it("rechaza si la señal ya abortó, sin abrir el stream", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const controller = new AbortController();
    controller.abort();

    await expect(streamSse("/audios/a1/progress", { signal: controller.signal }).next()).rejects.toMatchObject({
      name: "AbortError",
    });
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("corta el stream si un frame excede MAX_BUFFER_CHARS", async () => {
    let cancelled = false;
    const oversized = "data: " + "a".repeat(MAX_BUFFER_CHARS + 16);
    const stream = new ReadableStream<Uint8Array>({
      start(controller) {
        controller.enqueue(encoder.encode(oversized));
      },
      cancel() {
        cancelled = true;
      },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(stream, {
          status: 200,
          headers: { "content-type": "text/event-stream" },
        }),
      ),
    );

    const frames = streamSse("/audios/a1/progress");
    await expect(frames.next()).rejects.toThrow("sse_frame_too_large");
    expect(cancelled).toBe(true);
  });
});

describe("streamAudioProgress", () => {
  it("lee un GET de progreso y estrecha el snapshot", async () => {
    const snapshot = {
      attempt: {
        status: "receiving",
        received_bytes: 12,
        fragments_done: null,
        fragments_total: 4,
      },
      stages: { index: "not_requested" },
      aggregate: "processing",
    };
    const body =
      `event: snapshot\nid: 1\ndata: ${JSON.stringify(snapshot)}\n\n` +
      `event: done\nid: 2\ndata: ${JSON.stringify({
        aggregate: "ready",
        stages: { index: "succeeded" },
      })}\n\n`;
    const fetchMock = vi.fn().mockResolvedValue(sseResponse([body]));
    vi.stubGlobal("fetch", fetchMock);

    const events = [];
    for await (const event of streamAudioProgress("audio 1")) events.push(event);

    expect(events.map((event) => event.event)).toEqual(["snapshot", "done"]);
    expect(fetchMock).toHaveBeenCalledWith(
      "/audios/audio%201/progress",
      expect.objectContaining({ method: "GET", credentials: "include" }),
    );
  });
});

describe("parse frames", () => {
  it("rechaza un snapshot de progreso sin bytes", () => {
    expect(() =>
      parseProgressFrame({ event: "snapshot", id: "1", data: { aggregate: "processing" } }),
    ).toThrow(/snapshot/);
  });
});
