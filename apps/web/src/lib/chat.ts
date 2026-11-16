import { isRecord } from "./json";
import { streamSse } from "./sse";
import type { SseFrame } from "./sse";

/** Chat SSE payloads (`apps/api/app/services/chat_stream.py`, `schemas/citations.py`). */

export interface SendMessageIn {
  content: string;
  client_message_id?: string | null;
}

export interface ChatSnapshot {
  message_id: string;
  status: string;
}

export interface ChatDelta {
  text: string;
}

export interface CitationEvent {
  index: number;
  audio_id: string;
  transcript_version: number;
  chunk_id: string;
  segment_id: string | null;
  t_start: number | null;
  t_end: number | null;
  label: string;
  url: string;
}

export interface CitedSource {
  kind: "transcript";
  audio_id: string;
}

export interface ChatDone {
  message_id: string;
  conversation_id: string;
  status: string;
  sources: CitedSource[];
}

export interface ChatStreamError {
  code: string;
  message: string;
}

export type ChatStreamEvent =
  | { event: "snapshot"; id: string | null; data: ChatSnapshot }
  | { event: "delta"; id: string | null; data: ChatDelta }
  | { event: "citation"; id: string | null; data: CitationEvent }
  | { event: "done"; id: string | null; data: ChatDone }
  | { event: "error"; id: string | null; data: ChatStreamError };

function isNullableNumber(value: unknown): value is number | null {
  return value === null || typeof value === "number";
}

function asSnapshot(value: unknown): ChatSnapshot | null {
  if (!isRecord(value)) return null;
  if (typeof value.message_id !== "string" || typeof value.status !== "string") return null;
  return { message_id: value.message_id, status: value.status };
}

function asDelta(value: unknown): ChatDelta | null {
  if (!isRecord(value) || typeof value.text !== "string") return null;
  return { text: value.text };
}

function asCitation(value: unknown): CitationEvent | null {
  if (!isRecord(value)) return null;
  if (typeof value.index !== "number" || typeof value.transcript_version !== "number") return null;
  if (typeof value.audio_id !== "string" || typeof value.chunk_id !== "string") return null;
  if (typeof value.label !== "string" || typeof value.url !== "string") return null;
  if (
    value.segment_id !== undefined &&
    value.segment_id !== null &&
    typeof value.segment_id !== "string"
  ) {
    return null;
  }
  if (!isNullableNumber(value.t_start ?? null) || !isNullableNumber(value.t_end ?? null)) {
    return null;
  }
  return {
    index: value.index,
    audio_id: value.audio_id,
    transcript_version: value.transcript_version,
    chunk_id: value.chunk_id,
    segment_id: typeof value.segment_id === "string" ? value.segment_id : null,
    t_start: typeof value.t_start === "number" ? value.t_start : null,
    t_end: typeof value.t_end === "number" ? value.t_end : null,
    label: value.label,
    url: value.url,
  };
}

function asSource(value: unknown): CitedSource | null {
  if (!isRecord(value) || typeof value.audio_id !== "string") return null;
  if (value.kind !== undefined && value.kind !== "transcript") return null;
  return { kind: "transcript", audio_id: value.audio_id };
}

function asDone(value: unknown): ChatDone | null {
  if (!isRecord(value) || !Array.isArray(value.sources)) return null;
  if (typeof value.message_id !== "string" || typeof value.conversation_id !== "string") return null;
  if (typeof value.status !== "string") return null;
  const sources: CitedSource[] = [];
  for (const item of value.sources) {
    const source = asSource(item);
    if (source === null) return null;
    sources.push(source);
  }
  return {
    message_id: value.message_id,
    conversation_id: value.conversation_id,
    status: value.status,
    sources,
  };
}

function asStreamError(value: unknown): ChatStreamError | null {
  if (!isRecord(value) || typeof value.code !== "string" || typeof value.message !== "string") {
    return null;
  }
  return { code: value.code, message: value.message };
}

/** Narrow one chat frame. A known event with a bad payload is a contract error. */
export function parseChatFrame(frame: SseFrame): ChatStreamEvent {
  const id = frame.id;
  switch (frame.event) {
    case "snapshot": {
      const data = asSnapshot(frame.data);
      if (data === null) throw new Error("El evento snapshot no coincide con el contrato.");
      return { event: "snapshot", id, data };
    }
    case "delta": {
      const data = asDelta(frame.data);
      if (data === null) throw new Error("El evento delta no coincide con el contrato.");
      return { event: "delta", id, data };
    }
    case "citation": {
      const data = asCitation(frame.data);
      if (data === null) throw new Error("El evento citation no coincide con el contrato.");
      return { event: "citation", id, data };
    }
    case "done": {
      const data = asDone(frame.data);
      if (data === null) throw new Error("El evento done no coincide con el contrato.");
      return { event: "done", id, data };
    }
    case "error": {
      const data = asStreamError(frame.data);
      if (data === null) throw new Error("El evento error no coincide con el contrato.");
      return { event: "error", id, data };
    }
    default:
      throw new Error(`Evento SSE de chat no reconocido (${frame.event}).`);
  }
}

/** POST `/chat/conversations/{id}/messages`. `EventSource` cannot send this request. */
export async function* streamChatMessage(
  conversationId: string,
  message: SendMessageIn,
  signal?: AbortSignal,
): AsyncGenerator<ChatStreamEvent> {
  const frames = streamSse(
    `/chat/conversations/${encodeURIComponent(conversationId)}/messages`,
    {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({
        content: message.content,
        client_message_id: message.client_message_id,
      }),
      signal,
    },
  );
  for await (const frame of frames) {
    yield parseChatFrame(frame);
  }
}
