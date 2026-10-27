import type { ApiHttpError } from "./api";

// D1.6 — Tipos y validación del formulario de materias (U-S1-DA-02). El contrato
// de la API es A1.6 / M2a (§3.4): `{id, name, color, teacher, class_count}`.
export interface Subject {
  id: string;
  name: string;
  color: string;
  teacher: string | null;
  class_count: number;
}

/** Valores que edita el formulario; `teacher` vacío se envía como `null`. */
export interface SubjectDraft {
  name: string;
  color: string;
  teacher: string;
}

/** Color por defecto (igual al `server_default` de `subjects.color`). */
export const DEFAULT_SUBJECT_COLOR = "#6366f1";

const COLOR_PATTERN = /^#[0-9a-fA-F]{6}$/;

export type SubjectField = "name" | "color" | "teacher";

/** Validación de campos requeridos (nombre) y de formato (color). */
export function validateSubject(draft: SubjectDraft): Partial<Record<SubjectField, string>> {
  const errors: Partial<Record<SubjectField, string>> = {};
  if (draft.name.trim().length === 0) {
    errors.name = "la materia necesita un nombre";
  }
  if (draft.color !== "" && !COLOR_PATTERN.test(draft.color)) {
    errors.color = "usa un color #rrggbb (ej. #6366f1)";
  }
  return errors;
}

export interface SubjectFormError {
  field: SubjectField | "form";
  message: string;
}

/** Traduce un error de API (envelope) a un error de campo del formulario. */
export function subjectFormError(error: ApiHttpError): SubjectFormError {
  if (error.code === "subject_name_taken") {
    return { field: "name", message: error.message };
  }
  if (error.code === "validation_failed") {
    const fields = (error.details.fields ?? {}) as Record<string, string>;
    const entry = Object.entries(fields)[0];
    if (entry) {
      const [field, message] = entry;
      const known: SubjectField | "form" =
        field === "name" || field === "color" || field === "teacher" ? field : "form";
      return { field: known, message };
    }
  }
  return { field: "form", message: error.message };
}
