// D2.6 — Parser SSE incremental para `fetch` POST. El chat no puede usar `EventSource`
// porque el envío es un POST con CSRF; además, los frames pueden partirse entre chunks.
// El parser no interpreta el JSON: solo entrega eventos nombrados con su `id` opaco.

export interface SseFrame {
  event: string;
  id: string | null;
  data: string;
}

export interface SseParser {
  push(chunk: string): void;
  flush(): void;
}

function parseFrame(raw: string): SseFrame | null {
  let event = "message";
  let id: string | null = null;
  const data: string[] = [];
  let hasField = false;

  for (const line of raw.split("\n")) {
    if (line === "" || line.startsWith(":")) continue;
    const colon = line.indexOf(":");
    const field = colon === -1 ? line : line.slice(0, colon);
    let value = colon === -1 ? "" : line.slice(colon + 1);
    if (value.startsWith(" ")) value = value.slice(1);
    hasField = true;
    if (field === "event") event = value;
    else if (field === "id") id = value === "" ? null : value;
    else if (field === "data") data.push(value);
  }

  if (!hasField || data.length === 0) return null;
  return { event, id, data: data.join("\n") };
}

/** Parser tolerante a LF/CRLF, comentarios de heartbeat y frames incompletos. */
export function createSseParser(onFrame: (frame: SseFrame) => void): SseParser {
  let buffer = "";

  function drain(final: boolean): void {
    buffer = buffer.replace(/\r\n/g, "\n");
    let boundary = buffer.indexOf("\n\n");
    while (boundary !== -1) {
      const raw = buffer.slice(0, boundary);
      buffer = buffer.slice(boundary + 2);
      const frame = parseFrame(raw);
      if (frame !== null) onFrame(frame);
      boundary = buffer.indexOf("\n\n");
    }
    if (final && buffer.trim() !== "") {
      const frame = parseFrame(buffer);
      if (frame !== null) onFrame(frame);
      buffer = "";
    }
  }

  return {
    push(chunk: string) {
      buffer += chunk;
      drain(false);
    },
    flush() {
      drain(true);
    },
  };
}
