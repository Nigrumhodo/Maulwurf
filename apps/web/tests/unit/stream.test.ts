// D2.6 — C-S2-DA-05 (soporte unitario): parser SSE incremental del chat.
import { describe, expect, it } from "vitest";
import { createSseParser } from "@/lib/stream";
import type { SseFrame } from "@/lib/stream";

function collect(): { frames: SseFrame[]; push: (chunk: string) => void; flush: () => void } {
  const frames: SseFrame[] = [];
  const parser = createSseParser((frame) => frames.push(frame));
  return { frames, push: parser.push, flush: parser.flush };
}

describe("createSseParser", () => {
  it("parsea evento, id y datos, e ignora heartbeats", () => {
    const parser = collect();
    parser.push(": ping\nevent: delta\nid: 12\ndata: {\"text\":\"hola\"}\n\n");
    expect(parser.frames).toEqual([
      { event: "delta", id: "12", data: '{"text":"hola"}' },
    ]);
  });

  it("acepta frames partidos entre chunks y CRLF", () => {
    const parser = collect();
    parser.push("event: snap");
    parser.push("shot\r\nid: 0\r\ndata: {}\r\n");
    expect(parser.frames).toEqual([]);
    parser.push("\r\n");
    expect(parser.frames).toEqual([{ event: "snapshot", id: "0", data: "{}" }]);
  });

  it("une datos multilínea y conserva el id opaco", () => {
    const parser = collect();
    parser.push("event: delta\nid: 0007\ndata: linea 1\ndata: linea 2\n\n");
    expect(parser.frames).toEqual([
      { event: "delta", id: "0007", data: "linea 1\nlinea 2" },
    ]);
  });

  it("flush procesa un frame final sin línea vacía", () => {
    const parser = collect();
    parser.push("event: done\ndata: {}");
    expect(parser.frames).toEqual([]);
    parser.flush();
    expect(parser.frames).toEqual([{ event: "done", id: null, data: "{}" }]);
  });
});
