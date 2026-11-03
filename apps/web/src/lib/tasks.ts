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
  proposed_fields: Record<string, unknown>;
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

export const DEFAULT_EVENT_DURATION_MINUTES = 30;

export interface TaskEventBlockPatch {
  start_at: string | null;
  end_at: string | null;
  start_date: string | null;
  end_date_exclusive: string | null;
}

export interface TaskPatch {
  version: number;
  titulo?: string;
  detalle?: string | null;
  due_date?: string | null;
  due_at?: string | null;
  timezone?: string;
  all_day?: boolean;
  date_status?: TaskDateStatus;
  date_text?: string | null;
  date_reason?: string | null;
  event_block?: TaskEventBlockPatch | null;
}

export interface TaskUpdate {
  task: Task;
  revision_id: string | null;
}

export async function patchTask(taskId: string, patch: TaskPatch): Promise<TaskUpdate> {
  const res = await apiFetch(`/tasks/${encodeURIComponent(taskId)}`, {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(patch),
    retries: 0,
  });
  return (await res.json()) as TaskUpdate;
}

function wallParts(instant: Date, timeZone: string): Record<string, string> | null {
  try {
    const formatter = new Intl.DateTimeFormat("en-US", {
      timeZone,
      hourCycle: "h23",
      year: "numeric",
      month: "2-digit",
      day: "2-digit",
      hour: "2-digit",
      minute: "2-digit",
      second: "2-digit",
    });
    return Object.fromEntries(
      formatter
        .formatToParts(instant)
        .filter((part) => part.type !== "literal")
        .map((part) => [part.type, part.value]),
    );
  } catch {
    return null;
  }
}

function offsetMs(instant: Date, timeZone: string): number | null {
  const parts = wallParts(instant, timeZone);
  if (parts === null) return null;
  const asUtc = Date.UTC(
    Number(parts.year),
    Number(parts.month) - 1,
    Number(parts.day),
    Number(parts.hour),
    Number(parts.minute),
    Number(parts.second),
  );
  return asUtc - instant.getTime();
}

/** Convierte una hora de reloj en la zona dada a un instante ISO; null si no existe. */
export function wallTimeToInstant(
  dateValue: string,
  timeValue: string,
  timeZone: string,
): string | null {
  const dateParts = dateValue.split("-").map(Number);
  const timeParts = timeValue.split(":").map(Number);
  if (dateParts.length !== 3 || timeParts.length < 2) return null;
  const [year, month, day] = dateParts;
  const [hour, minute] = timeParts;
  if (
    year === undefined ||
    month === undefined ||
    day === undefined ||
    hour === undefined ||
    minute === undefined ||
    !Number.isFinite(year) ||
    !Number.isFinite(month) ||
    !Number.isFinite(day) ||
    !Number.isFinite(hour) ||
    !Number.isFinite(minute)
  ) {
    return null;
  }
  const wallUtc = Date.UTC(year, month - 1, day, hour, minute, 0);
  let instant = wallUtc;
  for (let attempt = 0; attempt < 3; attempt += 1) {
    const offset = offsetMs(new Date(instant), timeZone);
    if (offset === null) return null;
    const next = wallUtc - offset;
    if (next === instant) break;
    instant = next;
  }
  const result = new Date(instant);
  const parts = wallParts(result, timeZone);
  if (
    parts === null ||
    Number(parts.year) !== year ||
    Number(parts.month) !== month ||
    Number(parts.day) !== day ||
    Number(parts.hour) !== hour ||
    Number(parts.minute) !== minute
  ) {
    return null;
  }
  return result.toISOString();
}

export function instantToWall(
  instant: string | null,
  timeZone: string,
): { date: string; time: string } | null {
  if (instant === null) return null;
  const parsed = new Date(instant);
  if (Number.isNaN(parsed.getTime())) return null;
  const parts = wallParts(parsed, timeZone);
  if (parts === null) return null;
  return {
    date: `${parts.year}-${parts.month}-${parts.day}`,
    time: `${parts.hour}:${parts.minute}`,
  };
}

export function dateOnlyAddDays(value: string, days: number): string | null {
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (match === null) return null;
  const date = new Date(`${value}T00:00:00Z`);
  if (Number.isNaN(date.getTime())) return null;
  date.setUTCDate(date.getUTCDate() + days);
  return date.toISOString().slice(0, 10);
}

export function addMinutes(instant: string, minutes: number): string | null {
  const date = new Date(instant);
  if (Number.isNaN(date.getTime())) return null;
  return new Date(date.getTime() + minutes * 60_000).toISOString();
}
