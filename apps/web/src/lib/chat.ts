import { ApiHttpError, apiFetch } from "./api";
import { createSseParser } from "./stream";

// D2.6 — Contrato M7. La UI no inventa conversaciones ni respuestas: crea el alcance,
// carga historial persistido, consume el SSE del POST y reconcilia cortes por historial.

export type ChatMode = "all" | "subject" | "class";
export type ChatMessageStatus = "streaming" | "completed" | "failed" | "cancelled";

export interface ConversationScope {
  mode: ChatMode;
  subjectId?: string | null;
  audioId?: string | null;
}

export interface ChatConversation {
  id: string;
  mode: ChatMode;
  subject_id: string | null;
  audio_id: string | null;
  title: string | null;
  created_at: string;
  updated_at: string;
  last_message_at: string | null;
}

export interface ConversationPage {
  items: ChatConversation[];
  next_cursor: string | null;
}

export interface ChatCitation {
  index: number;
  offset?: number;
  audio_id: string;
  transcript_id?: string | null;
  transcript_version: number;
  chunk_id: string;
  segment_id: string | null;
  t_start: number | null;
  t_end: number | null;
  label: string;
  url: string;
  quote?: string | null;
}

export interface ChatMessage {
  id: string;
  role: "user" | "assistant" | "system";
  content: string;
  status: ChatMessageStatus;
  client_message_id: string | null;
  citas: ChatCitation[];
  created_at: string;
}

export interface MessagePage {
  items: ChatMessage[];
  next_cursor: string | null;
}

export type ChatStreamEvent =
  | { type: "snapshot"; messageId: string; status: ChatMessageStatus }
  | { type: "delta"; text: string }
  | { type: "citation"; citation: ChatCitation }
  | {
      type: "done";
      messageId: string;
      conversationId: string;
      status: "completed" | "cancelled";
      sources: Array<Record<string, unknown>>;
    }
  | { type: "error"; code: string; message: string };

/** Una cita por índice: el historial puede repetir el mismo `[Sn]` en varios fragmentos. */
export function dedupeCitations(citations: ChatCitation[]): ChatCitation[] {
  const seen = new Set<number>();
  const unique: ChatCitation[] = [];
  for (const citation of citations) {
    if (seen.has(citation.index)) continue;
    seen.add(citation.index);
    unique.push(citation);
  }
  return unique;
}

function pageParams(cursor: string | null): string {
  const params = new URLSearchParams({ limit: "50" });
  if (cursor !== null) params.set("cursor", cursor);
  return params.toString();
}

export async function fetchConversations(cursor: string | null = null): Promise<ConversationPage> {
  const res = await apiFetch(`/chat/conversations?${pageParams(cursor)}`, { cache: "no-store" });
  return (await res.json()) as ConversationPage;
}

export async function fetchConversationMessages(
  conversationId: string,
  cursor: string | null = null,
): Promise<MessagePage> {
  const res = await apiFetch(
    `/chat/conversations/${encodeURIComponent(conversationId)}/messages?${pageParams(cursor)}`,
    { cache: "no-store" },
  );
  return (await res.json()) as MessagePage;
}

export async function createConversation(scope: ConversationScope): Promise<ChatConversation> {
  const body: Record<string, string | null> = { mode: scope.mode };
  if (scope.mode === "subject") body.subject_id = scope.subjectId ?? null;
  if (scope.mode === "class") body.audio_id = scope.audioId ?? null;
  const res = await apiFetch("/chat/conversations", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
    retries: 0,
  });
  return (await res.json()) as ChatConversation;
}

export async function deleteConversation(conversationId: string): Promise<void> {
  await apiFetch(`/chat/conversations/${encodeURIComponent(conversationId)}`, {
    method: "DELETE",
    retries: 0,
  });
}

export async function cancelChatMessage(
  conversationId: string,
  messageId: string,
): Promise<void> {
  await apiFetch(
    `/chat/conversations/${encodeURIComponent(conversationId)}/messages/${encodeURIComponent(messageId)}/cancel`,
    { method: "POST", retries: 0 },
  );
}

function asRecord(value: unknown): Record<string, unknown> | null {
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function statusOf(value: unknown): ChatMessageStatus | null {
  return value === "streaming" ||
    value === "completed" ||
    value === "failed" ||
    value === "cancelled"
    ? value
    : null;
}

function citationOf(value: unknown): ChatCitation | null {
  const raw = asRecord(value);
  if (raw === null) return null;
  const index = raw.index;
  const audioId = raw.audio_id;
  const version = raw.transcript_version;
  const chunkId = raw.chunk_id;
  const label = raw.label;
  const url = raw.url;
  if (typeof index !== "number" || typeof audioId !== "string") return null;
  if (typeof version !== "number" || typeof chunkId !== "string") return null;
  if (typeof label !== "string" || typeof url !== "string") return null;
  const segmentId = typeof raw.segment_id === "string" ? raw.segment_id : null;
  const tStart = typeof raw.t_start === "number" ? raw.t_start : null;
  const tEnd = typeof raw.t_end === "number" ? raw.t_end : null;
  return {
    index,
    offset: typeof raw.offset === "number" ? raw.offset : undefined,
    audio_id: audioId,
    transcript_id: typeof raw.transcript_id === "string" ? raw.transcript_id : null,
    transcript_version: version,
    chunk_id: chunkId,
    segment_id: segmentId,
    t_start: tStart,
    t_end: tEnd,
    label,
    url,
    quote: typeof raw.quote === "string" ? raw.quote : null,
  };
}

/** Interpreta un frame SSE del chat; un payload inválido se ignora. */
export function parseChatEvent(name: string, data: string): ChatStreamEvent | null {
  let payload: unknown;
  try {
    payload = JSON.parse(data) as unknown;
  } catch {
    return null;
  }
  const raw = asRecord(payload);
  if (raw === null) return null;

  if (name === "snapshot") {
    const messageId = raw.message_id;
    const status = statusOf(raw.status);
    return typeof messageId === "string" && status !== null
      ? { type: "snapshot", messageId, status }
      : null;
  }
  if (name === "delta") {
    return typeof raw.text === "string" ? { type: "delta", text: raw.text } : null;
  }
  if (name === "citation") {
    const citation = citationOf(raw);
    return citation === null ? null : { type: "citation", citation };
  }
  if (name === "done") {
    const messageId = raw.message_id;
    const conversationId = raw.conversation_id;
    const status = raw.status;
    if (typeof messageId !== "string" || typeof conversationId !== "string") return null;
    if (status !== "completed" && status !== "cancelled") return null;
    const sources = Array.isArray(raw.sources)
      ? raw.sources.filter((source): source is Record<string, unknown> => asRecord(source) !== null)
      : [];
    return { type: "done", messageId, conversationId, status, sources };
  }
  if (name === "error") {
    const code = raw.code;
    const message = raw.message;
    return typeof code === "string" && typeof message === "string"
      ? { type: "error", code, message }
      : null;
  }
  return null;
}

export interface SendChatOptions {
  conversationId: string;
  content: string;
  clientMessageId: string;
  signal: AbortSignal;
  lastEventId?: string | null;
  onEvent: (event: ChatStreamEvent, eventId: string | null) => void;
}

/**
 * Envía un turno y consume su SSE. Devuelve `true` solo con `done|error`; un EOF sin
 * terminal es una interrupción y la UI debe reconciliar con historial.
 */
export async function sendChatMessage({
  conversationId,
  content,
  clientMessageId,
  signal,
  lastEventId,
  onEvent,
}: SendChatOptions): Promise<boolean> {
  const headers: Record<string, string> = {
    "content-type": "application/json",
    accept: "text/event-stream",
  };
  if (lastEventId !== null && lastEventId !== undefined) headers["Last-Event-ID"] = lastEventId;
  const res = await apiFetch(
    `/chat/conversations/${encodeURIComponent(conversationId)}/messages`,
    {
      method: "POST",
      headers,
      body: JSON.stringify({ content, client_message_id: clientMessageId }),
      signal,
      retries: 0,
    },
  );
  if (res.body === null) throw new Error("El servidor no devolvió un stream de chat.");

  let terminal = false;
  const parser = createSseParser((frame) => {
    const event = parseChatEvent(frame.event, frame.data);
    if (event === null) return;
    onEvent(event, frame.id);
    if (event.type === "done" || event.type === "error") terminal = true;
  });
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  try {
    while (!terminal) {
      const { done, value } = await reader.read();
      if (done) break;
      parser.push(decoder.decode(value, { stream: true }));
    }
    if (!terminal) {
      parser.push(decoder.decode());
      parser.flush();
    }
  } finally {
    if (terminal) {
      await reader.cancel().catch(() => undefined);
    }
    reader.releaseLock();
  }
  return terminal;
}

export function chatActionError(error: unknown): string {
  if (error instanceof ApiHttpError) {
    if (error.status === 429) {
      return `${error.message} Ya hay demasiados streams abiertos; espera un momento.`;
    }
    if (error.status === 503) {
      return `${error.message} El servicio está ocupado; reintenta en un momento.`;
    }
    return error.message;
  }
  return "la operación de chat falló; revisa tu conexión.";
}

export function modeLabel(mode: ChatMode): string {
  return mode === "all" ? "todas las clases" : mode === "subject" ? "una materia" : "una clase";
}
