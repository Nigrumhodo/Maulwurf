import { apiFetch } from "./api";
import { isRecord, readJson } from "./json";

// D4.3 — Hybrid transcript search client for the global Ctrl+K palette (`GET /search`).

export interface SearchSubject {
  id: string;
  name: string;
  color: string;
}

export interface SearchHit {
  chunk_id: string;
  audio_id: string;
  subject: SearchSubject;
  class_date: string;
  snippet: string;
  segment_id: string | null;
  t_start: number | null;
  t_end: number | null;
  score_rrf: number;
}

export interface SearchResponse {
  results: SearchHit[];
  took_ms: number;
  next_cursor: string | null;
}

function isSearchHit(value: unknown): value is SearchHit {
  if (!isRecord(value) || !isRecord(value.subject)) return false;
  return (
    typeof value.chunk_id === "string" &&
    typeof value.audio_id === "string" &&
    typeof value.subject.id === "string" &&
    typeof value.subject.name === "string" &&
    typeof value.subject.color === "string" &&
    typeof value.class_date === "string" &&
    typeof value.snippet === "string" &&
    (value.segment_id === null || typeof value.segment_id === "string") &&
    (value.t_start === null || typeof value.t_start === "number") &&
    (value.t_end === null || typeof value.t_end === "number") &&
    typeof value.score_rrf === "number"
  );
}

export interface SearchOptions {
  subjectId?: string;
  limit?: number;
  cursor?: string | null;
  signal?: AbortSignal;
}

export async function searchTranscripts(
  query: string,
  options: SearchOptions = {},
): Promise<SearchResponse> {
  const params = new URLSearchParams({ q: query });
  if (options.subjectId !== undefined && options.subjectId !== "") {
    params.set("subject_id", options.subjectId);
  }
  if (options.limit !== undefined) params.set("limit", String(options.limit));
  if (options.cursor !== undefined && options.cursor !== null && options.cursor !== "") {
    params.set("cursor", options.cursor);
  }
  const res = await apiFetch(`/search?${params.toString()}`, {
    signal: options.signal,
  });
  const payload = await readJson(res);
  if (
    !isRecord(payload) ||
    !Array.isArray(payload.results) ||
    typeof payload.took_ms !== "number"
  ) {
    throw new Error("La respuesta de búsqueda no coincide con el contrato.");
  }
  const results = payload.results.map((hit) => {
    if (!isSearchHit(hit)) {
      throw new Error("Un resultado de búsqueda no coincide con el contrato.");
    }
    return hit;
  });
  const nextCursor =
    payload.next_cursor === undefined || payload.next_cursor === null
      ? null
      : typeof payload.next_cursor === "string"
        ? payload.next_cursor
        : null;
  return { results, took_ms: payload.took_ms, next_cursor: nextCursor };
}

/** Destination reader route inside the authenticated Gestión layout. */
export function searchHitHref(hit: SearchHit): string {
  const base = `/gestion/biblioteca/${encodeURIComponent(hit.audio_id)}`;
  if (hit.segment_id === null || hit.segment_id === "") return base;
  return `${base}?segment=${encodeURIComponent(hit.segment_id)}`;
}

/** Honest timestamp formatting: never invents `00:00` when timestamps are withheld (G2-X). */
export function formatHitTimestamp(tStart: number | null): string {
  if (tStart === null || !Number.isFinite(tStart) || tStart < 0) {
    return "sin marca temporal";
  }
  const totalSeconds = Math.floor(tStart);
  const hours = Math.floor(totalSeconds / 3600);
  const minutes = Math.floor((totalSeconds % 3600) / 60);
  const seconds = totalSeconds % 60;
  if (hours > 0) {
    return `${hours}:${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
  }
  return `${minutes}:${String(seconds).padStart(2, "0")}`;
}
