import i18n from "@/i18n";
import { apiFetch } from "./api";
import { isRecord, readJson } from "./json";

// D2.1 — Capacidades de ingesta publicadas por el spike (`GET /ingestion/capabilities`,
// A2.3). `limits_validated === false` significa que el servidor NO aceptará subidas: los
// límites llegan nulos/vacíos y la UI debe mostrar un estado honesto en vez de inventar
// valores provisionales (S1). La versión del aviso de privacidad la inyecta el endpoint
// (`IngestionCapabilitiesOut` la exige siempre).
export interface IngestionTtl {
  upload_start_minutes: number | null;
  receive_minutes: number | null;
  asr_minutes: number | null;
}

export interface IngestionAsr {
  provider: string;
  model: string;
  timestamp_precision: "none" | null;
}

export interface IngestionCapabilities {
  source_report: string | null;
  source_version: string | null;
  limits_validated: boolean;
  privacy_notice_version: string;
  max_upload_bytes: number | null;
  max_duration_seconds: number | null;
  accepted_input_formats: string[];
  languages: string[];
  language_policy: "explicit_select_required_no_multi";
  ttl: IngestionTtl;
  ttl_source_report: string | null;
  ttl_source_version: string | null;
  asr: IngestionAsr;
  active_slots: number | null;
  required_stages: string[];
}

function isNullableNumber(value: unknown): value is number | null {
  return value === null || typeof value === "number";
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isStringList(value: unknown): value is string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string");
}

function isIngestionCapabilities(value: unknown): value is IngestionCapabilities {
  if (!isRecord(value) || !isRecord(value.ttl) || !isRecord(value.asr)) return false;
  return (
    isNullableString(value.source_report) &&
    isNullableString(value.source_version) &&
    typeof value.limits_validated === "boolean" &&
    typeof value.privacy_notice_version === "string" &&
    isNullableNumber(value.max_upload_bytes) &&
    isNullableNumber(value.max_duration_seconds) &&
    isStringList(value.accepted_input_formats) &&
    isStringList(value.languages) &&
    value.language_policy === "explicit_select_required_no_multi" &&
    isNullableNumber(value.ttl.upload_start_minutes) &&
    isNullableNumber(value.ttl.receive_minutes) &&
    isNullableNumber(value.ttl.asr_minutes) &&
    isNullableString(value.ttl_source_report) &&
    isNullableString(value.ttl_source_version) &&
    typeof value.asr.provider === "string" &&
    typeof value.asr.model === "string" &&
    (value.asr.timestamp_precision === null || value.asr.timestamp_precision === "none") &&
    isNullableNumber(value.active_slots) &&
    isStringList(value.required_stages)
  );
}

/** Lee las capacidades publicadas; propaga el `ApiHttpError` para que la UI decida. */
export async function fetchCapabilities(): Promise<IngestionCapabilities> {
  const res = await apiFetch("/ingestion/capabilities");
  const payload = await readJson(res);
  if (!isIngestionCapabilities(payload)) {
    throw new Error(i18n.t("study:capabilitiesLib.mismatch"));
  }
  return payload;
}
