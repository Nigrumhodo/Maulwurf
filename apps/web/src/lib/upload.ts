import { apiFetch } from "./api";
import type { ApiHttpError, DuplicateVariantDetails } from "./api";
import type { IngestionCapabilities } from "./capabilities";
import { isRecord, readJson } from "./json";
import { LANGUAGES } from "./languages";

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
    capabilities.privacy_notice_version.trim() !== ""
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

/** Forma de `AudioCreate` (`apps/api/app/routers/audios.py`), incluido el token de variante. */
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
  variant_confirmation_token: string | null;
}

/** Construye el body que espera `POST /audios`: vacíos opcionales viajan como `null`. */
export function buildAudioCreate(
  draft: UploadDraft,
  privacyNoticeVersion: string,
  variantConfirmationToken: string | null = null,
): AudioCreateBody {
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
    variant_confirmation_token: variantConfirmationToken,
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

function isAudioCreated(value: unknown): value is AudioCreated {
  if (!isRecord(value)) return false;
  return (
    typeof value.audio_id === "string" &&
    typeof value.attempt_id === "string" &&
    typeof value.upload_url === "string" &&
    typeof value.upload_expires_at === "string" &&
    typeof value.outcome === "string"
  );
}

/** Reserva la clase. El binario sigue siendo un `PUT` aparte. */
export async function createAudio(body: AudioCreateBody): Promise<AudioCreated> {
  const res = await apiFetch("/audios", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(body),
  });
  const payload = await readJson(res);
  if (!isAudioCreated(payload)) {
    throw new Error("La reserva de audio no coincide con el contrato.");
  }
  return payload;
}

export interface UploadFormError {
  field: "form" | "consent" | "language" | "variant";
  message: string;
  /** `consent_required`: el servidor rechazó el aviso; hay que volver a aceptar. */
  resetConsent?: boolean;
  /** `language_not_allowed`: idiomas que el servidor sí acepta ahora. */
  allowed?: string[];
  /** `duplicate_variant`: token que el siguiente `POST /audios` debe devolver. */
  variant?: DuplicateVariantDetails;
}

/** Traduce el envelope de error de `POST /audios` a un error de la UI. */
export function uploadFormError(error: ApiHttpError): UploadFormError {
  if (error.code === "consent_required") {
    return { field: "consent", message: error.message, resetConsent: true };
  }
  if (error.code === "duplicate_variant") {
    const variant = error.duplicateVariant();
    if (variant) return { field: "variant", message: error.message, variant };
  }
  if (error.code === "language_not_allowed") {
    return {
      field: "language",
      message: error.message,
      allowed: error.stringList("allowed") ?? [],
    };
  }
  if (error.code === "validation_failed") {
    const entry = error.firstFieldMessage();
    if (entry) return { field: "form", message: entry.message };
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
