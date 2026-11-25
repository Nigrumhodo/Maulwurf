import i18n from "@/i18n";
import { apiFetch } from "./api";

// D3.3: the review dialog must show the Calendar target before confirming. The backend
// owns the readiness decision (connected + scopes + write calendar); the web never calls
// Google or infers a writable target from a calendar id alone.

export interface GoogleStatus {
  status: "connected" | "disconnected";
  scopes: string[];
  calendar_id: string | null;
  calendar_write_ready: boolean;
  token_expires_at: string | null;
  last_refresh_at: string | null;
}

export interface IntegrationsStatus {
  google: GoogleStatus;
}

export async function fetchIntegrationsStatus(): Promise<IntegrationsStatus> {
  const res = await apiFetch("/integrations/status");
  return (await res.json()) as IntegrationsStatus;
}

// D3.7 — the browser navigates to the GET variants: a `fetch` POST cannot follow the
// cross-origin 302 to Google nor render the consent screen.
export const GOOGLE_CONNECT_PATH = "/integrations/google/connect";
export const GOOGLE_RECONNECT_PATH = "/integrations/google/reconnect";
export const GOOGLE_GMAIL_CONNECT_PATH = "/integrations/google/gmail/connect";
export const GMAIL_SEND_SCOPE = "https://www.googleapis.com/auth/gmail.send";

export function hasGmailSendScope(google: GoogleStatus | null): boolean {
  return google !== null && google.scopes.includes(GMAIL_SEND_SCOPE);
}

export function googleConnectHref(google: GoogleStatus | null): string {
  if (google === null) return GOOGLE_CONNECT_PATH;
  return google.status === "connected" || google.calendar_id !== null
    ? GOOGLE_RECONNECT_PATH
    : GOOGLE_CONNECT_PATH;
}

export function googleConnectLabel(google: GoogleStatus | null): string {
  return googleConnectHref(google) === GOOGLE_RECONNECT_PATH
    ? i18n.t("plan:integrations.reconnectLabel")
    : i18n.t("plan:integrations.connectLabel");
}

/**
 * Per-task sync explanation (C-S3-DA-08). The raw `calendar_sync_status` stays in the
 * badge; this only adds context and never suggests a write the backend does not expose.
 * `failed` is info-only: an external edit is resolved in Google, not by an automatic retry.
 */
export function syncStatusDescription(status: string): string | null {
  switch (status) {
    case "not_requested":
      return i18n.t("plan:integrations.sync.notRequested");
    case "pending":
      return i18n.t("plan:integrations.sync.pending");
    case "synced":
      return i18n.t("plan:integrations.sync.synced");
    case "failed":
      return i18n.t("plan:integrations.sync.failed");
    case "blocked":
      return i18n.t("plan:integrations.sync.blocked");
    default:
      return null;
  }
}

function compactCalendarId(calendarId: string): string {
  if (calendarId.length <= 36) return calendarId;
  const atIndex = calendarId.indexOf("@");
  if (atIndex > 12) {
    return `${calendarId.slice(0, 10)}…${calendarId.slice(atIndex)}`;
  }
  return `${calendarId.slice(0, 28)}…`;
}

export function calendarTargetLabel(google: GoogleStatus): string {
  if (google.calendar_write_ready && google.calendar_id !== null) {
    return `Maulwurf · ${compactCalendarId(google.calendar_id)}`;
  }
  if (google.calendar_id !== null) {
    return `Maulwurf · ${compactCalendarId(google.calendar_id)} ${i18n.t("plan:integrations.target.notReady")}`;
  }
  return i18n.t("plan:integrations.target.none");
}

// Closed reason vocabulary produced by the Calendar start/callback redirects. Unknown
// values fall back to a generic message instead of echoing the query string.
const CALENDAR_ERROR_REASONS = [
  "not_configured",
  "csrf_invalid",
  "not_connected",
  "rate_limited",
  "capacity_unavailable",
  "invalid_state",
  "access_denied",
  "missing_params",
  "state_mismatch",
  "oauth_failed",
  "wrong_account",
  "scope_required",
  "invalid_grant",
  "api_not_enabled",
  "calendar_unavailable",
  "calendar_error",
] as const;

type CalendarErrorReason = (typeof CALENDAR_ERROR_REASONS)[number];

function isCalendarErrorReason(reason: string): reason is CalendarErrorReason {
  return (CALENDAR_ERROR_REASONS as readonly string[]).includes(reason);
}

export function calendarErrorCopy(reason: string | null): string {
  if (reason === null || !isCalendarErrorReason(reason)) {
    return i18n.t("plan:integrations.calendarErrors.generic");
  }
  return i18n.t(`plan:integrations.calendarErrors.${reason}`);
}

const GMAIL_ERROR_REASONS = [
  "not_configured",
  "csrf_invalid",
  "rate_limited",
  "capacity_unavailable",
  "invalid_state",
  "access_denied",
  "missing_params",
  "state_mismatch",
  "oauth_failed",
  "wrong_account",
  "scope_required",
  "invalid_grant",
] as const;

type GmailErrorReason = (typeof GMAIL_ERROR_REASONS)[number];

function isGmailErrorReason(reason: string): reason is GmailErrorReason {
  return (GMAIL_ERROR_REASONS as readonly string[]).includes(reason);
}

export function gmailErrorCopy(reason: string | null): string {
  if (reason === null || !isGmailErrorReason(reason)) {
    return i18n.t("plan:integrations.gmailErrors.generic");
  }
  return i18n.t(`plan:integrations.gmailErrors.${reason}`);
}
