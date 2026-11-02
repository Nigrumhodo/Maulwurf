import { apiFetch } from "./api";
import type { ApiHttpError } from "./api";
import type { AudioCreated } from "./upload";

// D2.4 — Contrato de biblioteca (A2.9/A2.8). El listado ya trae la materia, el agregado,
// las etapas y el último intento; el detalle añade la versión de transcripción y las
// exportaciones. Aquí no se inventan estados ni se deduce una transcripción de un agregado
// que no la garantiza.

// S2 no expone cursor: se pide el máximo permitido y la UI avisa si se alcanza.
export const LIBRARY_LIMIT = 100;

export interface LibrarySubject {
  id: string;
  name: string;
  color: string;
}

export interface LibraryAttempt {
  attempt_id: string;
  status: string;
  received_bytes: number;
  fragments_done: number | null;
  fragments_total: number | null;
  cleanup_status: string;
}

export interface LibraryAudio {
  id: string;
  subject: LibrarySubject;
  title: string | null;
  class_date: string;
  class_timezone: string;
  language_code: string;
  duration_seconds: number | null;
  created_at: string;
  attempt: LibraryAttempt | null;
  stages: Record<string, string>;
  aggregate_status: string;
}

export interface LibraryTranscript {
  version: number;
  timestamp_precision: string;
  char_count: number;
}

export interface LibraryExports {
  txt: boolean;
  srt: boolean;
}

export interface LibraryAudioDetail extends LibraryAudio {
  audio_retained: boolean;
  transcript: LibraryTranscript | null;
  exports: LibraryExports;
}

export type RetryStage = "index" | "analyze";

export interface RetryAccepted {
  audio_id: string;
  transcript_version: number;
  runs: Array<{ stage: string; run_id: string; status: string }>;
}

export interface SubjectGroup {
  id: string;
  name: string;
  color: string;
  items: LibraryAudio[];
}

/** Lee `GET /audios`; sin cursor en el contrato S2 se pide el máximo permitido. */
export async function fetchAudios(subjectId?: string): Promise<LibraryAudio[]> {
  const params = new URLSearchParams({ limit: String(LIBRARY_LIMIT) });
  if (subjectId !== undefined && subjectId !== "") params.set("subject_id", subjectId);
  const res = await apiFetch(`/audios?${params.toString()}`, { cache: "no-store" });
  const data = (await res.json()) as { items: LibraryAudio[] };
  return data.items;
}

/** Lee `GET /audios/{id}`: versión de transcripción, exportaciones y estados derivados. */
export async function fetchAudioDetail(audioId: string): Promise<LibraryAudioDetail> {
  const res = await apiFetch(`/audios/${encodeURIComponent(audioId)}`, { cache: "no-store" });
  return (await res.json()) as LibraryAudioDetail;
}

/** `POST /audios/{id}/retry`: solo etapas de texto sobre una transcripción vigente. */
export async function retryAudio(
  audioId: string,
  stages: RetryStage[],
  transcriptVersion: number,
): Promise<RetryAccepted> {
  const res = await apiFetch(`/audios/${encodeURIComponent(audioId)}/retry`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ stages, transcript_version: transcriptVersion }),
  });
  return (await res.json()) as RetryAccepted;
}

/** `POST /audios/{id}/reupload`: nueva sesión efímera solo sin transcripción utilizable. */
export async function reuploadAudio(audioId: string): Promise<AudioCreated> {
  const res = await apiFetch(`/audios/${encodeURIComponent(audioId)}/reupload`, {
    method: "POST",
  });
  return (await res.json()) as AudioCreated;
}

/** Agrupa preservando el orden del servidor (más recientes primero) dentro de cada materia. */
export function groupBySubject(items: readonly LibraryAudio[]): SubjectGroup[] {
  const groups: SubjectGroup[] = [];
  const byId = new Map<string, SubjectGroup>();
  for (const item of items) {
    let group = byId.get(item.subject.id);
    if (group === undefined) {
      group = {
        id: item.subject.id,
        name: item.subject.name,
        color: item.subject.color,
        items: [],
      };
      byId.set(item.subject.id, group);
      groups.push(group);
    }
    group.items.push(item);
  }
  return groups;
}

/** `ready` y `partial` implican texto utilizable; `processing`/`failed` no. */
export function hasUsableTranscript(audio: LibraryAudio): boolean {
  return audio.aggregate_status === "ready" || audio.aggregate_status === "partial";
}

/** Etapas que el contrato de retry puede reintentar en una clase parcial. */
export function failedStages(audio: LibraryAudio): RetryStage[] {
  const stages: RetryStage[] = [];
  for (const stage of ["index", "analyze"] as const) {
    const status = audio.stages[stage];
    if (status === "failed" || status === "cancelled") stages.push(stage);
  }
  return stages;
}

export function isCleanupPending(audio: LibraryAudio): boolean {
  return audio.attempt?.status === "transcript_committed_cleanup_pending";
}

export function audioLabel(audio: LibraryAudio): string {
  const title = audio.title?.trim();
  return title !== undefined && title !== "" ? title : "clase sin título";
}

export function stageLabel(stage: RetryStage): string {
  return stage === "index" ? "índice" : "análisis";
}

export interface LibraryActionError {
  message: string;
  /** El estado local quedó obsoleto: conviene recargar el listado. */
  refresh?: boolean;
}

/** Traduce errores de retry/reupload a mensajes honestos para la biblioteca. */
export function libraryActionError(error: ApiHttpError): LibraryActionError {
  if (error.code === "transcript_unavailable") {
    return {
      message: `${error.message} La clase ya no tiene texto utilizable; vuelve a subir el audio.`,
      refresh: true,
    };
  }
  if (error.code === "invalid_transition") {
    return {
      message: `${error.message} Actualiza la biblioteca para ver el estado real.`,
      refresh: true,
    };
  }
  if (error.code === "not_found") {
    return { message: "la clase ya no existe; actualiza la biblioteca.", refresh: true };
  }
  if (error.status === 429 || error.status === 503) {
    return { message: `${error.message} Reintenta en un momento.` };
  }
  return { message: error.message };
}
