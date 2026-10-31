import { apiFetch } from "./api";

// D2.1 — Capacidades de ingesta publicadas por el spike (`GET /ingestion/capabilities`,
// A2.3). `limits_validated === false` significa que el servidor NO aceptará subidas: los
// límites llegan nulos/vacíos y la UI debe mostrar un estado honesto en vez de inventar
// valores provisionales (S1). La versión del aviso de privacidad la inyecta el endpoint.
export interface IngestionTtl {
  upload_start_minutes: number | null;
  receive_minutes: number | null;
  asr_minutes: number | null;
}

export interface IngestionAsr {
  provider: string;
  model: string;
  timestamp_precision: string | null;
}

export interface IngestionCapabilities {
  source_report: string | null;
  source_version: string | null;
  limits_validated: boolean;
  privacy_notice_version: string | null;
  max_upload_bytes: number | null;
  max_duration_seconds: number | null;
  accepted_input_formats: string[];
  languages: string[];
  language_policy: string;
  ttl: IngestionTtl;
  asr: IngestionAsr;
  active_slots: number | null;
  required_stages: string[];
}

/** Lee las capacidades publicadas; propaga el `ApiHttpError` para que la UI decida. */
export async function fetchCapabilities(): Promise<IngestionCapabilities> {
  const res = await apiFetch("/ingestion/capabilities");
  return (await res.json()) as IngestionCapabilities;
}
