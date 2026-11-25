import i18n from "@/i18n";
import { ApiHttpError, apiFetch } from "./api";
import { isRecord, readJson } from "./json";

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

const WEEKDAY_KEYS = [
  "sunday",
  "monday",
  "tuesday",
  "wednesday",
  "thursday",
  "friday",
  "saturday",
] as const;

/** 0 = Sunday is frozen by `apps/api/app/services/digests.py`; do not reorder. */
export function weekdayLabels(): string[] {
  return WEEKDAY_KEYS.map((key) => i18n.t(`settings:weekdays.${key}`));
}

export type ThresholdKind = "hours" | "days";

export const THRESHOLD_LIMITS: Record<
  ThresholdKind,
  { min: number; max: number; maxCount: number }
> = {
  hours: { min: 1, max: 24 * 366, maxCount: 10 },
  days: { min: 1, max: 366, maxCount: 10 },
};

export function thresholdUnitLabel(kind: ThresholdKind): string {
  return i18n.t(`settings:preferencesLib.units.${kind}`);
}

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
  const labels = weekdayLabels();
  return labels[value] ?? labels[0]!;
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
    return i18n.t("settings:preferencesLib.maxThresholds", {
      max: limits.maxCount,
      unit: thresholdUnitLabel(kind),
    });
  }
  for (const value of values) {
    if (!Number.isInteger(value) || value < limits.min || value > limits.max) {
      return i18n.t("settings:preferencesLib.thresholdRange", {
        unit: thresholdUnitLabel(kind),
        min: limits.min,
        max: limits.max,
      });
    }
  }
  return null;
}

export function validatePreferencesDraft(
  draft: NotificationPreferencesDraft,
): string | null {
  if (!/^\d{2}:\d{2}$/.test(draft.digest_daily_time)) {
    return i18n.t("settings:preferencesLib.invalidDailyTime");
  }
  const [hours, minutes] = draft.digest_daily_time.split(":").map(Number);
  if (hours > 23 || minutes > 59) {
    return i18n.t("settings:preferencesLib.invalidDailyTime");
  }
  if (
    !Number.isInteger(draft.digest_weekday) ||
    draft.digest_weekday < 0 ||
    draft.digest_weekday > 6
  ) {
    return i18n.t("settings:preferencesLib.invalidWeekday");
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

/** Single dirty check shared by the save and test buttons so they cannot disagree. */
export function isPreferencesDirty(
  current: NotificationPreferencesDraft,
  draft: NotificationPreferencesDraft,
): boolean {
  return Object.keys(buildPreferencesPatch(current, draft)).length > 0;
}

function isNumberArray(value: unknown): value is number[] {
  return Array.isArray(value) && value.every((item) => typeof item === "number");
}

function isNotificationPreferences(value: unknown): value is NotificationPreferences {
  if (!isRecord(value)) return false;
  return (
    typeof value.gmail_opt_in === "boolean" &&
    typeof value.digest_daily_enabled === "boolean" &&
    typeof value.digest_daily_time === "string" &&
    typeof value.digest_weekly_enabled === "boolean" &&
    typeof value.digest_weekday === "number" &&
    isNumberArray(value.reminder_offsets_hours) &&
    isNumberArray(value.reminder_allday_offsets_days) &&
    (value.updated_at === null || typeof value.updated_at === "string")
  );
}

function isNotificationTestResult(value: unknown): value is NotificationTestResult {
  if (!isRecord(value) || !isRecord(value.warning)) return false;
  return (
    typeof value.id === "string" &&
    typeof value.type === "string" &&
    typeof value.status === "string" &&
    typeof value.scheduled_at === "string" &&
    typeof value.warning.code === "string" &&
    typeof value.warning.message === "string"
  );
}

export async function fetchNotificationPreferences(): Promise<NotificationPreferences> {
  const res = await apiFetch("/me/notifications");
  const payload = await readJson(res);
  if (!isNotificationPreferences(payload)) {
    throw new Error(i18n.t("settings:preferencesLib.responseMismatch"));
  }
  return payload;
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
  const payload = await readJson(res);
  if (!isNotificationPreferences(payload)) {
    throw new Error(i18n.t("settings:preferencesLib.responseMismatch"));
  }
  return payload;
}

export async function sendTestNotification(): Promise<NotificationTestResult> {
  const res = await apiFetch("/notifications/test", {
    method: "POST",
    retries: 0,
  });
  const payload = await readJson(res);
  if (!isNotificationTestResult(payload)) {
    throw new Error(i18n.t("settings:preferencesLib.testResponseMismatch"));
  }
  return payload;
}

export function preferencesErrorMessage(error: unknown): string {
  if (error instanceof ApiHttpError) {
    if (error.status === 403) return i18n.t("settings:preferencesLib.sessionInvalid");
    if (error.status === 401) return i18n.t("settings:preferencesLib.sessionExpired");
  }
  return i18n.t("settings:preferencesLib.saveFailed");
}

export function notificationTestErrorMessage(error: unknown): string {
  if (error instanceof ApiHttpError) {
    if (error.status === 409 && error.code === "notifications_test_unavailable") {
      const reason = error.details.reason;
      if (reason === "gmail_not_opted_in") {
        return i18n.t("settings:preferencesLib.testReasons.gmail_not_opted_in");
      }
      if (reason === "recipient_unverified") {
        return i18n.t("settings:preferencesLib.testReasons.recipient_unverified");
      }
      return i18n.t("settings:preferencesLib.testUnavailable");
    }
    if (error.status === 403) return i18n.t("settings:preferencesLib.sessionInvalid");
    if (error.status === 401) return i18n.t("settings:preferencesLib.sessionExpired");
  }
  return i18n.t("settings:preferencesLib.testFailed");
}
