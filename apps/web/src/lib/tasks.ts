import i18n, { intlLocale } from "@/i18n";
import { apiFetch } from "./api";
import { isRecord } from "./json";
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
  ordinal: number;
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
  calendar_id: string | null;
  calendar_event_id: string | null;
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

/** Wire alias used by the shared TaskList. */
export type TaskOut = Task;

export interface TaskQuery {
  status?: TaskStatusFilter;
  subjectId?: string;
  audioId?: string;
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
  occurrences?: number;
}

export interface TaskQualityState {
  signals: TaskQualitySignal[];
  unavailable: boolean;
  /** Reader metadata used by the task-to-segment link, never to invent a time. */
  timestamp_precision: string | null;
  duration_seconds: number | null;
}

function qualityLabel(code: TaskQualitySignal["code"]): string {
  return i18n.t(`plan:tasksLib.quality.${code}`);
}

export async function fetchTask(taskId: string): Promise<Task> {
  const res = await apiFetch(`/tasks/${encodeURIComponent(taskId)}`);
  return (await res.json()) as Task;
}

export async function fetchTaskPage(query: TaskQuery = {}): Promise<TaskPage> {
  const params = new URLSearchParams({ limit: String(query.limit ?? 20) });
  if (query.status !== undefined && query.status !== "all") params.set("status", query.status);
  if (query.subjectId !== undefined && query.subjectId !== "") {
    params.set("subject_id", query.subjectId);
  }
  if (query.audioId !== undefined && query.audioId !== "") {
    params.set("audio_id", query.audioId);
  }
  if (query.dueFrom !== undefined && query.dueFrom !== "") params.set("due_from", query.dueFrom);
  if (query.dueTo !== undefined && query.dueTo !== "") params.set("due_to", query.dueTo);
  if (query.cursor !== undefined && query.cursor !== null) params.set("cursor", query.cursor);
  const res = await apiFetch(`/tasks?${params.toString()}`);
  return (await res.json()) as TaskPage;
}

/** `GET /tasks` list consumed by the shared `TaskList`/`useServerList` (dashboard). */
export async function fetchTasks(): Promise<Task[]> {
  const page = await fetchTaskPage({ limit: 50 });
  return page.items;
}

/** Due instant from the task itself: `due_at`, else `due_date`, else `date_status`. */
export function taskSchedule(task: Task): string {
  if (task.due_at !== null) return task.due_at;
  if (task.due_date !== null) return task.due_date;
  return task.date_status;
}

export function qualitySignals(detail: LibraryAudioDetail | null): TaskQualitySignal[] {
  const warnings = detail?.transcript?.quality_warnings ?? [];
  const grouped = new Map<
    TaskQualitySignal["code"],
    { count: number | null; total: number | null; occurrences: number }
  >();
  for (const raw of warnings) {
    const code = raw.code;
    if (code !== "repetition" && code !== "empty_segments" && code !== "low_confidence") {
      continue;
    }
    const count = typeof raw.count === "number" ? raw.count : null;
    const total = typeof raw.total === "number" ? raw.total : null;
    const existing = grouped.get(code);
    if (existing === undefined) {
      grouped.set(code, { count, total, occurrences: 1 });
    } else {
      existing.occurrences += 1;
      if (count !== null) {
        existing.count = (existing.count ?? 0) + count;
      }
      if (total !== null) {
        existing.total = (existing.total ?? 0) + total;
      }
    }
  }
  const signals: TaskQualitySignal[] = [];
  for (const [code, entry] of grouped.entries()) {
    signals.push(
      entry.occurrences > 1
        ? {
            code,
            label: qualityLabel(code),
            count: entry.count,
            total: entry.total,
            occurrences: entry.occurrences,
          }
        : {
            code,
            label: qualityLabel(code),
            count: entry.count,
            total: entry.total,
          },
    );
  }
  return signals;
}

export function qualitySummary(signal: TaskQualitySignal): string {
  const { label, count, occurrences } = signal;
  if ((occurrences ?? 1) > 1) {
    if (signal.code === "repetition") {
      return count === null
        ? i18n.t("plan:tasksLib.quality.repeatedNoCount", { label, occurrences })
        : i18n.t("plan:tasksLib.quality.repeatedWords", { label, occurrences, count });
    }
    return count === null
      ? i18n.t("plan:tasksLib.quality.warningsNoCount", { label, occurrences })
      : i18n.t("plan:tasksLib.quality.repeatedCount", { label, occurrences, count });
  }
  const counts =
    count === null
      ? ""
      : signal.total === null
        ? ` (${count})`
        : ` (${count}/${signal.total})`;
  return `${label}${counts}`;
}

export function confidenceLabel(score: number | null): string {
  return score === null
    ? i18n.t("plan:tasksLib.noConfidence")
    : i18n.t("plan:tasksLib.confidence", { score: score.toFixed(2) });
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
    return new Intl.DateTimeFormat(intlLocale(), {
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
  return i18n.t(`plan:tasksLib.status.${status}`);
}

export function taskStatusFilters(): Array<{ value: TaskStatusFilter; label: string }> {
  return [
    { value: "all", label: i18n.t("plan:tasksLib.status.all") },
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
  tipo?: string;
  titulo?: string;
  detalle?: string | null;
  due_date?: string | null;
  due_at?: string | null;
  timezone?: string;
  all_day?: boolean;
  date_status?: TaskDateStatus;
  date_reason?: string | null;
  event_block?: TaskEventBlockPatch | null;
}

export interface TaskCreateInput {
  source: "manual";
  tipo: string;
  titulo: string;
  detalle?: string | null;
  subject_id?: string | null;
  due_date?: string | null;
  due_at?: string | null;
  timezone: string;
  all_day: boolean;
  date_status: TaskDateStatus;
  event_block?: TaskEventBlockPatch | null;
}

export async function createTask(input: TaskCreateInput): Promise<Task> {
  const res = await apiFetch("/tasks", {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(input),
    retries: 0,
  });
  return (await res.json()) as Task;
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
  const payload = (await res.json()) as
    | { task?: Task; revision_id?: string | null }
    | Task;
  const task =
    "task" in payload && payload.task !== undefined ? payload.task : (payload as Task);
  const revisionId =
    "revision_id" in payload
      ? (payload.revision_id ?? null)
      : (task.pending_revision?.id ?? null);
  return { task, revision_id: revisionId };
}

export interface TaskConfirmation {
  task: Task;
  notice?: string | null;
  revision_id?: string | null;
}

export interface TaskLifecycleResult {
  task: Task;
  notice: string | null;
  cancelled_reminders: number;
  calendar_event_kept: boolean;
}

export async function confirmTask(
  taskId: string,
  expectedVersion: number,
  revisionId: string | null = null,
): Promise<TaskConfirmation> {
  const res = await apiFetch(`/tasks/${encodeURIComponent(taskId)}/confirm`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      expected_version: expectedVersion,
      revision_id: revisionId,
    }),
    retries: 0,
  });
  const payload = (await res.json()) as {
    task: Task;
    notice?: string | null;
    revision_id?: string | null;
  };
  return {
    task: payload.task,
    notice: payload.notice ?? null,
    revision_id: payload.revision_id ?? null,
  };
}

export async function dismissTask(
  taskId: string,
  expectedVersion: number,
): Promise<TaskLifecycleResult> {
  return closeTask(taskId, "dismiss", expectedVersion);
}

export async function completeTask(
  taskId: string,
  expectedVersion: number,
): Promise<TaskLifecycleResult> {
  return closeTask(taskId, "complete", expectedVersion);
}

async function closeTask(
  taskId: string,
  action: "dismiss" | "complete",
  expectedVersion: number,
): Promise<TaskLifecycleResult> {
  const res = await apiFetch(`/tasks/${encodeURIComponent(taskId)}/${action}`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ expected_version: expectedVersion }),
    retries: 0,
  });
  const payload = (await res.json()) as {
    task: Task;
    notice?: string | null;
    cancelled_reminders?: number;
    calendar_event_kept?: boolean;
  };
  const kept =
    payload.calendar_event_kept === true || payload.notice === "calendar_event_kept";
  return {
    task: payload.task,
    notice: kept ? "calendar_event_kept" : (payload.notice ?? null),
    cancelled_reminders: payload.cancelled_reminders ?? 0,
    calendar_event_kept: kept,
  };
}

function readString(record: Record<string, unknown>, key: string): string | null | undefined {
  if (!(key in record)) return undefined;
  const value = record[key];
  if (typeof value === "string") return value;
  return value === null ? null : undefined;
}

function readNumber(record: Record<string, unknown>, key: string): number | null | undefined {
  if (!(key in record)) return undefined;
  const value = record[key];
  if (typeof value === "number" && Number.isFinite(value)) return value;
  return value === null ? null : undefined;
}

function readBoolean(record: Record<string, unknown>, key: string): boolean | undefined {
  if (!(key in record)) return undefined;
  return typeof record[key] === "boolean" ? record[key] : undefined;
}

function readDateStatus(
  record: Record<string, unknown>,
  key: string,
): TaskDateStatus | undefined {
  if (!(key in record)) return undefined;
  const value = record[key];
  return value === "resolved" || value === "ambiguous" || value === "missing"
    ? value
    : undefined;
}

/**
 * D3.3: the effective task a confirmation would write. A pending revision is an overlay;
 * omitted fields keep the active value and unknown keys are ignored.
 */
export function effectiveTask(task: Task): Task {
  const proposed = task.pending_revision?.proposed_fields;
  if (proposed === undefined) return task;
  const next: Task = { ...task, event_block: { ...task.event_block } };

  const tipo = readString(proposed, "tipo");
  if (tipo !== null && tipo !== undefined) next.tipo = tipo;
  const titulo = readString(proposed, "titulo");
  if (titulo !== null && titulo !== undefined) next.titulo = titulo;
  const detalle = readString(proposed, "detalle");
  if (detalle !== undefined) next.detalle = detalle;
  const dueDate = readString(proposed, "due_date");
  if (dueDate !== undefined) next.due_date = dueDate;
  const dueAt = readString(proposed, "due_at");
  if (dueAt !== undefined) next.due_at = dueAt;
  const timezone = readString(proposed, "timezone");
  if (timezone !== null && timezone !== undefined) next.timezone = timezone;
  const allDay = readBoolean(proposed, "all_day");
  if (allDay !== undefined) next.all_day = allDay;
  const dateStatus = readDateStatus(proposed, "date_status");
  if (dateStatus !== undefined) next.date_status = dateStatus;
  const dateText = readString(proposed, "date_text");
  if (dateText !== undefined) next.date_text = dateText;
  const dateReason = readString(proposed, "date_reason");
  if (dateReason !== undefined) next.date_reason = dateReason;
  const confidence = readNumber(proposed, "confidence_score");
  if (confidence !== undefined) next.confidence_score = confidence;

  if (isRecord(proposed.event_block)) {
    const block = proposed.event_block;
    const startAt = readString(block, "start_at");
    const endAt = readString(block, "end_at");
    const startDate = readString(block, "start_date");
    const endDate = readString(block, "end_date_exclusive");
    next.event_block = {
      ...task.event_block,
      start_at: startAt === undefined ? task.event_block.start_at : startAt,
      end_at: endAt === undefined ? task.event_block.end_at : endAt,
      start_date: startDate === undefined ? task.event_block.start_date : startDate,
      end_date_exclusive:
        endDate === undefined ? task.event_block.end_date_exclusive : endDate,
    };
  }
  return next;
}

function formatDateOnly(value: string): string | null {
  const [year, month, day] = value.split("-");
  if (year === undefined || month === undefined || day === undefined) return null;
  return `${day}/${month}/${year}`;
}

function formatInstant(value: string, timeZone: string): string | null {
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) return null;
  try {
    return new Intl.DateTimeFormat(intlLocale(), {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone,
    }).format(instant);
  } catch {
    return null;
  }
}

/** D3.3: the agenda block as the user will see it, never a guessed duration. */
export function formatEventBlock(task: Task): string | null {
  const block = task.event_block;
  if (block.start_at !== null && block.end_at !== null) {
    const start = formatInstant(block.start_at, task.timezone);
    const end = formatInstant(block.end_at, task.timezone);
    if (start !== null && end !== null) return `${start} – ${end}`;
  }
  if (block.start_date !== null && block.end_date_exclusive !== null) {
    const start = formatDateOnly(block.start_date);
    const end = formatDateOnly(block.end_date_exclusive);
    if (start !== null && end !== null) return `${start} – ${end} ${i18n.t("plan:tasksLib.exclusiveEnd")}`;
  }
  return null;
}

export function eventModeLabel(task: Task): string {
  if (task.event_block.start_at !== null || task.event_block.end_at !== null) {
    return i18n.t("plan:tasksLib.eventMode.timed");
  }
  if (
    task.event_block.start_date !== null ||
    task.event_block.end_date_exclusive !== null
  ) {
    return i18n.t("plan:tasksLib.eventMode.allDay");
  }
  return i18n.t("plan:tasksLib.eventMode.none");
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

export type DueMode = "missing" | "all_day" | "timed";
export type EventMode = "none" | "all_day" | "timed";

export interface ScheduleFormValues {
  dueMode: DueMode;
  dueDate: string;
  dueTime: string;
  timezone: string;
  eventMode: EventMode;
  eventStartDate: string;
  eventStartTime: string;
  eventEndDate: string;
  durationMinutes: number;
}

export interface ScheduleFields {
  date_status: TaskDateStatus;
  due_date: string | null;
  due_at: string | null;
  timezone: string;
  all_day: boolean;
  event_block: TaskEventBlockPatch | null;
}

/** Validate the due/agenda form state. Returns the wire fields or a translated error message. */
export function buildScheduleFields(values: ScheduleFormValues): ScheduleFields | string {
  const { dueMode, dueDate, dueTime, timezone, eventMode } = values;
  let dueFields: Pick<ScheduleFields, "due_date" | "due_at" | "all_day">;
  if (dueMode === "all_day") {
    if (dueDate === "") {
      return i18n.t("plan:tasksLib.scheduleErrors.dueDateOrMissing");
    }
    dueFields = { due_date: dueDate, due_at: null, all_day: true };
  } else if (dueMode === "timed") {
    if (dueDate === "") return i18n.t("plan:tasksLib.scheduleErrors.dueDate");
    const dueAt = wallTimeToInstant(dueDate, dueTime, timezone);
    if (dueAt === null) return i18n.t("plan:tasksLib.scheduleErrors.dueTimeInvalid");
    dueFields = { due_date: null, due_at: dueAt, all_day: false };
  } else {
    dueFields = { due_date: null, due_at: null, all_day: false };
  }

  let eventBlock: TaskEventBlockPatch | null = null;
  if (eventMode === "all_day") {
    if (values.eventStartDate === "" || values.eventEndDate === "") {
      return i18n.t("plan:tasksLib.scheduleErrors.blockNeedsDates");
    }
    if (values.eventEndDate <= values.eventStartDate) {
      return i18n.t("plan:tasksLib.scheduleErrors.blockEndAfterStart");
    }
    eventBlock = {
      start_at: null,
      end_at: null,
      start_date: values.eventStartDate,
      end_date_exclusive: values.eventEndDate,
    };
  } else if (eventMode === "timed") {
    if (values.eventStartDate === "") return i18n.t("plan:tasksLib.scheduleErrors.blockStartDate");
    const startAt = wallTimeToInstant(values.eventStartDate, values.eventStartTime, timezone);
    if (startAt === null) return i18n.t("plan:tasksLib.scheduleErrors.blockStartTimeInvalid");
    const endAt = addMinutes(startAt, values.durationMinutes);
    if (endAt === null) return i18n.t("plan:tasksLib.scheduleErrors.durationInvalid");
    eventBlock = { start_at: startAt, end_at: endAt, start_date: null, end_date_exclusive: null };
  }

  return {
    date_status: dueMode === "missing" ? "missing" : "resolved",
    ...dueFields,
    timezone,
    event_block: eventBlock,
  };
}
