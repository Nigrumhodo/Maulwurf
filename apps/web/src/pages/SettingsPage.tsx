import { useEffect, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { useAuth } from "@/components/auth/AuthProvider";
import { GoogleReconnect } from "@/components/calendar/GoogleReconnect";
import { AccountDeletionPanel } from "@/components/settings/AccountDeletionPanel";
import { LegalLinks } from "@/components/legal/LegalLinks";
import { DeliveryPanel } from "@/components/settings/DeliveryPanel";
import { NotificationPreferences } from "@/components/settings/NotificationPreferences";
import { Button } from "@/components/ui/Button";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { TimezoneSelect } from "@/components/ui/TimezoneSelect";
import { UiLanguageSwitcher } from "@/components/ui/UiLanguageSwitcher";
import { updateTimezone } from "@/lib/auth";
import { ApiHttpError } from "@/lib/api";
import {
  calendarSyncErrorCopy,
  fetchCalendarSettings,
  fetchGoogleCalendarList,
  syncCalendarMirror,
  updateCalendarSettings,
  type GoogleCalendarListItem,
} from "@/lib/calendar";
import {
  GOOGLE_GMAIL_CONNECT_PATH,
  calendarErrorCopy,
  fetchIntegrationsStatus,
  gmailErrorCopy,
  hasGmailSendScope,
} from "@/lib/integrations";
import type { GoogleStatus } from "@/lib/integrations";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

// D1.2 / D3.7 / D4.1 / D4.2 — Settings: editable timezone (`PATCH /me`), notification
// preferences, Google Calendar + optional Gmail `gmail.send` connection, M8 delivery history
// with duplicate-acknowledged resend, and observable account deletion.

interface RedirectNotice {
  kind: "connected" | "error";
  reason: string | null;
}

export function SettingsPage() {
  const { t } = useTranslation("settings");
  useDocumentTitle(t("page.title"));
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

  const [readIds, setReadIds] = useState<string[]>([]);
  const [remoteCalendars, setRemoteCalendars] = useState<GoogleCalendarListItem[]>([]);
  const [calListLoading, setCalListLoading] = useState(false);
  const [calSaving, setCalSaving] = useState(false);
  const [calSyncing, setCalSyncing] = useState(false);
  const [calSettingsNotice, setCalSettingsNotice] = useState<string | null>(null);
  const [calSettingsError, setCalSettingsError] = useState<string | null>(null);

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
        setStatusError(t("page.statusCheckFailed"));
      })
      .finally(() => {
        if (!cancelled) setStatusLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [calendarNotice, gmailNotice, t]);

  async function handleSaveTimezone(event: React.FormEvent): Promise<void> {
    event.preventDefault();
    if (user === null || timezoneDraft.trim() === "" || timezoneDraft === user.timezone) return;
    setTzSaving(true);
    setTzNotice(null);
    setTzError(null);
    try {
      const updated = await updateTimezone(timezoneDraft);
      setTimezoneDraft(updated.timezone);
      setTzNotice(t("profile.timezoneUpdated"));
      if (typeof retrySession === "function") {
        await retrySession();
      }
    } catch (err) {
      setTzError(
        err instanceof ApiHttpError
          ? err.message
          : t("profile.timezoneSaveFailed"),
      );
    } finally {
      setTzSaving(false);
    }
  }

  const gmailReady = hasGmailSendScope(google);
  const calendarScopesReady =
    google !== null &&
    google.status === "connected" &&
    (google.calendar_write_ready ||
      google.scopes.includes("https://www.googleapis.com/auth/calendar.calendarlist.readonly"));

  useEffect(() => {
    if (!calendarScopesReady) return;
    let cancelled = false;
    fetchCalendarSettings()
      .then((settings) => {
        if (!cancelled) setReadIds(settings.read_calendar_ids);
      })
      .catch(() => {
        // Non-blocking: user can still reconnect or reload calendars explicitly.
      });
    return () => {
      cancelled = true;
    };
  }, [calendarScopesReady]);

  function toggleReadCalendar(calendarId: string): void {
    setReadIds((current) =>
      current.includes(calendarId)
        ? current.filter((item) => item !== calendarId)
        : [...current, calendarId],
    );
  }

  async function handleLoadGoogleCalendars(): Promise<void> {
    setCalListLoading(true);
    setCalSettingsError(null);
    setCalSettingsNotice(null);
    try {
      const list = await fetchGoogleCalendarList();
      setRemoteCalendars(list.items.filter((item) => !item.is_maulwurf_write));
      if (list.selected_ids.length > 0) {
        setReadIds(list.selected_ids);
      }
    } catch (err) {
      setCalSettingsError(calendarSyncErrorCopy(err));
    } finally {
      setCalListLoading(false);
    }
  }

  async function handleSaveReadCalendars(): Promise<void> {
    setCalSaving(true);
    setCalSettingsError(null);
    setCalSettingsNotice(null);
    try {
      const saved = await updateCalendarSettings(readIds);
      setReadIds(saved.read_calendar_ids);
      setCalSettingsNotice(t("calendars.readSaved"));
    } catch (err) {
      setCalSettingsError(calendarSyncErrorCopy(err));
    } finally {
      setCalSaving(false);
    }
  }

  async function handleManualCalendarSync(): Promise<void> {
    setCalSyncing(true);
    setCalSettingsError(null);
    setCalSettingsNotice(null);
    try {
      const synced = await syncCalendarMirror();
      setCalSettingsNotice(
        t("calendars.mirrorSynced", { inserted: synced.inserted, removed: synced.removed }),
      );
    } catch (err) {
      setCalSettingsError(calendarSyncErrorCopy(err));
    } finally {
      setCalSyncing(false);
    }
  }

  const h2 = "capitalize text-xl text-mint";
  const h3 = "capitalize text-lg text-lavender";
  const sections = [
    { id: "perfil", label: t("sections.profile") },
    { id: "notificaciones", label: t("sections.notifications") },
    { id: "entregas", label: t("sections.deliveries") },
    { id: "cuenta", label: t("sections.account") },
    { id: "privacidad", label: t("sections.privacy") },
  ];

  return (
    <div className="flex flex-col gap-4 border-2 border-pure-black bg-surface p-4 shadow-block sm:p-6">
      <header className="flex flex-col gap-1 border-b-2 border-lavender/30 pb-4">
        <h1 className="capitalize text-2xl text-mint">{t("page.title")}</h1>
        <p className="text-base text-lavender/80 max-w-prose">
          {t("page.subtitle")}
        </p>
      </header>

      <div className="flex flex-col gap-6 lg:flex-row lg:items-start lg:gap-8">
        <nav
          aria-label={t("page.sectionsLabel")}
          className="lg:sticky lg:top-24 lg:w-44 lg:shrink-0"
        >
          <ul className="flex flex-wrap gap-2 lg:flex-col lg:gap-0 lg:divide-y-2 lg:divide-lavender/20">
            {sections.map((section) => (
              <li key={section.id}>
                <a
                  href={`#${section.id}`}
                  className="capitalize inline-flex min-h-8 items-center border-2 border-lavender/30 px-3 text-lavender hover:border-mint hover:text-mint lg:flex lg:border-0 lg:px-0"
                >
                  {section.label}
                </a>
              </li>
            ))}
          </ul>
        </nav>

        <div className="flex min-w-0 flex-1 flex-col gap-8 divide-y-2 divide-lavender/30 [&>section]:pt-8 [&>section:first-child]:pt-0">
          <section id="perfil" aria-labelledby="ajustes-perfil" className="flex scroll-mt-24 flex-col gap-3">
            <h2 id="ajustes-perfil" className={h2}>{t("sections.profile")}</h2>
            <div className="lowercase flex flex-col gap-2 text-lavender">
              <span className="text-mint">&gt; {t("profile.uiLanguage")}</span>
              <div className="flex">
                <UiLanguageSwitcher />
              </div>
              <p className="text-base text-lavender/60">{t("profile.uiLanguageHint")}</p>
            </div>
        {user === null ? (
          <p className="lowercase text-lavender">{t("profile.none")}</p>
        ) : (
          <div className="lowercase flex flex-col gap-3 text-lavender">
            <dl className="flex flex-col gap-3">
              <div>
                <dt className="text-mint">&gt; {t("profile.email")}</dt>
                <dd>{user.email}</dd>
              </div>
              <div>
                <dt className="text-mint">&gt; {t("profile.timezone")}</dt>
                <dd>{user.timezone}</dd>
              </div>
            </dl>

            <form
              onSubmit={(event) => void handleSaveTimezone(event)}
              className="flex flex-wrap items-end gap-3 border-t-2 border-lavender/30 pt-3"
            >
              <div className="w-full max-w-sm">
              <TimezoneSelect
                label={t("profile.changeTimezone")}
                value={timezoneDraft}
                disabled={tzSaving}
                onChange={(event) => setTimezoneDraft(event.target.value)}
              />
              </div>
              <Button
                type="submit"
                size="sm"
                variant="secondary"
                disabled={tzSaving || timezoneDraft === "" || timezoneDraft === user.timezone}
              >
                {tzSaving ? t("profile.saving") : t("profile.saveTimezone")}
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
          </section>

          <section id="notificaciones" aria-labelledby="ajustes-notificaciones" className="flex scroll-mt-24 flex-col gap-6">
            <h2 id="ajustes-notificaciones" className={h2}>{t("sections.notifications")}</h2>
            <NotificationPreferences />
            <div className="flex flex-col gap-3 border-t-2 border-lavender/30 pt-6">
              <h3 className={h3}>{t("gmail.title")}</h3>
        <div className="lowercase flex flex-col gap-3 text-lavender">
          {gmailNotice !== null && (
            <div
              role={gmailNotice.kind === "error" ? "alert" : "status"}
              className="border-2 border-mint px-3 py-2 text-mint"
            >
              {gmailNotice.kind === "connected" ? (
                <p>&gt; {t("gmail.connected")}</p>
              ) : (
                <p>&gt; {gmailErrorCopy(gmailNotice.reason)}</p>
              )}
            </div>
          )}

          <p className="text-lavender/80">
            {t("gmail.explainer")}
          </p>

          {!statusLoading && statusError === null && (
            <div className="flex flex-wrap items-center gap-3">
              <StatusBadge status={gmailReady ? "connected" : "disconnected"} />
              <span className="text-sm text-lavender/80">
                {gmailReady ? t("gmail.granted") : t("gmail.notGranted")}
              </span>
              <a
                href={GOOGLE_GMAIL_CONNECT_PATH}
                className="inline-flex min-h-8 w-fit items-center gap-1 border-2 border-lavender/40 px-2 py-0.5 text-mint underline decoration-dotted hover:border-mint hover:bg-mint hover:text-on-accent"
              >
                {gmailReady ? t("gmail.reconnect") : t("gmail.connect")}
              </a>
            </div>
          )}
        </div>
            </div>
          </section>

          <section id="entregas" aria-labelledby="ajustes-entregas" className="flex scroll-mt-24 flex-col gap-3">
            <h2 id="ajustes-entregas" className={h2}>{t("sections.deliveries")}</h2>
            <h3 className={h3}>{t("deliveries.title")}</h3>
            <DeliveryPanel />
          </section>

          <section id="cuenta" aria-labelledby="ajustes-cuenta" className="flex scroll-mt-24 flex-col gap-6">
            <h2 id="ajustes-cuenta" className={h2}>{t("sections.account")}</h2>
            <div className="flex flex-col gap-3">
              <h3 className={h3}>{t("calendars.title")}</h3>
        {calendarNotice !== null && (
          <div
            role={calendarNotice.kind === "error" ? "alert" : "status"}
            className="lowercase mb-4 flex flex-col gap-1 border-2 border-mint px-3 py-2 text-mint"
          >
            {calendarNotice.kind === "connected" ? (
              <>
                <p>&gt; {t("calendars.connected")}</p>
                <p className="text-sm">
                  {t("calendars.unblocked")}
                </p>
              </>
            ) : (
              <p>&gt; {calendarErrorCopy(calendarNotice.reason)}</p>
            )}
          </div>
        )}

        {statusLoading ? (
          <p className="lowercase text-lavender/80">&gt; {t("calendars.checking")}</p>
        ) : statusError !== null ? (
          <p role="alert" className="lowercase text-mint">
            &gt; {statusError}
          </p>
        ) : google !== null ? (
          <div className="flex flex-col gap-3">
            <GoogleReconnect google={google} />

            {calendarScopesReady && (
              <div className="lowercase flex flex-col gap-3 border-t-2 border-lavender/30 pt-3 text-lavender">
                <p className="text-mint">&gt; {t("calendars.readTitle")}</p>
                <p className="text-sm text-lavender/80">
                  <Trans
                    t={t}
                    i18nKey="calendars.readExplainer"
                    components={{ strong: <strong /> }}
                  />
                </p>

                <label className="flex min-h-10 items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={readIds.includes("primary")}
                    onChange={() => toggleReadCalendar("primary")}
                  />
                  <span>{t("calendars.primary")}</span>
                </label>

                {remoteCalendars
                  .filter((item) => !item.primary && item.id !== "primary")
                  .map((item) => (
                    <label key={item.id} className="flex min-h-10 items-center gap-2 text-sm">
                      <input
                        type="checkbox"
                        checked={readIds.includes(item.id)}
                        onChange={() => toggleReadCalendar(item.id)}
                      />
                      <span>{item.summary}</span>
                    </label>
                  ))}

                <div className="flex flex-wrap items-center gap-2">
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    disabled={calSaving}
                    onClick={() => void handleSaveReadCalendars()}
                  >
                    {calSaving ? t("calendars.saving") : t("calendars.saveRead")}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    disabled={calListLoading}
                    onClick={() => void handleLoadGoogleCalendars()}
                  >
                    {calListLoading ? t("calendars.loading") : t("calendars.viewOthers")}
                  </Button>
                  <Button
                    type="button"
                    size="sm"
                    variant="secondary"
                    disabled={calSyncing || readIds.length === 0}
                    onClick={() => void handleManualCalendarSync()}
                  >
                    {calSyncing ? t("calendars.syncing") : t("calendars.syncNow")}
                  </Button>
                </div>

                {calSettingsNotice !== null && (
                  <p role="status" className="text-sm text-mint">
                    &gt; {calSettingsNotice}
                  </p>
                )}
                {calSettingsError !== null && (
                  <p role="alert" className="text-sm text-mint">
                    &gt; {calSettingsError}
                  </p>
                )}
              </div>
            )}
          </div>
        ) : null}
            </div>
            <div className="flex flex-col gap-3 border-t-2 border-lavender/30 pt-6">
              <h3 className={h3}>{t("account.dangerZone")}</h3>
              <AccountDeletionPanel />
            </div>
          </section>

          <section
            id="privacidad"
            aria-labelledby="ajustes-privacidad"
            className="flex scroll-mt-24 flex-col gap-3"
          >
            <h2 id="ajustes-privacidad" className={h2}>
              {t("sections.privacy")}
            </h2>
            <p className="max-w-prose text-lavender/80">
              <Trans
                t={t}
                i18nKey="privacy.deletion"
                components={{
                  acct: (
                    <a
                      href="#cuenta"
                      className="text-mint underline decoration-dotted hover:text-lavender"
                    />
                  ),
                }}
              />
            </p>
            <p className="max-w-prose text-lavender/80">{t("privacy.backups")}</p>
            <LegalLinks />
          </section>
        </div>
      </div>
    </div>
  );
}
