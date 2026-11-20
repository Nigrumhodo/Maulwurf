import { apiFetch } from "./api";
import { isRecord, readJson } from "./json";

// D4.2 — M8 audited deliveries listing and duplicate-acknowledged resend.
// Backend owns all state transitions; `delivery_unknown` is terminal and never retries
// automatically because Gmail does not guarantee exactly-once delivery.

export const NOTIFICATION_STATUSES = [
  "pending",
  "sending",
  "sent",
  "failed",
  "delivery_unknown",
  "cancelled",
] as const;

export type NotificationStatus = (typeof NOTIFICATION_STATUSES)[number];

export type NotificationType =
  | "reminder_t48"
  | "reminder_t24"
  | "reminder_t2"
  | "reminder_t3d"
  | "reminder_t1d"
  | "digest_daily"
  | "digest_weekly"
  | "test";

export interface NotificationItem {
  id: string;
  type: NotificationType | string;
  status: NotificationStatus | string;
  task_id: string | null;
  occurrence: string | null;
  due_version: number | null;
  occurrence_seq: number;
  scheduled_at: string;
  attempts: number;
  sent_at: string | null;
  last_error_code: string | null;
  created_at: string;
  updated_at: string;
}

export interface NotificationListResponse {
  items: NotificationItem[];
  next_cursor: string | null;
}

export interface NotificationResendResult extends NotificationItem {
  warning: {
    code: "delivery_uncertain" | "duplicate_risk_acknowledged";
    message: string;
  };
}

function isNotificationItem(value: unknown): value is NotificationItem {
  if (!isRecord(value)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.type === "string" &&
    typeof value.status === "string" &&
    (value.task_id === null || typeof value.task_id === "string") &&
    (value.occurrence === null || typeof value.occurrence === "string") &&
    (value.due_version === null || typeof value.due_version === "number") &&
    typeof value.occurrence_seq === "number" &&
    typeof value.scheduled_at === "string" &&
    typeof value.attempts === "number" &&
    (value.sent_at === null || typeof value.sent_at === "string") &&
    (value.last_error_code === null || typeof value.last_error_code === "string") &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string"
  );
}

export interface NotificationQuery {
  statuses?: NotificationStatus[];
  from?: string;
  to?: string;
  limit?: number;
  cursor?: string | null;
}

/** `GET /notifications` ordered newest first with cursor pagination. */
export async function fetchNotifications(
  query: NotificationQuery = {},
): Promise<NotificationListResponse> {
  const params = new URLSearchParams({ limit: String(query.limit ?? 20) });
  for (const status of query.statuses ?? []) {
    params.append("status", status);
  }
  if (query.from !== undefined && query.from !== "") params.set("from", query.from);
  if (query.to !== undefined && query.to !== "") params.set("to", query.to);
  if (query.cursor !== undefined && query.cursor !== null && query.cursor !== "") {
    params.set("cursor", query.cursor);
  }
  const res = await apiFetch(`/notifications?${params.toString()}`);
  const payload = await readJson(res);
  if (
    !isRecord(payload) ||
    !Array.isArray(payload.items) ||
    (payload.next_cursor !== null && typeof payload.next_cursor !== "string")
  ) {
    throw new Error("El historial de notificaciones no coincide con el contrato.");
  }
  const items: NotificationItem[] = payload.items.map((item) => {
    if (!isNotificationItem(item)) {
      throw new Error("Una entrega de notificación no coincide con el contrato.");
    }
    return item;
  });
  return { items, next_cursor: payload.next_cursor };
}

/** `POST /notifications/{id}/resend`: requires explicit duplicate-risk acknowledgement. */
export async function resendNotification(
  notificationId: string,
): Promise<NotificationResendResult> {
  const res = await apiFetch(`/notifications/${encodeURIComponent(notificationId)}/resend`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ acknowledge_duplicate_risk: true }),
    retries: 0,
  });
  const payload = await readJson(res);
  const rawWarning = isRecord(payload) ? payload.warning : undefined;
  if (!isNotificationItem(payload) || !isRecord(rawWarning)) {
    throw new Error("La respuesta de reenvío no coincide con el contrato.");
  }
  const code = rawWarning.code;
  const message = rawWarning.message;
  if (
    (code !== "delivery_uncertain" && code !== "duplicate_risk_acknowledged") ||
    typeof message !== "string"
  ) {
    throw new Error("La advertencia de reenvío no coincide con el contrato.");
  }
  return { ...payload, warning: { code, message } };
}

export function canResendNotification(status: string): boolean {
  return status === "failed" || status === "delivery_unknown";
}

export function notificationTypeLabel(type: string): string {
  switch (type) {
    case "reminder_t48":
      return "recordatorio T-48 h";
    case "reminder_t24":
      return "recordatorio T-24 h";
    case "reminder_t2":
      return "recordatorio T-2 h";
    case "reminder_t3d":
      return "recordatorio T-3 d";
    case "reminder_t1d":
      return "recordatorio T-1 d";
    case "digest_daily":
      return "resumen diario";
    case "digest_weekly":
      return "resumen semanal";
    case "test":
      return "envío de prueba";
    default:
      return type;
  }
}

export function notificationStatusExplanation(
  status: string,
  lastErrorCode: string | null,
): string {
  const codeNote = lastErrorCode !== null ? ` (${lastErrorCode})` : "";
  switch (status) {
    case "pending":
      return "programada en espera de la ventana de envío.";
    case "sending":
      return "reclamada por el worker de entrega en este instante.";
    case "sent":
      return "aceptada por Gmail para tu correo verificado.";
    case "failed":
      return `no se pudo enviar${codeNote}; el sistema no reintenta automáticamente.`;
    case "delivery_unknown":
      return `estado terminal: Gmail no confirma entrega única y se perdió la respuesta o expiró el lease${codeNote}. No se reintenta automáticamente; reenviar crea una nueva ocurrencia y puede duplicar el correo.`;
    case "cancelled":
      return "cancelada por cambio de fecha, preferencia o estado de la tarea.";
    default:
      return `estado registrado: ${status}${codeNote}.`;
  }
}
