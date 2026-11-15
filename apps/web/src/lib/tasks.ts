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
