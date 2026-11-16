import { abortError, apiFetch } from "./api";

/** One SSE frame. Comment keep-alives (`: ping`) are not frames. */
export interface SseFrame<T = unknown> {
  event: string;
  id: string | null;
  data: T;
}

export interface SseRequestInit {
  method?: "GET" | "POST";
  body?: string;
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

function parseSseBlock(block: string): SseFrame | null {
  let eventName = "message";
  let id: string | null = null;
  const dataLines: string[] = [];
  let sawField = false;

  for (const rawLine of block.split(/\r?\n/)) {
    if (rawLine === "" || rawLine.startsWith(":")) continue;
    sawField = true;
    const separator = rawLine.indexOf(":");
    const field = separator === -1 ? rawLine : rawLine.slice(0, separator);
    const value = separator === -1 ? "" : rawLine.slice(separator + 1).replace(/^ /, "");
    if (field === "event") eventName = value;
    else if (field === "data") dataLines.push(value);
    else if (field === "id" && !value.includes("\0")) id = value;
  }

  if (!sawField) return null;

  const rawData = dataLines.join("\n");
  if (rawData === "") return { event: eventName, id, data: null };
  try {
    const data: unknown = JSON.parse(rawData);
    return { event: eventName, id, data };
  } catch {
    return { event: eventName, id, data: rawData };
  }
}

/**
 * Read `text/event-stream` until the body ends, the consumer stops, or `signal` aborts.
 * Stopping the generator cancels the body so a progress poll does not stay open.
 */
export async function* readSse(res: Response, signal?: AbortSignal): AsyncGenerator<SseFrame> {
  if (signal?.aborted) throw abortError(signal);
  const body = res.body;
  if (body === null) throw new Error("El stream SSE no tiene cuerpo.");

  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";

  const onAbort = () => {
    void reader.cancel(signal?.reason).catch(() => undefined);
  };
  signal?.addEventListener("abort", onAbort);

  try {
    while (true) {
      if (signal?.aborted) throw abortError(signal);
      const { done, value } = await reader.read();
      if (signal?.aborted) throw abortError(signal);
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split(/\r?\n\r?\n/);
      buffer = blocks.pop() ?? "";
      for (const block of blocks) {
        const frame = parseSseBlock(block);
        if (frame !== null) yield frame;
      }
    }
    buffer += decoder.decode();
    if (buffer.trim() !== "") {
      const frame = parseSseBlock(buffer);
      if (frame !== null) yield frame;
    }
  } finally {
    signal?.removeEventListener("abort", onAbort);
    await reader.cancel().catch(() => undefined);
  }
}

/**
 * Open one SSE response through `apiFetch` (cookies, CSRF on POST, one 429 retry on GET).
 * Progress is GET. Chat is POST because `EventSource` cannot send a body or `X-CSRF-Token`.
 */
export async function* streamSse(
  path: string,
  init: SseRequestInit = {},
): AsyncGenerator<SseFrame> {
  if (init.signal?.aborted) throw abortError(init.signal);
  const res = await apiFetch(path, {
    method: init.method ?? "GET",
    body: init.body,
    headers: { accept: "text/event-stream", ...init.headers },
    signal: init.signal,
  });
  const contentType = res.headers.get("content-type") ?? "";
  if (!contentType.includes("text/event-stream")) {
    throw new Error("La respuesta no es un stream SSE.");
  }
  yield* readSse(res, init.signal);
}
