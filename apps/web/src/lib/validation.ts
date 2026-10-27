// D1.5 — Validación de formularios base y parseo del envelope de error de la API
// (`{"error": {code, message, details}}`). U-S1-DA-02.
export interface ApiErrorShape {
  code?: string;
  message?: string;
  details?: Record<string, unknown> | unknown[];
}

/** Campo requerido no vacío: acepta strings con contenido no-blanco. */
export function isRequired(value: unknown): boolean {
  return typeof value === "string" && value.trim().length > 0;
}

/** Extrae el envelope de error tipado; `null` si el cuerpo no lo tiene. */
export function parseErrorEnvelope(body: unknown): ApiErrorShape | null {
  if (typeof body !== "object" || body === null) return null;
  const error = (body as Record<string, unknown>).error;
  if (typeof error !== "object" || error === null) return null;
  const e = error as Record<string, unknown>;
  const details = e.details;
  return {
    code: typeof e.code === "string" ? e.code : undefined,
    message: typeof e.message === "string" ? e.message : undefined,
    details:
      typeof details === "object" && details !== null
        ? (details as Record<string, unknown> | unknown[])
        : undefined,
  };
}

/** Mensaje de error de un campo dentro de `details` (string o array de strings). */
export function fieldMessage(details: unknown, field: string): string | undefined {
  if (typeof details !== "object" || details === null || Array.isArray(details)) {
    return undefined;
  }
  const value = (details as Record<string, unknown>)[field];
  if (typeof value === "string") return value;
  if (Array.isArray(value) && typeof value[0] === "string") return value[0];
  return undefined;
}
