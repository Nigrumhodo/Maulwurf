import { apiFetch } from "./api";
import type { LibraryAudioDetail } from "./library";
import { safeCitationHref } from "./markdown";

// D3.1 — M4 inbox contract. The server owns statuses, dates, evidence and confidence;
// this module only formats them for review and never derives a due date or a quote.

export const TASK_STATUSES = [
  "pending",
  "needs_review",
  "confirmed",
  "dismissed",
  "completed",
] as const;

export type TaskStatus = (typeof TASK_STATUSES)[number];
export type TaskStatusFilter = TaskStatus | "all";
export type TaskDateStatus = "resolved" | "ambiguous" | "missing";

export interface TaskSubject {
  id: string;
  name: string;
}

export interface TaskSource {
  audio_id: string;
  transcript_version: number;
  segment_id: string;
  char_start: number;
  char_end: number;
}

export interface TaskEvidence {
  segment_id: string;
  char_start: number;
  char_end: number;
  quote: string;
  t_start: number | null;
  t_end: number | null;
}

export interface TaskEventBlock {
  start_at: string | null;
  end_at: string | null;
  start_date: string | null;
  end_date_exclusive: string | null;
  duration_default_editable: boolean;
}

export interface PendingRevision {
  id: string;
  base_task_version: number;
  status: string;
}

export interface Task {
  id: string;
  audio_id: string | null;
  subject: TaskSubject | null;
  source: string;
  tipo: string;
  titulo: string;
  detalle: string | null;
  due_date: string | null;
  due_at: string | null;
  timezone: string;
  all_day: boolean;
  date_status: TaskDateStatus;
  date_text: string | null;
  date_reason: string | null;
  confidence_score: number | null;
  status: TaskStatus;
  calendar_sync_status: string;
  version: number;
  due_version: number;
  event_block: TaskEventBlock;
  sources: TaskSource[];
  evidence: TaskEvidence[];
  pending_revision: PendingRevision | null;
}

export interface TaskPage {
  items: Task[];
  next_cursor: string | null;
}

export interface TaskQuery {
  status?: TaskStatusFilter;
  subjectId?: string;
  dueFrom?: string;
  dueTo?: string;
  limit?: number;
  cursor?: string | null;
}

export interface TaskQualitySignal {
  code: "repetition" | "empty_segments" | "low_confidence";
  label: string;
  count: number | null;
  total: number | null;
}

export interface TaskQualityState {
  signals: TaskQualitySignal[];
  unavailable: boolean;
}

const QUALITY_LABELS: Record<TaskQualitySignal["code"], string> = {
  low_confidence: "baja confianza",
  empty_segments: "segmentos vacíos",
  repetition: "repeticiones",
};

export async function fetchTasks(query: TaskQuery = {}): Promise<TaskPage> {
  const params = new URLSearchParams({ limit: String(query.limit ?? 20) });
  if (query.status !== undefined && query.status !== "all") params.set("status", query.status);
  if (query.subjectId !== undefined && query.subjectId !== "") {
    params.set("subject_id", query.subjectId);
  }
  if (query.dueFrom !== undefined && query.dueFrom !== "") params.set("due_from", query.dueFrom);
  if (query.dueTo !== undefined && query.dueTo !== "") params.set("due_to", query.dueTo);
  if (query.cursor !== undefined && query.cursor !== null) params.set("cursor", query.cursor);
  const res = await apiFetch(`/tasks?${params.toString()}`);
  return (await res.json()) as TaskPage;
}

export function qualitySignals(detail: LibraryAudioDetail | null): TaskQualitySignal[] {
  const warnings = detail?.transcript?.quality_warnings ?? [];
  const signals: TaskQualitySignal[] = [];
  for (const raw of warnings) {
    const code = raw.code;
    if (code !== "repetition" && code !== "empty_segments" && code !== "low_confidence") {
      continue;
    }
    const count = typeof raw.count === "number" ? raw.count : null;
    const total = typeof raw.total === "number" ? raw.total : null;
    signals.push({ code, label: QUALITY_LABELS[code], count, total });
  }
  return signals;
}

export function qualitySummary(signal: TaskQualitySignal): string {
  const counts =
    signal.count === null
      ? ""
      : signal.total === null
        ? ` (${signal.count})`
        : ` (${signal.count}/${signal.total})`;
  return `${signal.label}${counts}`;
}

export function confidenceLabel(score: number | null): string {
  return score === null ? "sin confianza" : `confianza ${score.toFixed(2)}`;
}

export function formatTaskDue(task: Task): string | null {
  if (task.date_status !== "resolved") return null;
  if (task.due_date !== null) {
    const [year, month, day] = task.due_date.split("-");
    if (year === undefined || month === undefined || day === undefined) return null;
    return `${day}/${month}/${year}`;
  }
  if (task.due_at === null) return null;
  const instant = new Date(task.due_at);
  if (Number.isNaN(instant.getTime())) return null;
  try {
    return new Intl.DateTimeFormat("es", {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: task.timezone,
    }).format(instant);
  } catch {
    return null;
  }
}

export function taskEvidenceHref(task: Task, evidence: TaskEvidence): string | null {
  const source = task.sources.find(
    (item) =>
      item.segment_id === evidence.segment_id &&
      item.char_start === evidence.char_start &&
      item.char_end === evidence.char_end,
  );
  const audioId = source?.audio_id ?? task.audio_id;
  if (audioId === null) return null;
  return safeCitationHref(
    `/biblioteca/${encodeURIComponent(audioId)}?segment=${encodeURIComponent(evidence.segment_id)}`,
  );
}

export function taskStatusLabel(status: TaskStatus): string {
  switch (status) {
    case "needs_review":
      return "por confirmar";
    case "pending":
      return "pendientes";
    case "confirmed":
      return "confirmadas";
    case "dismissed":
      return "descartadas";
    case "completed":
      return "completadas";
  }
}

export function taskStatusFilters(): Array<{ value: TaskStatusFilter; label: string }> {
  return [
    { value: "all", label: "todas" },
    ...TASK_STATUSES.map((status) => ({ value: status, label: taskStatusLabel(status) })),
  ];
}
