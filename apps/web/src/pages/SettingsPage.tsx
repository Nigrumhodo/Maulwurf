import { useEffect, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { Card } from "@/components/ui/Card";
import { GoogleReconnect } from "@/components/calendar/GoogleReconnect";
import { NotificationPreferences } from "@/components/settings/NotificationPreferences";
import { useAuth } from "@/components/auth/AuthProvider";
import {
  calendarErrorCopy,
  fetchIntegrationsStatus,
} from "@/lib/integrations";
import type { GoogleStatus } from "@/lib/integrations";

// D1.2 — Settings. D3.7 adds the Google Calendar panel and the result of the consent
// redirect (`/settings?calendar=connected|error&reason=`). Reconnecting only resumes
// blocked tasks; nothing is confirmed and no event is written from this page. D4.1 adds
// the M8 notification preferences; saving them never sends mail.

interface CalendarNotice {
  kind: "connected" | "error";
  reason: string | null;
}

export function SettingsPage() {
  const { user } = useAuth();
  const [searchParams, setSearchParams] = useSearchParams();
  const [notice, setNotice] = useState<CalendarNotice | null>(null);
  const [google, setGoogle] = useState<GoogleStatus | null>(null);
  const [statusError, setStatusError] = useState<string | null>(null);
  const [statusLoading, setStatusLoading] = useState(true);

  useEffect(() => {
    const result = searchParams.get("calendar");
    if (result === null) return;
    setNotice({
      kind: result === "connected" ? "connected" : "error",
      reason: searchParams.get("reason"),
    });
    // Consume the redirect params so a refresh does not repeat the banner.
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
  }, [notice]);

  return (
    <div className="flex flex-col gap-6">
      <h1 className="capitalize text-2xl text-mint">settings</h1>

      <Card title="perfil">
        {user === null ? (
          <p className="lowercase text-lavender">sin perfil cargado.</p>
        ) : (
          <dl className="lowercase flex flex-col gap-2 text-lavender">
            <div>
              <dt className="text-mint">&gt; email</dt>
              <dd>{user.email}</dd>
            </div>
            <div>
              <dt className="text-mint">&gt; zona horaria</dt>
              <dd>{user.timezone}</dd>
            </div>
          </dl>
        )}
      </Card>

      <NotificationPreferences />

      <Card title="google calendar">
        {notice !== null && (
          <div
            role={notice.kind === "error" ? "alert" : "status"}
            className="lowercase mb-4 flex flex-col gap-1 border-2 border-mint px-3 py-2 text-mint"
          >
            {notice.kind === "connected" ? (
              <>
                <p>&gt; google calendar conectado.</p>
                <p className="text-xs">
                  las tareas bloqueadas volvieron a pendiente; nada se confirmó automáticamente.
                </p>
              </>
            ) : (
              <p>&gt; {calendarErrorCopy(notice.reason)}</p>
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
    </div>
  );
}
