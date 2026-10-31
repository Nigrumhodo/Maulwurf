import { LANGUAGES } from "./languages";
import type { ApiHttpError } from "./api";
import type { IngestionCapabilities } from "./capabilities";

// D2.1 — Lógica pura del formulario de subida (U-S2-DA-01): validación, construcción
// del body de `POST /audios` y mapeo de errores del contrato a la UI. Se mantiene sin
// DOM para poder probarla como el resto de helpers (api/status/validation/subjects).
export interface UploadDraft {
  subjectId: string;
  classDate: string;
  classTimezone: string;
  languageCode: string;
  title: string;
  teacher: string;
  cloudAccepted: boolean;
  voiceAcknowledged: boolean;
}

export type UploadField = "subjectId" | "classDate" | "classTimezone" | "languageCode";

export interface UploadErrors {
  fields: Partial<Record<UploadField, string>>;
  consent?: string;
  form?: string;
}

export interface LanguageOption {
  code: string;
  label: string;
}

/** Convierte los códigos publicados por capabilities en opciones con etiqueta legible. */
export function languageOptions(codes: readonly string[]): LanguageOption[] {
  return codes.map((code) => ({
    code,
    label: LANGUAGES.find((language) => language.code === code)?.label ?? code,
  }));
}

/**
 * Códigos que el servidor publica para el select. `multi` es un valor de política, no
 * un idioma: ofrecerlo rompería la regla D2.1 de selección explícita sin multidioma.
 */
export function publishedLanguageCodes(
  capabilities: IngestionCapabilities | null,
): string[] {
  return (capabilities?.languages ?? []).filter((code) => code !== "multi");
}

/** La subida solo es posible con límites validados y una versión de aviso vigente. */
export function capabilitiesUsable(capabilities: IngestionCapabilities | null): boolean {
  return (
    capabilities !== null &&
    capabilities.limits_validated &&
    capabilities.privacy_notice_version !== null
  );
}

/**
 * Valida el borrador ANTES de reservar: materia, fecha, zona e idioma SIN preselección y
 * los DOS consentimientos obligatorios. Si el servidor no publicó límites, ningún campo
 * habilita el envío (estado honesto en vez de valores provisionales).
 */
export function validateUpload(
  draft: UploadDraft,
  capabilities: IngestionCapabilities | null,
): UploadErrors {
  const fields: Partial<Record<UploadField, string>> = {};
  if (!capabilitiesUsable(capabilities)) {
    return {
      fields,
      form: "el servidor no ha publicado límites de ingesta; la subida aún no está disponible.",
    };
  }
  const languageCodes = publishedLanguageCodes(capabilities);
  if (languageCodes.length === 0) {
    return {
      fields,
      form: "el servidor no ha publicado idiomas de audio; la subida aún no está disponible.",
    };
  }
  if (draft.subjectId === "") fields.subjectId = "elige una materia";
  if (draft.classDate === "") fields.classDate = "indica la fecha de clase";
  if (draft.classTimezone === "") fields.classTimezone = "elige la zona horaria";
  if (draft.languageCode === "") {
    fields.languageCode = "elige el idioma del audio";
  } else if (!languageCodes.includes(draft.languageCode)) {
    fields.languageCode = "elige uno de los idiomas publicados por el servidor";
  }
  if (!draft.cloudAccepted || !draft.voiceAcknowledged) {
    return { fields, consent: "acepta ambos consentimientos para continuar" };
  }
  return { fields };
}

/** Forma exacta de `AudioCreate` (`apps/api/app/routers/audios.py`). */
export interface AudioCreateBody {
  subject_id: string;
  class_date: string;
  class_timezone: string;
  language_code: string;
  title: string | null;
  teacher: string | null;
  privacy_notice_version: string;
  cloud_processing_accepted: boolean;
  third_party_voice_acknowledged: boolean;
}

/** Construye el body que espera `POST /audios`: vacíos opcionales viajan como `null`. */
export function buildAudioCreate(draft: UploadDraft, privacyNoticeVersion: string): AudioCreateBody {
  const title = draft.title.trim();
  const teacher = draft.teacher.trim();
  return {
    subject_id: draft.subjectId,
    class_date: draft.classDate,
    class_timezone: draft.classTimezone,
    language_code: draft.languageCode,
    title: title === "" ? null : title,
    teacher: teacher === "" ? null : teacher,
    privacy_notice_version: privacyNoticeVersion,
    cloud_processing_accepted: draft.cloudAccepted,
    third_party_voice_acknowledged: draft.voiceAcknowledged,
  };
}

/** Respuesta `201` de `POST /audios` (reserva lista para el `PUT` de D2.2). */
export interface AudioCreated {
  audio_id: string;
  attempt_id: string;
  upload_url: string;
  upload_expires_at: string;
  outcome: string;
}

export interface UploadFormError {
  field: "form" | "consent" | "language";
  message: string;
  /** `consent_required`: el servidor rechazó el aviso; hay que volver a aceptar. */
  resetConsent?: boolean;
  /** `language_not_allowed`: idiomas que el servidor sí acepta ahora. */
  allowed?: string[];
}

/** Traduce el envelope de error de `POST /audios` a un error de la UI. */
export function uploadFormError(error: ApiHttpError): UploadFormError {
  if (error.code === "consent_required") {
    return { field: "consent", message: error.message, resetConsent: true };
  }
  if (error.code === "language_not_allowed") {
    const allowed = Array.isArray(error.details.allowed)
      ? (error.details.allowed as string[])
      : [];
    return { field: "language", message: error.message, allowed };
  }
  if (error.code === "validation_failed") {
    const fields = (error.details.fields ?? {}) as Record<string, string>;
    const entry = Object.entries(fields)[0];
    if (entry) return { field: "form", message: entry[1] };
  }
  if (error.status === 429) {
    return {
      field: "form",
      message: `${error.message} Has alcanzado el límite; inténtalo de nuevo en unos segundos.`,
    };
  }
  if (error.status === 503) {
    return {
      field: "form",
      message: `${error.message} La ingesta está ocupada; reintenta en un momento.`,
    };
  }
  return { field: "form", message: error.message };
}
