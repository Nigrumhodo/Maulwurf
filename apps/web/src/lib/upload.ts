import { LANGUAGES } from "./languages";
import { ApiHttpError, apiFetch } from "./api";
import type { ApiRequestInit } from "./api";
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
  variant_confirmation_token?: string;
}

/**
 * Construye el body que espera `POST /audios`: vacíos opcionales viajan como `null`.
 * El token de variante solo se envía en el segundo `POST` tras un `409 duplicate_variant`.
 */
export function buildAudioCreate(
  draft: UploadDraft,
  privacyNoticeVersion: string,
  variantConfirmationToken?: string,
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
    ...(variantConfirmationToken != null
      ? { variant_confirmation_token: variantConfirmationToken }
      : {}),
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

// D2.2 — Envío binario en streaming (U-S2-DA-02). El `PUT` va a la URL relativa de la
// reserva con `fetch` y un `ReadableStream` que cuenta los bytes REALES leídos del archivo;
// cancelar aborta el `fetch` y llama a la API de cancelación. Ningún helper toca el DOM ni
// crea Server Actions: el binario nunca se materializa ni se persiste en el navegador.

const MIME_FORMATS: Record<string, string> = {
  "audio/wav": "wav",
  "audio/wave": "wav",
  "audio/x-wav": "wav",
  "audio/mpeg": "mp3",
  "audio/mp3": "mp3",
  "audio/flac": "flac",
  "audio/x-flac": "flac",
  "audio/ogg": "ogg",
  "audio/mp4": "m4a",
  "audio/x-m4a": "m4a",
  "audio/webm": "webm",
};

/** Formatea bytes para la UI sin inventar límites (los publica el servidor). */
export function formatBytes(value: number | null): string {
  if (value === null) return "no publicado";
  if (value >= 1024 * 1024) return `${(value / (1024 * 1024)).toFixed(1)} MiB`;
  if (value >= 1024) return `${Math.round(value / 1024)} KiB`;
  return `${value} B`;
}

/** Extensiones aceptadas por el input `file` según los formatos publicados. */
export function acceptedFileExtensions(capabilities: IngestionCapabilities | null): string[] {
  return (capabilities?.accepted_input_formats ?? [])
    .map((format) => format.trim().toLowerCase())
    .filter((format) => format !== "")
    .map((format) => `.${format}`);
}

function formatOf(file: File): string | null {
  const byMime = MIME_FORMATS[file.type.toLowerCase()];
  if (byMime !== undefined) return byMime;
  const name = file.name.toLowerCase();
  const dot = name.lastIndexOf(".");
  return dot === -1 ? null : name.slice(dot + 1);
}

export interface FileCheck {
  ok: boolean;
  error?: string;
  format?: string;
}

/** Resultado de elegir un archivo: el `File` válido o el error que debe mostrarse. */
export interface PickedFile {
  file: File | null;
  error: string | null;
}

/** Valida un archivo elegido por el usuario; ambos flujos de subida comparten esta regla. */
export function pickUploadFile(
  file: File | null,
  capabilities: IngestionCapabilities | null,
): PickedFile {
  if (file === null) return { file: null, error: null };
  const check = validateFile(file, capabilities);
  return check.ok
    ? { file, error: null }
    : { file: null, error: check.error ?? "archivo no válido." };
}

/** Comprueba archivo vacío, tamaño máximo publicado y formato aceptado. */
export function validateFile(file: File, capabilities: IngestionCapabilities | null): FileCheck {
  if (file.size === 0) return { ok: false, error: "el archivo está vacío." };
  const max = capabilities?.max_upload_bytes ?? null;
  if (max !== null && file.size > max) {
    return {
      ok: false,
      error: `el archivo supera el máximo publicado (${formatBytes(max)}).`,
    };
  }
  const accepted = (capabilities?.accepted_input_formats ?? []).map((format) =>
    format.trim().toLowerCase(),
  );
  const format = formatOf(file);
  if (accepted.length > 0 && (format === null || !accepted.includes(format))) {
    return { ok: false, error: `formato no aceptado; usa ${accepted.join(", ")}.` };
  }
  return { ok: true, format: format ?? undefined };
}

export interface UploadProgress {
  sentBytes: number;
  totalBytes: number;
}

/**
 * Envuelve lecturas por bloques de `file.slice()` contando los bytes que se entregan al
 * `fetch`. El progreso es real (bytes leídos del archivo), nunca un porcentaje inventado
 * del procesamiento. No usa `file.stream()`: jsdom no lo implementa y el slicing mantiene
 * la memoria acotada a un bloque.
 */
const UPLOAD_CHUNK_BYTES = 64 * 1024;

export function createProgressStream(
  file: File,
  onProgress: (progress: UploadProgress) => void,
): ReadableStream<Uint8Array> {
  const totalBytes = file.size;
  let offset = 0;
  let cancelled = false;
  return new ReadableStream<Uint8Array>({
    async pull(controller) {
      if (cancelled) return;
      if (offset >= totalBytes) {
        controller.close();
        return;
      }
      const end = Math.min(offset + UPLOAD_CHUNK_BYTES, totalBytes);
      const chunk = new Uint8Array(await file.slice(offset, end).arrayBuffer());
      if (cancelled) return;
      offset = end;
      onProgress({ sentBytes: offset, totalBytes });
      controller.enqueue(chunk);
    },
    cancel() {
      cancelled = true;
    },
  });
}

export interface UploadAccepted {
  outcome: "accepted";
  attemptId: string | null;
  status: string | null;
  receivedBytes: number | null;
}

export interface UploadDuplicate {
  outcome: "duplicate_exact";
  audioId: string | null;
}

export type UploadResult = UploadAccepted | UploadDuplicate;

export interface UploadContentOptions {
  uploadUrl: string;
  file: File;
  signal?: AbortSignal;
  onProgress?: (progress: UploadProgress) => void;
}

function stringOrNull(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

/**
 * `PUT` en streaming a la URL relativa de la reserva. `retries: 0` es deliberado: un
 * stream ya consumido no puede reintentarse; el usuario reintenta con una reserva nueva o
 * la misma si el servidor sigue esperando bytes.
 */
export async function uploadContent({
  uploadUrl,
  file,
  signal,
  onProgress,
}: UploadContentOptions): Promise<UploadResult> {
  if (!uploadUrl.startsWith("/")) {
    throw new Error(`La URL de subida debe ser relativa: ${uploadUrl}`);
  }
  // `duplex: "half"` es obligatorio en Chromium para un cuerpo en streaming; aún no está
  // en la definición de `RequestInit` del lib.dom de TypeScript.
  const init = {
    method: "PUT",
    headers: { "content-type": file.type || "application/octet-stream" },
    body: createProgressStream(file, onProgress ?? (() => {})) as unknown as BodyInit,
    signal,
    retries: 0,
    duplex: "half",
  } as ApiRequestInit & { duplex: "half" };

  const res = await apiFetch(uploadUrl, init);
  const payload = (await res.json()) as Record<string, unknown>;
  if (res.status === 200) {
    return { outcome: "duplicate_exact", audioId: stringOrNull(payload.audio_id) };
  }
  return {
    outcome: "accepted",
    attemptId: stringOrNull(payload.attempt_id),
    status: stringOrNull(payload.status),
    receivedBytes: typeof payload.received_bytes === "number" ? payload.received_bytes : null,
  };
}

/** Cancela el intento; un `409`/`404` significa que ya no queda nada que cancelar. */
export async function cancelAttempt(audioId: string, attemptId: string): Promise<string | null> {
  try {
    const res = await apiFetch(
      `/audios/${encodeURIComponent(audioId)}/attempts/${encodeURIComponent(attemptId)}/cancel`,
      { method: "POST", retries: 0 },
    );
    const payload = (await res.json()) as { status?: unknown };
    return stringOrNull(payload.status);
  } catch (error) {
    if (error instanceof ApiHttpError && (error.status === 409 || error.status === 404)) {
      return null;
    }
    throw error;
  }
}

export interface VariantConflict {
  existingAudioId: string;
  token: string;
  expiresAt: string | null;
}

/** Lee el `409 duplicate_variant`: único error cuyo `details` lleva un token en plano. */
export function variantConflict(error: ApiHttpError): VariantConflict | null {
  if (error.code !== "duplicate_variant") return null;
  const token = error.details.variant_confirmation_token;
  const existingAudioId = error.details.existing_audio_id;
  if (typeof token !== "string" || token === "") return null;
  if (typeof existingAudioId !== "string" || existingAudioId === "") return null;
  const expiresAt = error.details.expires_at;
  return {
    existingAudioId,
    token,
    expiresAt: typeof expiresAt === "string" ? expiresAt : null,
  };
}

export interface UploadErrorView {
  message: string;
  /** La reserva ya no sirve: hay que volver al formulario y reservar otra vez. */
  resetReservation?: boolean;
  /** Reintentar el mismo `PUT` dentro de la ventana tiene sentido. */
  retry?: boolean;
}

/** Traduce los errores del `PUT` a una vista honesta (sin inventar estados). */
export function mapUploadError(error: ApiHttpError): UploadErrorView {
  if (error.code === "payload_too_large") {
    const max = typeof error.details.max_bytes === "number" ? error.details.max_bytes : null;
    const limit = max !== null ? ` (máximo ${formatBytes(max)})` : "";
    return { message: `${error.message}${limit}` };
  }
  if (error.code === "upload_expired") {
    return { message: `${error.message} Vuelve a reservar la clase.`, resetReservation: true };
  }
  if (error.code === "attempt_not_active") {
    return { message: `${error.message} Vuelve a reservar la clase.`, resetReservation: true };
  }
  if (error.code === "variant_hash_mismatch") {
    return { message: error.message, resetReservation: true };
  }
  if (error.status === 404) {
    return { message: "la clase ya no existe; vuelve a reservarla.", resetReservation: true };
  }
  if (error.status === 503 || error.status === 429) {
    return { message: `${error.message} Reintenta en un momento.`, retry: true };
  }
  if (error.status === 413) {
    return { message: error.message };
  }
  if (error.status === 415) {
    return { message: error.message };
  }
  return { message: error.message, retry: true };
}

/**
 * Libera una URL de objeto si el flujo llegara a crear una (hoy no hay vista previa). El
 * `File` se suelta en el componente; el binario nunca se persiste en el navegador (D2.8).
 */
export function releaseUploadResources(objectUrl: string | null): void {
  if (
    objectUrl !== null &&
    typeof URL !== "undefined" &&
    typeof URL.revokeObjectURL === "function"
  ) {
    URL.revokeObjectURL(objectUrl);
  }
}
