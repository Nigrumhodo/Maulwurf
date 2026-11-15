import { apiFetch } from "./api";
import { isRecord, readJson } from "./json";

/** Wire types for `apps/api/app/schemas/tasks.py`. Dates and datetimes arrive as ISO strings. */

export interface TaskSubjectOut {
  id: string;
  name: string;
}

export interface TaskSourceOut {
  audio_id: string;
  transcript_version: number;
  segment_id: string;
  char_start: number;
  char_end: number;
}

export interface TaskEvidenceOut {
  segment_id: string;
  char_start: number;
  char_end: number;
  quote: string;
  t_start: number | null;
  t_end: number | null;
}

export interface TaskEventBlockOut {
  start_at: string | null;
  end_at: string | null;
  start_date: string | null;
  end_date_exclusive: string | null;
  duration_default_editable: boolean;
}

/**
 * Server `PendingRevisionOut.proposed_fields` is an open `dict[str, Any]`.
 * The keys are the proposal columns, not a client-side escape hatch.
 */
export type ProposedFields = Record<string, unknown>;

export interface PendingRevisionOut {
  id: string;
  base_task_version: number;
  proposed_fields: ProposedFields;
  status: string;
}

export interface TaskOut {
  id: string;
  audio_id: string | null;
  subject: TaskSubjectOut | null;
  source: string;
  tipo: string;
  titulo: string;
  detalle: string | null;
  due_date: string | null;
  due_at: string | null;
  timezone: string;
  all_day: boolean;
  date_status: string;
  date_text: string | null;
  date_reason: string | null;
  confidence_score: number | null;
  status: string;
  calendar_sync_status: string;
  version: number;
  due_version: number;
  event_block: TaskEventBlockOut;
  sources: TaskSourceOut[];
  evidence: TaskEvidenceOut[];
  pending_revision: PendingRevisionOut | null;
}

export interface TaskListOut {
  items: TaskOut[];
  next_cursor: string | null;
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isTaskSubject(value: unknown): value is TaskSubjectOut {
  return isRecord(value) && typeof value.id === "string" && typeof value.name === "string";
}

function isTaskOut(value: unknown): value is TaskOut {
  if (!isRecord(value) || !isRecord(value.event_block)) return false;
  return (
    typeof value.id === "string" &&
    isNullableString(value.audio_id) &&
    (value.subject === null || isTaskSubject(value.subject)) &&
    typeof value.source === "string" &&
    typeof value.tipo === "string" &&
    typeof value.titulo === "string" &&
    isNullableString(value.detalle) &&
    isNullableString(value.due_date) &&
    isNullableString(value.due_at) &&
    typeof value.timezone === "string" &&
    typeof value.all_day === "boolean" &&
    typeof value.date_status === "string" &&
    isNullableString(value.date_text) &&
    isNullableString(value.date_reason) &&
    (value.confidence_score === null || typeof value.confidence_score === "number") &&
    typeof value.status === "string" &&
    typeof value.calendar_sync_status === "string" &&
    typeof value.version === "number" &&
    typeof value.due_version === "number" &&
    Array.isArray(value.sources) &&
    Array.isArray(value.evidence) &&
    (value.pending_revision === null || isRecord(value.pending_revision))
  );
}

/** Due instant from the task itself: `due_at`, else `due_date`, else `date_status`. */
export function taskSchedule(task: TaskOut): string {
  if (task.due_at !== null) return task.due_at;
  if (task.due_date !== null) return task.due_date;
  return task.date_status;
}

/** `GET /tasks`. The dashboard and the tareas page both read this list. */
export async function fetchTasks(): Promise<TaskOut[]> {
  const res = await apiFetch("/tasks");
  const payload = await readJson(res);
  if (!isRecord(payload) || !Array.isArray(payload.items)) {
    throw new Error("El listado de tareas no coincide con el contrato.");
  }
  return payload.items.map((item) => {
    if (!isTaskOut(item)) {
      throw new Error("Una tarea del listado no coincide con el contrato.");
    }
    return item;
  });
}
