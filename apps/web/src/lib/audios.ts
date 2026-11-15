import { streamSse } from "./sse";
import type { SseFrame } from "./sse";
import { isRecord } from "./json";

/** Wire types for the audio list and detail routes (`apps/api/app/routers/audios.py`). */

export interface AudioSubjectRef {
  id: string;
  name: string;
  color: string;
}

export interface AttemptOut {
  attempt_id: string;
  status: string;
  received_bytes: number;
  fragments_done: number | null;
  fragments_total: number | null;
  cleanup_status: string;
}

export interface AudioListItemOut {
  id: string;
  subject: AudioSubjectRef;
  title: string | null;
  class_date: string;
  class_timezone: string;
  language_code: string;
  duration_seconds: number | null;
  created_at: string;
  attempt: AttemptOut | null;
  stages: Record<string, string>;
  aggregate_status: string;
}

export interface AudioListOut {
  items: AudioListItemOut[];
}

export interface QualityWarning {
  code: "repetition" | "empty_segments" | "low_confidence";
  count?: number;
  total?: number;
}

export interface TranscriptOut {
  version: number;
  timestamp_precision: string;
  char_count: number;
  quality_warnings: QualityWarning[];
  summary: string | null;
  topics: string[];
  analysis_version: number | null;
}

export interface ExportsOut {
  txt: boolean;
  srt: boolean;
}

export interface AudioDetailOut {
  id: string;
  subject: AudioSubjectRef;
  title: string | null;
  class_date: string;
  class_timezone: string;
  language_code: string;
  duration_seconds: number | null;
  created_at: string;
  attempt: AttemptOut | null;
  stages: Record<string, string>;
  aggregate_status: string;
  audio_retained: boolean;
  analysis_backfill_available: boolean;
  transcript: TranscriptOut | null;
  exports: ExportsOut;
}

/** SSE payloads from `apps/api/app/services/progress.py`. Bytes and fragments only. */

export interface AttemptPayload {
  status: string | null;
  received_bytes: number;
  fragments_done: number | null;
  fragments_total: number | null;
}

export interface SnapshotPayload {
  attempt: AttemptPayload;
  stages: Record<string, string>;
  aggregate: string;
}

export interface ProgressPayload {
  received_bytes: number;
  fragments_done: number | null;
  fragments_total: number | null;
}

export interface TerminalDone {
  aggregate: string;
  stages: Record<string, string>;
}

export interface TerminalError {
  code: string;
  detail: string | null;
}

export type ProgressStreamEvent =
  | { event: "snapshot"; id: string | null; data: SnapshotPayload }
  | { event: "progress"; id: string | null; data: ProgressPayload }
  | { event: "done"; id: string | null; data: TerminalDone }
  | { event: "error"; id: string | null; data: TerminalError };

function isNullableNumber(value: unknown): value is number | null {
  return value === null || typeof value === "number";
}

function isStringMap(value: unknown): value is Record<string, string> {
  return isRecord(value) && Object.values(value).every((item) => typeof item === "string");
}

function asAttemptPayload(value: unknown): AttemptPayload | null {
  if (!isRecord(value) || typeof value.received_bytes !== "number") return null;
  if (value.status !== null && typeof value.status !== "string") return null;
  if (!isNullableNumber(value.fragments_done) || !isNullableNumber(value.fragments_total)) {
    return null;
  }
  return {
    status: typeof value.status === "string" ? value.status : null,
    received_bytes: value.received_bytes,
    fragments_done: value.fragments_done,
    fragments_total: value.fragments_total,
  };
}

function asSnapshotPayload(value: unknown): SnapshotPayload | null {
  if (!isRecord(value) || typeof value.aggregate !== "string" || !isStringMap(value.stages)) {
    return null;
  }
  const attempt = asAttemptPayload(value.attempt);
  if (attempt === null) return null;
  return { attempt, stages: value.stages, aggregate: value.aggregate };
}

function asProgressPayload(value: unknown): ProgressPayload | null {
  if (!isRecord(value) || typeof value.received_bytes !== "number") return null;
  if (!isNullableNumber(value.fragments_done) || !isNullableNumber(value.fragments_total)) {
    return null;
  }
  return {
    received_bytes: value.received_bytes,
    fragments_done: value.fragments_done,
    fragments_total: value.fragments_total,
  };
}

function asTerminalDone(value: unknown): TerminalDone | null {
  if (!isRecord(value) || typeof value.aggregate !== "string" || !isStringMap(value.stages)) {
    return null;
  }
  return { aggregate: value.aggregate, stages: value.stages };
}

function asTerminalError(value: unknown): TerminalError | null {
  if (!isRecord(value) || typeof value.code !== "string") return null;
  if (value.detail !== undefined && value.detail !== null && typeof value.detail !== "string") {
    return null;
  }
  return { code: value.code, detail: typeof value.detail === "string" ? value.detail : null };
}

/** Narrow one progress frame. A known event with a bad payload is a contract error. */
export function parseProgressFrame(frame: SseFrame): ProgressStreamEvent {
  const id = frame.id;
  switch (frame.event) {
    case "snapshot": {
      const data = asSnapshotPayload(frame.data);
      if (data === null) throw new Error("El evento snapshot no coincide con el contrato.");
      return { event: "snapshot", id, data };
    }
    case "progress": {
      const data = asProgressPayload(frame.data);
      if (data === null) throw new Error("El evento progress no coincide con el contrato.");
      return { event: "progress", id, data };
    }
    case "done": {
      const data = asTerminalDone(frame.data);
      if (data === null) throw new Error("El evento done no coincide con el contrato.");
      return { event: "done", id, data };
    }
    case "error": {
      const data = asTerminalError(frame.data);
      if (data === null) throw new Error("El evento error no coincide con el contrato.");
      return { event: "error", id, data };
    }
    default:
      throw new Error(`Evento SSE de progreso no reconocido (${frame.event}).`);
  }
}

/** GET `/audios/{id}/progress`. The server polls; cancel the generator to close it. */
export async function* streamAudioProgress(
  audioId: string,
  signal?: AbortSignal,
): AsyncGenerator<ProgressStreamEvent> {
  for await (const frame of streamSse(`/audios/${encodeURIComponent(audioId)}/progress`, {
    method: "GET",
    signal,
  })) {
    yield parseProgressFrame(frame);
  }
}
