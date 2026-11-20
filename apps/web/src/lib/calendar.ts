import { ApiHttpError, apiFetch } from "./api";
import { isRecord } from "./json";
import type { Task } from "./tasks";

// D3.6 - the calendar mirror is Google truth for Google events; Maulwurf tasks are drawn
// from their confirmed block. The two are merged here, never duplicated: a task with a
// linked event wins over its mirror row, and the block is the fallback identity.

export interface CalendarMirrorEvent {
  id: string;
  calendar_id: string;
  event_id: string;
  title: string;
  source: string;
  start_at: string | null;
  end_at: string | null;
  start_date: string | null;
  end_date_exclusive: string | null;
  timezone: string | null;
  remote_status: string;
  last_synced_at: string;
}

export interface CalendarEventsResponse {
  events: CalendarMirrorEvent[];
  from: string;
  to: string;
  last_synced_at: string | null;
  coverage_ok: boolean;
  coverage_notice: string | null;
  stale: boolean;
}

export interface CalendarItem {
  id: string;
  title: string;
  start: string;
  end: string;
  allDay: boolean;
  backgroundColor: string;
  borderColor: string;
  textColor: string;
  extendedProps: {
    kind: "task" | "google";
    taskId?: string;
    calendarId?: string;
    eventId?: string;
    status?: string;
    subjectName?: string;
  };
}

export interface CalendarBuildResult {
  events: CalendarItem[];
  skippedLinkedEvents: number;
  skippedByBlock: number;
  skippedCancelled: number;
  tasksWithoutBlock: number;
}

const ACTIVE_TASK_STATUSES = new Set(["pending", "needs_review", "confirmed"]);
const GOOGLE_COLOR = "#64748b";
const FALLBACK_SUBJECT_COLOR = "#8b5cf6";

export interface CalendarSettingsData {
  write_calendar_id: string | null;
  read_calendar_ids: string[];
}

export interface GoogleCalendarListItem {
  id: string;
  summary: string;
  primary: boolean;
  access_role: string | null;
  is_maulwurf_write: boolean;
}

export interface GoogleCalendarListResponse {
  items: GoogleCalendarListItem[];
  selected_ids: string[];
}

export interface CalendarSyncResponse {
  from: string;
  to: string;
  calendars: string[];
  inserted: number;
  removed: number;
  last_synced_at: string;
}

export async function fetchCalendarEvents(
  from: string,
  to: string,
): Promise<CalendarEventsResponse> {
  const params = new URLSearchParams({ from, to });
  const res = await apiFetch(`/calendar/events?${params.toString()}`, {
    cache: "no-store",
  });
  return (await res.json()) as CalendarEventsResponse;
}

export async function fetchCalendarSettings(): Promise<CalendarSettingsData> {
  const res = await apiFetch("/calendar/settings", { cache: "no-store" });
  const body: unknown = await res.json();
  if (!isRecord(body)) {
    return { write_calendar_id: null, read_calendar_ids: [] };
  }
  const writeId = typeof body.write_calendar_id === "string" ? body.write_calendar_id : null;
  const readIds = Array.isArray(body.read_calendar_ids)
    ? body.read_calendar_ids.filter((item): item is string => typeof item === "string")
    : [];
  return { write_calendar_id: writeId, read_calendar_ids: readIds };
}

export async function updateCalendarSettings(
  readCalendarIds: string[],
): Promise<CalendarSettingsData> {
  const res = await apiFetch("/calendar/settings", {
    method: "PUT",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ read_calendar_ids: readCalendarIds }),
  });
  return (await res.json()) as CalendarSettingsData;
}

export async function fetchGoogleCalendarList(): Promise<GoogleCalendarListResponse> {
  const res = await apiFetch("/calendar/calendars", { cache: "no-store" });
  const body: unknown = await res.json();
  if (!isRecord(body) || !Array.isArray(body.items)) {
    return { items: [], selected_ids: [] };
  }
  return body as unknown as GoogleCalendarListResponse;
}

export async function syncCalendarMirror(): Promise<CalendarSyncResponse> {
  const res = await apiFetch("/calendar/sync", {
    method: "POST",
    retries: 0,
  });
  return (await res.json()) as CalendarSyncResponse;
}

export function calendarSyncErrorCopy(err: unknown): string {
  if (err instanceof ApiHttpError) {
    const reason = isRecord(err.details) && typeof err.details.reason === "string"
      ? err.details.reason
      : null;
    if (reason === "no_read_calendars") {
      return "no hay calendarios de lectura seleccionados; elige al menos uno (p. ej. principal) en settings.";
    }
    if (reason === "google_not_connected" || reason === "missing_calendar_scopes") {
      return "conecta o reconecta google calendar en settings antes de sincronizar.";
    }
    if (reason === "google_invalid_grant") {
      return "google revocó el acceso; reconecta google calendar en settings.";
    }
    if (reason === "api_not_enabled") {
      return "la API de Google Calendar está deshabilitada en Google Cloud Console; habilítala en tu proyecto y vuelve a intentar.";
    }
    if (reason === "calendar_sync_cooldown") {
      return "ya sincronizaste hace unos segundos; espera un momento antes de volver a sincronizar.";
    }
    return err.message;
  }
  return "no se pudo sincronizar Google Calendar.";
}

export function calendarCoverageLabel(notice: string | null): string {
  switch (notice) {
    case "never_synced":
      return "nunca sincronizado";
    case "no_read_calendars":
      return "no hay calendarios de lectura";
    case "calendar_set_changed":
      return "cambió el conjunto de calendarios";
    case "outside_coverage":
      return "fuera de la ventana sincronizada";
    case "stale":
      return "datos desactualizados";
    default:
      return "cobertura completa";
  }
}

export function formatLastSyncedAt(value: string | null): string {
  if (value === null) return "nunca sincronizado";
  const instant = new Date(value);
  if (Number.isNaN(instant.getTime())) return "fecha de sincronización inválida";
  try {
    return new Intl.DateTimeFormat("es", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(instant);
  } catch {
    return value;
  }
}

function taskBlock(task: Task): { start: string; end: string; allDay: boolean } | null {
  if (task.event_block.start_at !== null && task.event_block.end_at !== null) {
    return {
      start: task.event_block.start_at,
      end: task.event_block.end_at,
      allDay: false,
    };
  }
  if (
    task.event_block.start_date !== null &&
    task.event_block.end_date_exclusive !== null
  ) {
    return {
      start: task.event_block.start_date,
      end: task.event_block.end_date_exclusive,
      allDay: true,
    };
  }
  return null;
}

function mirrorBlock(
  event: CalendarMirrorEvent,
): { start: string; end: string; allDay: boolean } | null {
  if (event.start_at !== null && event.end_at !== null) {
    return { start: event.start_at, end: event.end_at, allDay: false };
  }
  if (event.start_date !== null && event.end_date_exclusive !== null) {
    return {
      start: event.start_date,
      end: event.end_date_exclusive,
      allDay: true,
    };
  }
  return null;
}

function blockKey(block: { start: string; end: string; allDay: boolean }): string {
  return `${block.allDay ? "day" : "time"}:${block.start}:${block.end}`;
}

function eventKey(event: CalendarMirrorEvent): string {
  return `${event.calendar_id}:${event.event_id}`;
}

function readableTextColor(background: string): string {
  const match = /^#([0-9a-f]{6})$/i.exec(background);
  if (match === null) return "#f8fafc";
  const value = match[1]!;
  const red = Number.parseInt(value.slice(0, 2), 16);
  const green = Number.parseInt(value.slice(2, 4), 16);
  const blue = Number.parseInt(value.slice(4, 6), 16);
  const luminance = (0.299 * red + 0.587 * green + 0.114 * blue) / 255;
  return luminance > 0.62 ? "#0f172a" : "#f8fafc";
}

export function activeTasksWithoutBlock(tasks: Task[]): Task[] {
  return tasks.filter(
    (task) => ACTIVE_TASK_STATUSES.has(task.status) && taskBlock(task) === null,
  );
}

export function buildCalendarEvents(
  events: CalendarMirrorEvent[],
  tasks: Task[],
  subjectColors: ReadonlyMap<string, string>,
): CalendarBuildResult {
  const items: CalendarItem[] = [];
  const linkedEventKeys = new Set<string>();
  const linkedBlockKeys = new Set<string>();
  let tasksWithoutBlock = 0;

  for (const task of tasks) {
    if (!ACTIVE_TASK_STATUSES.has(task.status)) continue;
    const block = taskBlock(task);
    if (block === null) {
      tasksWithoutBlock += 1;
      continue;
    }
    if (task.calendar_id !== null && task.calendar_event_id !== null) {
      linkedEventKeys.add(`${task.calendar_id}:${task.calendar_event_id}`);
    }
    linkedBlockKeys.add(blockKey(block));
    const color =
      (task.subject !== null ? subjectColors.get(task.subject.id) : undefined) ??
      FALLBACK_SUBJECT_COLOR;
    items.push({
      id: `task:${task.id}`,
      title: task.titulo,
      start: block.start,
      end: block.end,
      allDay: block.allDay,
      backgroundColor: color,
      borderColor: color,
      textColor: readableTextColor(color),
      extendedProps: {
        kind: "task",
        taskId: task.id,
        status: task.status,
        subjectName: task.subject?.name,
        calendarId: task.calendar_id ?? undefined,
        eventId: task.calendar_event_id ?? undefined,
      },
    });
  }

  let skippedLinkedEvents = 0;
  let skippedByBlock = 0;
  let skippedCancelled = 0;
  for (const event of events) {
    if (event.remote_status === "cancelled" || event.remote_status === "missing") {
      skippedCancelled += 1;
      continue;
    }
    if (linkedEventKeys.has(eventKey(event))) {
      skippedLinkedEvents += 1;
      continue;
    }
    const block = mirrorBlock(event);
    if (block === null) continue;
    if (linkedBlockKeys.has(blockKey(block))) {
      skippedByBlock += 1;
      continue;
    }
    items.push({
      id: `google:${event.calendar_id}:${event.event_id}`,
      title: event.title,
      start: block.start,
      end: block.end,
      allDay: block.allDay,
      backgroundColor: GOOGLE_COLOR,
      borderColor: GOOGLE_COLOR,
      textColor: "#f8fafc",
      extendedProps: {
        kind: "google",
        calendarId: event.calendar_id,
        eventId: event.event_id,
      },
    });
  }

  return {
    events: items,
    skippedLinkedEvents,
    skippedByBlock,
    skippedCancelled,
    tasksWithoutBlock,
  };
}
