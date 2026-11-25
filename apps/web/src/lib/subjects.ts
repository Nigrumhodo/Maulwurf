import i18n from "@/i18n";
import { apiFetch } from "./api";
import type { ApiHttpError } from "./api";
import { isRecord, readJson } from "./json";

// D1.6 — Tipos y validación del formulario de materias (U-S1-DA-02). El contrato
// de la API es A1.6 / M2a (§3.4): `{id, name, color, teacher, class_count}`.
export interface Subject {
  id: string;
  name: string;
  color: string;
  teacher: string | null;
  class_count: number;
}

function isSubject(value: unknown): value is Subject {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.name === "string" &&
    typeof value.color === "string" &&
    (value.teacher === null || typeof value.teacher === "string") &&
    typeof value.class_count === "number"
  );
}

async function readSubject(res: Response): Promise<Subject> {
  const payload = await readJson(res);
  if (!isSubject(payload)) {
    throw new Error(i18n.t("plan:subjectsLib.subjectMismatch"));
  }
  return payload;
}

/** Lista las materias del tenant; reutilizado por `MateriasPage` (D1.6) y `UploadForm` (D2.1). */
export async function fetchSubjects(): Promise<Subject[]> {
  const res = await apiFetch("/subjects");
  const payload = await readJson(res);
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new Error(i18n.t("plan:subjectsLib.listMismatch"));
  }
  return payload.items.map((item) => {
    if (!isSubject(item)) {
      throw new Error(i18n.t("plan:subjectsLib.itemMismatch"));
    }
    return item;
  });
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

export function subjectPayload(draft: SubjectDraft): {
  name: string;
  color: string;
  teacher: string | null;
} {
  return {
    name: draft.name,
    color: draft.color,
    teacher: draft.teacher.trim() === "" ? null : draft.teacher,
  };
}

/** Validación de campos requeridos (nombre) y de formato (color `#rrggbb`). */
export function validateSubject(draft: SubjectDraft): Partial<Record<SubjectField, string>> {
  const errors: Partial<Record<SubjectField, string>> = {};
  if (draft.name.trim().length === 0) {
    errors.name = i18n.t("plan:subjectsLib.nameRequired");
  }
  if (!COLOR_PATTERN.test(draft.color)) {
    errors.color = i18n.t("plan:subjectsLib.colorFormat");
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
    const entry = error.firstFieldMessage();
    if (entry) {
      const known: SubjectField | "form" =
        entry.field === "name" || entry.field === "color" || entry.field === "teacher"
          ? entry.field
          : "form";
      return { field: known, message: entry.message };
    }
  }
  return { field: "form", message: error.message };
}

export async function createSubject(draft: SubjectDraft): Promise<Subject> {
  const res = await apiFetch("/subjects", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(subjectPayload(draft)),
  });
  return readSubject(res);
}

export async function updateSubject(subjectId: string, draft: SubjectDraft): Promise<Subject> {
  const res = await apiFetch(`/subjects/${subjectId}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(subjectPayload(draft)),
  });
  return readSubject(res);
}
