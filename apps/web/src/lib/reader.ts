import { ApiHttpError, apiFetch } from "./api";
import type { LibraryAudioDetail } from "./library";

// D2.5 — Contrato del lector (C-S2-DA-04). El backend entrega el texto en segmentos
// ordenados con identificadores estables; el frontend decide qué se puede afirmar sobre
// tiempos y exportaciones sin inventar datos.

export interface ReaderSegment {
  id: string;
  ordinal: number;
  text: string;
  char_start: number;
  char_end: number;
  t_start: number | null;
  t_end: number | null;
}

export interface ReaderTranscript {
  audio_id: string;
  version: number;
  timestamp_precision: string;
  char_count: number;
  summary: string | null;
  topics: string[];
  analysis_version: number | null;
  segments: ReaderSegment[];
}

export async function fetchTranscriptReader(audioId: string): Promise<ReaderTranscript> {
  const res = await apiFetch(`/audios/${encodeURIComponent(audioId)}/transcript`, {
    cache: "no-store",
  });
  return (await res.json()) as ReaderTranscript;
}

const TIMED_PRECISIONS = new Set(["word", "segment"]);

/** Un tiempo solo es visible si la transcripción lo declara fiable y está dentro del audio. */
export function validSegmentTime(
  segment: ReaderSegment,
  timestampPrecision: string,
  durationSeconds: number | null,
): boolean {
  if (!TIMED_PRECISIONS.has(timestampPrecision)) return false;
  const start = segment.t_start;
  const end = segment.t_end;
  if (start === null || end === null) return false;
  if (!Number.isFinite(start) || !Number.isFinite(end)) return false;
  if (start < 0 || end <= start) return false;
  if (durationSeconds !== null && end > durationSeconds + 0.001) return false;
  return true;
}

/** `mm:ss` o `h:mm:ss`; el servidor ya validó el valor antes de llegar aquí. */
export function formatClock(seconds: number): string {
  const total = Math.floor(seconds);
  const hours = Math.floor(total / 3600);
  const minutes = Math.floor((total % 3600) / 60);
  const rest = total % 60;
  const pad = (value: number) => String(value).padStart(2, "0");
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(rest)}` : `${minutes}:${pad(rest)}`;
}

/** Etiqueta visible del segmento: tiempo válido o el ordinal honesto. */
export function segmentLabel(
  segment: ReaderSegment,
  timestampPrecision: string,
  durationSeconds: number | null,
): string {
  return validSegmentTime(segment, timestampPrecision, durationSeconds)
    ? formatClock(segment.t_start ?? 0)
    : `segmento ${segment.ordinal}`;
}

export function summaryState(
  detail: LibraryAudioDetail,
  transcript: ReaderTranscript,
): { text: string | null; note: string | null } {
  if (detail.stages.analyze === "not_requested") {
    return { text: null, note: "resumen no solicitado para esta clase" };
  }
  const text = transcript.summary?.trim() ?? "";
  if (text !== "") return { text, note: null };
  if (detail.stages.analyze === "pending" || detail.stages.analyze === "running") {
    return { text: null, note: "el resumen todavía se está generando" };
  }
  return { text: null, note: "el resumen no está disponible" };
}

export type SrtDownloadResult = "downloaded" | "unavailable";

function triggerDownload(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = filename;
  try {
    document.body.append(anchor);
    anchor.click();
  } finally {
    anchor.remove();
    // D2.8: release the blob URL even when the browser refuses the click.
    URL.revokeObjectURL(url);
  }
}

/**
 * SRT condicionado por el contrato. Un `409 srt_unavailable` es un estado honesto (carrera
 * con una versión sin tiempos), no un fallo de red; se traduce para deshabilitar el botón.
 */
export async function downloadSrt(audioId: string): Promise<SrtDownloadResult> {
  try {
    const res = await apiFetch(`/audios/${encodeURIComponent(audioId)}/transcript.srt`, {
      cache: "no-store",
    });
    triggerDownload(await res.blob(), "transcript.srt");
    return "downloaded";
  } catch (error) {
    if (error instanceof ApiHttpError && error.code === "srt_unavailable") return "unavailable";
    throw error;
  }
}
