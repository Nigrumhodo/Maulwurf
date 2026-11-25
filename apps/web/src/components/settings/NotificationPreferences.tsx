import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { useAuth } from "@/components/auth/AuthProvider";
import { ApiHttpError } from "@/lib/api";
import {
  THRESHOLD_LIMITS,
  buildPreferencesPatch,
  fetchNotificationPreferences,
  isPreferencesDirty,
  notificationTestErrorMessage,
  preferencesErrorMessage,
  sendTestNotification,
  thresholdUnitLabel,
  updateNotificationPreferences,
  validatePreferencesDraft,
  weekdayLabels,
} from "@/lib/preferences";
import type {
  NotificationPreferences,
  NotificationPreferencesDraft,
  ThresholdKind,
} from "@/lib/preferences";

const selectClass =
  "bg-surface text-lavender border-2 border-lavender px-3 py-2 caret-mint focus:border-mint focus:outline-none";

function draftOf(preferences: NotificationPreferences): NotificationPreferencesDraft {
  return {
    gmail_opt_in: preferences.gmail_opt_in,
    digest_daily_enabled: preferences.digest_daily_enabled,
    digest_daily_time: preferences.digest_daily_time,
    digest_weekly_enabled: preferences.digest_weekly_enabled,
    digest_weekday: preferences.digest_weekday,
    reminder_offsets_hours: [...preferences.reminder_offsets_hours],
    reminder_allday_offsets_days: [...preferences.reminder_allday_offsets_days],
  };
}

interface ThresholdEditorProps {
  kind: ThresholdKind;
  title: string;
  hint: string;
  values: number[];
  onChange: (values: number[]) => void;
  disabled: boolean;
}

function ThresholdEditor({
  kind,
  title,
  hint,
  values,
  onChange,
  disabled,
}: ThresholdEditorProps) {
  const { t } = useTranslation("settings");
  const limits = THRESHOLD_LIMITS[kind];
  const nextValue = (): number => {
    const used = new Set(values);
    for (let candidate = limits.min; candidate <= limits.max; candidate += 1) {
      if (!used.has(candidate)) return candidate;
    }
    return limits.min;
  };

  return (
    <fieldset className="flex min-w-0 flex-col gap-2 border-0 border-t-2 border-lavender/30 p-0 pt-4 first:border-t-0 first:pt-0">
      <legend className="capitalize float-left mb-2 w-full p-0 text-lg text-mint">{title}</legend>
      <p className="text-sm text-lavender/80">{hint}</p>
      {values.length === 0 ? (
        <p className="lowercase text-lavender/80">
          {t("preferences.noThresholds")}
        </p>
      ) : (
        <ul className="flex flex-col gap-2">
          {values.map((value, index) => (
            <li key={`${kind}-${index}`} className="flex flex-wrap items-end gap-2">
              <Input
                type="number"
                label={t("preferences.thresholdLabel", {
                  index: index + 1,
                  unit: thresholdUnitLabel(kind),
                })}
                min={limits.min}
                max={limits.max}
                // Empty input becomes NaN rather than 0 so clearing stays visually blank
                // and is rejected explicitly by validation on save.
                value={Number.isNaN(value) ? "" : value}
                disabled={disabled}
                onChange={(event) => {
                  const next = [...values];
                  next[index] =
                    event.target.value === "" ? Number.NaN : Number(event.target.value);
                  onChange(next);
                }}
              />
              <Button
                type="button"
                variant="ghost"
                size="sm"
                disabled={disabled}
                onClick={() => onChange(values.filter((_, current) => current !== index))}
              >
                {t("preferences.remove")}
              </Button>
            </li>
          ))}
        </ul>
      )}
      <div>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={disabled || values.length >= limits.maxCount}
          onClick={() => onChange([...values, nextValue()])}
        >
          {t("preferences.addThreshold")}
        </Button>
      </div>
    </fieldset>
  );
}

export function NotificationPreferences() {
  const { t } = useTranslation("settings");
  const { user } = useAuth();
  const [saved, setSaved] = useState<NotificationPreferences | null>(null);
  const [draft, setDraft] = useState<NotificationPreferencesDraft | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);
  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [testing, setTesting] = useState(false);
  const [testError, setTestError] = useState<string | null>(null);
  const [testNotice, setTestNotice] = useState<string | null>(null);
  const mountedRef = useRef(true);
  const draftVersionRef = useRef(0);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setLoadError(null);
    fetchNotificationPreferences()
      .then((preferences) => {
        if (cancelled) return;
        setSaved(preferences);
        setDraft(draftOf(preferences));
      })
      .catch(() => {
        if (cancelled) return;
        setLoadError(t("preferences.loadFailed"));
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey, t]);

  function update<K extends keyof NotificationPreferencesDraft>(
    key: K,
    value: NotificationPreferencesDraft[K],
  ) {
    draftVersionRef.current += 1;
    setDraft((current) => (current === null ? current : { ...current, [key]: value }));
    setNotice(null);
    setSaveError(null);
  }

  async function handleSave() {
    if (saved === null || draft === null) return;
    const validation = validatePreferencesDraft(draft);
    if (validation !== null) {
      setSaveError(validation);
      return;
    }
    const patch = buildPreferencesPatch(draftOf(saved), draft);
    if (Object.keys(patch).length === 0) {
      setNotice(t("preferences.noChanges"));
      return;
    }
    const versionAtStart = draftVersionRef.current;
    setSaving(true);
    setSaveError(null);
    setNotice(null);
    try {
      const updated = await updateNotificationPreferences(patch);
      if (!mountedRef.current) return;
      setSaved(updated);
      // Preserve edits typed by the user while the request was in flight.
      if (draftVersionRef.current === versionAtStart) {
        setDraft(draftOf(updated));
      }
      setNotice(t("preferences.saved"));
      setTestError(null);
      setTestNotice(null);
    } catch (cause) {
      if (!mountedRef.current) return;
      const field = cause instanceof ApiHttpError ? cause.firstFieldMessage() : null;
      setSaveError(field?.message ?? preferencesErrorMessage(cause));
    } finally {
      if (mountedRef.current) setSaving(false);
    }
  }

  async function handleTest() {
    if (saved === null || draft === null) return;
    if (!saved.gmail_opt_in) {
      setTestError(t("preferences.testNeedsOptIn"));
      return;
    }
    if (isPreferencesDirty(draftOf(saved), draft)) {
      setTestError(t("preferences.testNeedsSave"));
      return;
    }
    setTesting(true);
    setTestError(null);
    setTestNotice(null);
    try {
      const result = await sendTestNotification();
      if (!mountedRef.current) return;
      setTestNotice(t("preferences.testRecorded", { message: result.warning.message }));
    } catch (cause) {
      if (!mountedRef.current) return;
      setTestError(notificationTestErrorMessage(cause));
    } finally {
      if (mountedRef.current) setTesting(false);
    }
  }

  if (loading) {
    return (
      <div>
        <p className="lowercase text-lavender/80">&gt; {t("preferences.loading")}</p>
      </div>
    );
  }

  if (loadError !== null) {
    return (
      <div>
        <p role="alert" className="lowercase text-mint">
          &gt; {loadError}
        </p>
        <div className="mt-3">
          <Button type="button" variant="secondary" onClick={() => setReloadKey((key) => key + 1)}>
            {t("preferences.retry")}
          </Button>
        </div>
      </div>
    );
  }

  if (saved === null || draft === null) return null;

  const dirty = isPreferencesDirty(draftOf(saved), draft);

  return (
    <div>
      <div className="flex flex-col gap-3">
        <fieldset className="flex min-w-0 flex-col gap-2 border-0 border-t-2 border-lavender/30 p-0 pt-4 first:border-t-0 first:pt-0">
          <legend className="capitalize float-left mb-2 w-full p-0 text-lg text-mint">Gmail</legend>
          <label className="flex min-h-10 items-center gap-2">
            <input
              type="checkbox"
              className="h-4 w-4 accent-mint"
              checked={draft.gmail_opt_in}
              disabled={saving}
              onChange={(event) => update("gmail_opt_in", event.target.checked)}
            />
            <span className="capitalize text-lavender">{t("preferences.gmailOptIn")}</span>
          </label>
          <p className="lowercase text-sm text-lavender/80">
            {t("preferences.whatIsSent")}
          </p>
          <p className="lowercase text-sm text-lavender/80">
            {t("preferences.toEmail", { email: user?.email ?? t("preferences.yourGoogleAccount") })}
          </p>
          <p className="lowercase text-sm text-lavender/80">
            {t("preferences.howToDisable")}
          </p>
        </fieldset>

        <ThresholdEditor
          kind="hours"
          title={t("preferences.timedTitle")}
          hint={t("preferences.timedHint")}
          values={draft.reminder_offsets_hours}
          onChange={(values) => update("reminder_offsets_hours", values)}
          disabled={saving}
        />

        <ThresholdEditor
          kind="days"
          title={t("preferences.allDayTitle")}
          hint={t("preferences.allDayHint")}
          values={draft.reminder_allday_offsets_days}
          onChange={(values) => update("reminder_allday_offsets_days", values)}
          disabled={saving}
        />

        <fieldset className="flex min-w-0 flex-col gap-2 border-0 border-t-2 border-lavender/30 p-0 pt-4 first:border-t-0 first:pt-0">
          <legend className="capitalize float-left mb-2 w-full p-0 text-lg text-mint">{t("preferences.dailyTitle")}</legend>
          <label className="flex min-h-10 items-center gap-2">
            <input
              type="checkbox"
              className="h-4 w-4 accent-mint"
              checked={draft.digest_daily_enabled}
              disabled={saving}
              onChange={(event) => update("digest_daily_enabled", event.target.checked)}
            />
            <span className="capitalize text-lavender">{t("preferences.dailyEnable")}</span>
          </label>
          <Input
            type="time"
            label={t("preferences.localTime")}
            value={draft.digest_daily_time}
            disabled={saving}
            onChange={(event) => update("digest_daily_time", event.target.value.slice(0, 5))}
          />
          <p className="lowercase text-sm text-lavender/80">
            {t("preferences.profileTimezone", { timezone: user?.timezone ?? t("preferences.noProfile") })}
          </p>
        </fieldset>

        <fieldset className="flex min-w-0 flex-col gap-2 border-0 border-t-2 border-lavender/30 p-0 pt-4 first:border-t-0 first:pt-0">
          <legend className="capitalize float-left mb-2 w-full p-0 text-lg text-mint">{t("preferences.weeklyTitle")}</legend>
          <label className="flex min-h-10 items-center gap-2">
            <input
              type="checkbox"
              className="h-4 w-4 accent-mint"
              checked={draft.digest_weekly_enabled}
              disabled={saving}
              onChange={(event) => update("digest_weekly_enabled", event.target.checked)}
            />
            <span className="capitalize text-lavender">{t("preferences.weeklyEnable")}</span>
          </label>
          <label className="flex max-w-sm flex-col gap-1">
            <span className="text-lavender/80">{t("preferences.day")}</span>
            <select
              aria-label={t("preferences.weeklyDayLabel")}
              value={draft.digest_weekday}
              disabled={saving}
              onChange={(event) => update("digest_weekday", Number(event.target.value))}
              className={selectClass}
            >
              {weekdayLabels().map((label, value) => (
                <option key={label} value={value}>
                  {label}
                </option>
              ))}
            </select>
          </label>
        </fieldset>

        {saveError !== null && (
          <p role="alert" className="border-2 border-mint px-3 py-2 text-mint">
            &gt; {saveError}
          </p>
        )}
        {notice !== null && (
          <p role="status" className="border-2 border-mint px-3 py-2 text-mint">
            &gt; {notice}
          </p>
        )}
        {testError !== null && (
          <p role="alert" className="border-2 border-mint px-3 py-2 text-mint">
            &gt; {testError}
          </p>
        )}
        {testNotice !== null && (
          <p role="status" className="border-2 border-mint px-3 py-2 text-mint">
            &gt; {testNotice}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" disabled={saving} onClick={() => void handleSave()}>
            {saving ? t("preferences.saving") : t("preferences.save")}
          </Button>
          <Button
            type="button"
            variant="secondary"
            disabled={testing || saving || !saved.gmail_opt_in || dirty}
            onClick={() => void handleTest()}
          >
            {testing ? t("preferences.testing") : t("preferences.sendTest")}
          </Button>
          {!saved.gmail_opt_in && (
            <span className="lowercase text-sm text-lavender/80">
              {t("preferences.testHint")}
            </span>
          )}
          {dirty && (
            <span className="lowercase text-sm text-lavender/80">
              {t("preferences.unsaved")}
            </span>
          )}
        </div>
      </div>
    </div>
  );
}
