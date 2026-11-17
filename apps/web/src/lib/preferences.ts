import { ApiHttpError, apiFetch } from "./api";

// D4.1 — M8 notification preferences. The backend owns the values and the closed
// weekday vocabulary (0 = Sunday); this module only shapes the form, validates the
// client-side bounds and sends the dirty fields of a partial PATCH.
//
// `POST /notifications/test` records an audited occurrence. It does not prove a Gmail
// delivery: J4.1 owns the real send and Gmail offers no exactly-once guarantee.

export interface NotificationPreferences {
  gmail_opt_in: boolean;
  digest_daily_enabled: boolean;
  digest_daily_time: string;
  digest_weekly_enabled: boolean;
  digest_weekday: number;
  reminder_offsets_hours: number[];
  reminder_allday_offsets_days: number[];
  updated_at: string | null;
}

export type NotificationPreferencesDraft = Omit<NotificationPreferences, "updated_at">;

export type NotificationPreferencesPatch = Partial<NotificationPreferencesDraft>;

/** 0 = Sunday is frozen by `apps/api/app/services/digests.py`; do not reorder. */
export const WEEKDAYS = [
  "domingo",
  "lunes",
  "martes",
  "miércoles",
  "jueves",
  "viernes",
  "sábado",
] as const;

export type ThresholdKind = "hours" | "days";

export const THRESHOLD_LIMITS: Record<
  ThresholdKind,
  { min: number; max: number; maxCount: number; label: string }
> = {
  hours: { min: 1, max: 24 * 366, maxCount: 10, label: "horas" },
  days: { min: 1, max: 366, maxCount: 10, label: "días" },
};

export interface NotificationTestWarning {
  code: "delivery_uncertain" | "duplicate_risk_acknowledged";
  message: string;
}

export interface NotificationTestResult {
  id: string;
  type: string;
  status: string;
  scheduled_at: string;
  warning: NotificationTestWarning;
}

export function weekdayLabel(value: number): string {
  return WEEKDAYS[value] ?? WEEKDAYS[0];
}

export function normalizeThresholds(values: number[]): number[] {
  return [...new Set(values)].sort((left, right) => right - left);
}

export function validateThresholds(
  values: number[],
  kind: ThresholdKind,
): string | null {
  const limits = THRESHOLD_LIMITS[kind];
  if (values.length > limits.maxCount) {
    return `máximo ${limits.maxCount} umbrales de ${limits.label}.`;
  }
  for (const value of values) {
    if (!Number.isInteger(value) || value < limits.min || value > limits.max) {
      return `cada umbral de ${limits.label} debe ser un entero entre ${limits.min} y ${limits.max}.`;
    }
  }
  return null;
}

export function validatePreferencesDraft(
  draft: NotificationPreferencesDraft,
): string | null {
  if (!/^\d{2}:\d{2}$/.test(draft.digest_daily_time)) {
    return "elige una hora válida para el resumen diario (HH:MM).";
  }
  const [hours, minutes] = draft.digest_daily_time.split(":").map(Number);
  if (hours > 23 || minutes > 59) {
    return "elige una hora válida para el resumen diario (HH:MM).";
  }
  if (
    !Number.isInteger(draft.digest_weekday) ||
    draft.digest_weekday < 0 ||
    draft.digest_weekday > 6
  ) {
    return "elige un día válido para el resumen semanal.";
  }
  return (
    validateThresholds(draft.reminder_offsets_hours, "hours") ??
    validateThresholds(draft.reminder_allday_offsets_days, "days")
  );
}

function sameNumbers(left: number[], right: number[]): boolean {
  return (
    left.length === right.length && left.every((value, index) => value === right[index])
  );
}

/** Send only changed fields so the partial PATCH contract stays visible on the wire. */
export function buildPreferencesPatch(
  current: NotificationPreferencesDraft,
  draft: NotificationPreferencesDraft,
): NotificationPreferencesPatch {
  const patch: NotificationPreferencesPatch = {};
  if (draft.gmail_opt_in !== current.gmail_opt_in) {
    patch.gmail_opt_in = draft.gmail_opt_in;
  }
  if (draft.digest_daily_enabled !== current.digest_daily_enabled) {
    patch.digest_daily_enabled = draft.digest_daily_enabled;
  }
  if (draft.digest_daily_time !== current.digest_daily_time) {
    patch.digest_daily_time = draft.digest_daily_time;
  }
  if (draft.digest_weekly_enabled !== current.digest_weekly_enabled) {
    patch.digest_weekly_enabled = draft.digest_weekly_enabled;
  }
  if (draft.digest_weekday !== current.digest_weekday) {
    patch.digest_weekday = draft.digest_weekday;
  }
  if (!sameNumbers(draft.reminder_offsets_hours, current.reminder_offsets_hours)) {
    patch.reminder_offsets_hours = normalizeThresholds(draft.reminder_offsets_hours);
  }
  if (
    !sameNumbers(draft.reminder_allday_offsets_days, current.reminder_allday_offsets_days)
  ) {
    patch.reminder_allday_offsets_days = normalizeThresholds(
      draft.reminder_allday_offsets_days,
    );
  }
  return patch;
}

export async function fetchNotificationPreferences(): Promise<NotificationPreferences> {
  const res = await apiFetch("/me/notifications");
  return (await res.json()) as NotificationPreferences;
}

export async function updateNotificationPreferences(
  patch: NotificationPreferencesPatch,
): Promise<NotificationPreferences> {
  const res = await apiFetch("/me/notifications", {
    method: "PATCH",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(patch),
    retries: 0,
  });
  return (await res.json()) as NotificationPreferences;
}

export async function sendTestNotification(): Promise<NotificationTestResult> {
  const res = await apiFetch("/notifications/test", {
    method: "POST",
    retries: 0,
  });
  return (await res.json()) as NotificationTestResult;
}

export function preferencesErrorMessage(error: unknown): string {
  if (error instanceof ApiHttpError) {
    if (error.status === 403) return "la sesión no es válida; recarga la página.";
    if (error.status === 401) return "la sesión expiró; vuelve a iniciar sesión.";
  }
  return "no se pudieron guardar las preferencias; revisa la conexión.";
}

const TEST_REASONS: Record<string, string> = {
  gmail_not_opted_in:
    "activa el opt-in de Gmail y guarda los cambios antes de enviar una prueba.",
  recipient_unverified:
    "tu email de Google no está verificado; no se puede enviar la prueba.",
};

export function notificationTestErrorMessage(error: unknown): string {
  if (error instanceof ApiHttpError) {
    if (error.status === 409 && error.code === "notifications_test_unavailable") {
      const reason = error.details.reason;
      if (typeof reason === "string" && reason in TEST_REASONS) {
        return TEST_REASONS[reason];
      }
      return "el envío de prueba no está disponible en el estado actual.";
    }
    if (error.status === 403) return "la sesión no es válida; recarga la página.";
    if (error.status === 401) return "la sesión expiró; vuelve a iniciar sesión.";
  }
  return "no se pudo registrar la prueba; revisa la conexión.";
}
