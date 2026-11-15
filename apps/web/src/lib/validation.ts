// D1.5 — Required-field check for forms (U-S1-DA-02).
// The API error envelope is parsed once in `lib/api.ts`.

/** Campo requerido no vacío: acepta strings con contenido no-blanco. */
export function isRequired(value: unknown): boolean {
  return typeof value === "string" && value.trim().length > 0;
}
