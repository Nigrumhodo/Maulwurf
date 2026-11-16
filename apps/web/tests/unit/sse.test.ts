import { afterEach, describe, expect, it, vi } from "vitest";
import { parseProgressFrame, streamAudioProgress } from "@/lib/audios";
import { parseChatFrame, streamChatMessage } from "@/lib/chat";
import { setCsrfToken, clearCsrfToken } from "@/lib/csrf";
import { streamSse } from "@/lib/sse";
import type { SseFrame } from "@/lib/sse";

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

describe("streamChatMessage", () => {
  it("hace POST con CSRF y estrecha delta y done", async () => {
    setCsrfToken("tok-a");
    const citation = {
      index: 1,
      audio_id: "a",
      transcript_version: 1,
      chunk_id: "c",
      segment_id: null,
      t_start: 0,
      t_end: 1.5,
      label: "clase",
      url: "/audios/a",
    };
    const body = [
      'event: delta\nid: 2\ndata: {"text":"hola"}\n\n',
      `event: citation\nid: 3\ndata: ${JSON.stringify(citation)}\n\n`,
      `event: done\nid: 4\ndata: ${JSON.stringify({
        message_id: "m",
        conversation_id: "conv",
        status: "completed",
        sources: [{ kind: "transcript", audio_id: "a" }],
      })}\n\n`,
    ].join("");
    const fetchMock = vi.fn().mockResolvedValue(sseResponse([body]));
    vi.stubGlobal("fetch", fetchMock);

    const events = [];
    for await (const event of streamChatMessage("conv", { content: "hola", client_message_id: "c1" })) {
      events.push(event);
    }

    expect(events.map((event) => event.event)).toEqual(["delta", "citation", "done"]);
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
    expect((init.headers as Headers).get("x-csrf-token")).toBe("tok-a");
    expect(JSON.parse(String(init.body))).toEqual({ content: "hola", client_message_id: "c1" });
  });
});

describe("parse frames", () => {
  it("rechaza un snapshot de progreso sin bytes", () => {
    expect(() =>
      parseProgressFrame({ event: "snapshot", id: "1", data: { aggregate: "processing" } }),
    ).toThrow(/snapshot/);
  });

  it("rechaza un delta de chat sin texto", () => {
    expect(() => parseChatFrame({ event: "delta", id: null, data: {} })).toThrow(/delta/);
  });
});
