import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "@/components/auth/AuthProvider";
import { GoogleReconnect } from "@/components/calendar/GoogleReconnect";
import { AccountDeletionPanel } from "@/components/settings/AccountDeletionPanel";
import { DeliveryPanel } from "@/components/settings/DeliveryPanel";
import { NotificationPreferences } from "@/components/settings/NotificationPreferences";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { TimezoneSelect } from "@/components/ui/TimezoneSelect";
import { updateTimezone } from "@/lib/auth";
import { ApiHttpError } from "@/lib/api";
import {
  GOOGLE_GMAIL_CONNECT_PATH,
  calendarErrorCopy,
  fetchIntegrationsStatus,
  gmailErrorCopy,
  hasGmailSendScope,
} from "@/lib/integrations";
import type { GoogleStatus } from "@/lib/integrations";

// D1.2 / D3.7 / D4.1 / D4.2 — Settings: editable timezone (`PATCH /me`), notification
// preferences, Google Calendar + optional Gmail `gmail.send` connection, M8 delivery history
// with duplicate-acknowledged resend, and observable account deletion.

interface RedirectNotice {
  kind: "connected" | "error";
  reason: string | null;
}

export function SettingsPage() {
  const { user, retrySession } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [calendarNotice, setCalendarNotice] = useState<RedirectNotice | null>(null);
  const [gmailNotice, setGmailNotice] = useState<RedirectNotice | null>(null);
  const [google, setGoogle] = useState<GoogleStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);

  const [timezoneDraft, setTimezoneDraft] = useState(user?.timezone ?? "");
  const [tzSaving, setTzSaving] = useState(false);
  const [tzNotice, setTzNotice] = useState<string | null>(null);
  const [tzError, setTzError] = useState<string | null>(null);

  useEffect(() => {
    if (user?.timezone !== undefined && timezoneDraft === "") {
      setTimezoneDraft(user.timezone);
    }
  }, [user?.timezone, timezoneDraft]);

  useEffect(() => {
    const calendarResult = searchParams.get("calendar");
    const gmailResult = searchParams.get("gmail");
    if (calendarResult === null && gmailResult === null) return;
    if (calendarResult !== null) {
      setCalendarNotice({
        kind: calendarResult === "connected" ? "connected" : "error",
        reason: searchParams.get("reason"),
      });
    }
    if (gmailResult !== null) {
      setGmailNotice({
        kind: gmailResult === "connected" ? "connected" : "error",
        reason: searchParams.get("reason"),
      });
    }
    setSearchParams({}, { replace: true });
  }, [searchParams, setSearchParams]);

  useEffect(() => {
    let cancelled = false;
    setStatusLoading(true);
    fetchIntegrationsStatus()
      .then((status) => {
        if (cancelled) return;
        setGoogle(status.google ?? null);
        setStatusError(null);
      })
      .catch(() => {
        if (cancelled) return;
        setStatusError("no se pudo comprobar el estado de Google Calendar.");
      })
      .finally(() => {
        if (!cancelled) setStatusLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [calendarNotice, gmailNotice]);

  async function handleSaveTimezone(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (user === null || timezoneDraft.trim() === "" || timezoneDraft === user.timezone) return;
    setTzSaving(true);
    setTzNotice(null);
    setTzError(null);
    try {
      const updated = await updateTimezone(timezoneDraft);
      setTimezoneDraft(updated.timezone);
      setTzNotice("zona horaria actualizada para futuros resúmenes.");
      if (typeof retrySession === "function") {
        await retrySession();
      }
    } catch (err) {
      setTzError(
        err instanceof ApiHttpError
          ? err.message
          : "no se pudo guardar la zona horaria.",
      );
    } finally {
      setTzSaving(false);
    }
  }

  const gmailReady = hasGmailSendScope(google);

  return (
    <div className="flex flex-col gap-6">
      <h1 className="capitalize text-2xl text-mint">settings</h1>

      <Card title="perfil">
        {user === null ? (
          <p className="lowercase text-lavender">sin perfil cargado.</p>
        ) : (
          <div className="lowercase flex flex-col gap-4 text-lavender">
            <dl className="flex flex-col gap-2">
              <div>
                <dt className="text-mint">&gt; email</dt>
                <dd>{user.email}</dd>
              </div>
              <div>
                <dt className="text-mint">&gt; zona horaria</dt>
                <dd>{user.timezone}</dd>
              </div>
            </dl>

            <form
              onSubmit={(event) => void handleSaveTimezone(event)}
              className="flex flex-wrap items-end gap-3 border-t-2 border-lavender/20 pt-3"
            >
              <TimezoneSelect
                label="cambiar zona horaria"
                value={timezoneDraft}
                disabled={tzSaving}
                onChange={(event) => setTimezoneDraft(event.target.value)}
              />
              <Button
                type="submit"
                size="sm"
                variant="secondary"
                disabled={tzSaving || timezoneDraft === "" || timezoneDraft === user.timezone}
              >
                {tzSaving ? "guardando…" : "guardar zona"}
              </Button>
            </form>

            {tzNotice !== null && (
              <p role="status" className="text-mint">
                &gt; {tzNotice}
              </p>
            )}
            {tzError !== null && (
              <p role="alert" className="text-mint">
                &gt; {tzError}
              </p>
            )}
          </div>
        )}
      </Card>

      <NotificationPreferences />

      <Card title="conexión gmail (opt-in)">
        <div className="lowercase flex flex-col gap-3 text-lavender">
          {gmailNotice !== null && (
            <div
              role={gmailNotice.kind === "error" ? "alert" : "status"}
              className="border-2 border-mint px-3 py-2 text-mint"
            >
              {gmailNotice.kind === "connected" ? (
                <p>&gt; permiso gmail.send conectado para tus recordatorios opt-in.</p>
              ) : (
                <p>&gt; {gmailErrorCopy(gmailNotice.reason)}</p>
              )}
            </div>
          )}

          <p className="text-lavender/80">
            solo concede envío (`gmail.send`) a tu dirección verificada; nunca lee tu bandeja y no
            envía correos hasta activar el opt-in arriba.
          </p>

          {!statusLoading && statusError === null && (
            <div className="flex flex-wrap items-center gap-3">
              <StatusBadge status={gmailReady ? "connected" : "disconnected"} />
              <span className="text-xs text-lavender/70">
                {gmailReady ? "permiso gmail.send concedido" : "sin permiso gmail.send"}
              </span>
              <a
                href={GOOGLE_GMAIL_CONNECT_PATH}
                className="inline-flex w-fit items-center gap-1 border-2 border-lavender/40 px-2 py-0.5 text-mint underline decoration-dotted hover:border-mint hover:bg-mint hover:text-on-accent"
              >
                {gmailReady ? "reconectar gmail.send" : "conectar gmail (opt-in)"}
              </a>
            </div>
          )}
        </div>
      </Card>

      <DeliveryPanel />

      <Card title="google calendar">
        {calendarNotice !== null && (
          <div
            role={calendarNotice.kind === "error" ? "alert" : "status"}
            className="lowercase mb-4 flex flex-col gap-1 border-2 border-mint px-3 py-2 text-mint"
          >
            {calendarNotice.kind === "connected" ? (
              <>
                <p>&gt; google calendar conectado.</p>
                <p className="text-xs">
                  las tareas bloqueadas volvieron a pendiente; nada se confirmó automáticamente.
                </p>
              </>
            ) : (
              <p>&gt; {calendarErrorCopy(calendarNotice.reason)}</p>
            )}
          </div>
        )}

        {statusLoading ? (
          <p className="lowercase text-lavender/60">&gt; comprobando Google Calendar…</p>
        ) : statusError !== null ? (
          <p role="alert" className="lowercase text-mint">
            &gt; {statusError}
          </p>
        ) : google !== null ? (
          <GoogleReconnect google={google} />
        ) : null}
      </Card>

      <AccountDeletionPanel />
    </div>
  );
}
