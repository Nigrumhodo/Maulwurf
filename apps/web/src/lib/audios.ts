import { apiFetch } from "./api";
import { isRecord, readJson } from "./json";

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

function isNullableNumber(value: unknown): value is number | null {
  return value === null || typeof value === "number";
}

function isStringMap(value: unknown): value is Record<string, string> {
  return isRecord(value) && Object.values(value).every((item) => typeof item === "string");
}

function isAttempt(value: unknown): value is AttemptOut {
  if (!isRecord(value)) return false;
  return (
    typeof value.attempt_id === "string" &&
    typeof value.status === "string" &&
    typeof value.received_bytes === "number" &&
    isNullableNumber(value.fragments_done) &&
    isNullableNumber(value.fragments_total) &&
    typeof value.cleanup_status === "string"
  );
}

function isAudioListItem(value: unknown): value is AudioListItemOut {
  if (!isRecord(value) || !isRecord(value.subject)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.subject.id === "string" &&
    typeof value.subject.name === "string" &&
    typeof value.subject.color === "string" &&
    (value.title === null || typeof value.title === "string") &&
    typeof value.class_date === "string" &&
    typeof value.class_timezone === "string" &&
    typeof value.language_code === "string" &&
    isNullableNumber(value.duration_seconds) &&
    typeof value.created_at === "string" &&
    (value.attempt === null || isAttempt(value.attempt)) &&
    isStringMap(value.stages) &&
    typeof value.aggregate_status === "string"
  );
}

/** `GET /audios`. The list is the same source for the library and the dashboard. */
export async function fetchAudios(): Promise<AudioListItemOut[]> {
  const res = await apiFetch("/audios");
  const payload = await readJson(res);
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new Error("El listado de clases no coincide con el contrato.");
  }
  return payload.items.map((item) => {
    if (!isAudioListItem(item)) {
      throw new Error("Una clase del listado no coincide con el contrato.");
    }
    return item;
  });
}
