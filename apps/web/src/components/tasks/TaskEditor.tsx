import { useEffect, useMemo, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { DateInput } from "@/components/ui/DateInput";
import { Input } from "@/components/ui/Input";
import { TimezoneSelect } from "@/components/ui/TimezoneSelect";
import i18n, { intlLocale } from "@/i18n";
import { ApiHttpError } from "@/lib/api";
import {
  DEFAULT_EVENT_DURATION_MINUTES,
  addMinutes,
  buildScheduleFields,
  dateOnlyAddDays,
  instantToWall,
  patchTask,
  wallTimeToInstant,
} from "@/lib/tasks";
import type { DueMode, EventMode, Task, TaskPatch, TaskUpdate } from "@/lib/tasks";

const ACTIVITY_TYPES = [
  "tarea",
  "entrega",
  "examen",
  "lectura",
  "proyecto",
  "recordatorio",
  "recomendacion",
] as const;

function dueModeOf(task: Task): DueMode {
  if (task.date_status !== "resolved") return "missing";
  return task.due_date !== null ? "all_day" : "timed";
}

function eventModeOf(task: Task): EventMode {
  if (task.event_block.start_at !== null || task.event_block.end_at !== null) return "timed";
  if (
    task.event_block.start_date !== null ||
    task.event_block.end_date_exclusive !== null
  ) {
    return "all_day";
  }
  return "none";
}

function timedDuration(task: Task): number {
  if (task.event_block.start_at === null || task.event_block.end_at === null) {
    return DEFAULT_EVENT_DURATION_MINUTES;
  }
  const start = new Date(task.event_block.start_at).getTime();
  const end = new Date(task.event_block.end_at).getTime();
  if (Number.isNaN(start) || Number.isNaN(end) || end <= start) {
    return DEFAULT_EVENT_DURATION_MINUTES;
  }
  return Math.max(1, Math.round((end - start) / 60_000));
}

function fieldError(error: unknown): string {
  if (error instanceof ApiHttpError) return error.firstFieldMessage()?.message ?? error.message;
  return i18n.t("plan:taskForm.saveFailed");
}

export interface TaskEditorProps {
  task: Task;
  onSaved: (update: TaskUpdate) => void;
  onCancel: () => void;
}

const selectClass =
  "bg-surface text-lavender border-2 border-lavender px-3 py-2 caret-mint focus:border-mint focus:outline-none";

export function TaskEditor({ task, onSaved, onCancel }: TaskEditorProps) {
  const { t } = useTranslation("plan");
  const dueWall = instantToWall(task.due_at, task.timezone);
  const eventStartWall = instantToWall(task.event_block.start_at, task.timezone);
  const initialDueMode = dueModeOf(task);
  const initialEventMode = eventModeOf(task);

  const containerRef = useRef<HTMLDivElement | null>(null);
  const [tipo, setTipo] = useState(task.tipo);
  const [titulo, setTitulo] = useState(task.titulo);
  const [detalle, setDetalle] = useState(task.detalle ?? "");
  const [dueMode, setDueMode] = useState<DueMode>(initialDueMode);
  const [dueDate, setDueDate] = useState(
    task.due_date ?? dueWall?.date ?? "",
  );
  const [dueTime, setDueTime] = useState(dueWall?.time ?? "09:00");
  const [timezone, setTimezone] = useState(task.timezone);
  const [eventMode, setEventMode] = useState<EventMode>(initialEventMode);
  const [eventStartDate, setEventStartDate] = useState(
    task.event_block.start_date ?? eventStartWall?.date ?? "",
  );
  const [eventStartTime, setEventStartTime] = useState(eventStartWall?.time ?? "09:00");
  const [eventEndDate, setEventEndDate] = useState(
    task.event_block.end_date_exclusive ??
      (task.event_block.start_date !== null
        ? (dateOnlyAddDays(task.event_block.start_date, 1) ?? "")
        : ""),
  );
  const [durationMinutes, setDurationMinutes] = useState(timedDuration(task));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedRevision, setSavedRevision] = useState<string | null>(null);
  const [savedDirect, setSavedDirect] = useState(false);

  useEffect(() => {
    const el = containerRef.current;
    if (el !== null && typeof el.scrollIntoView === "function") {
      el.scrollIntoView({ block: "nearest", behavior: "smooth" });
    }
  }, []);

  const eventEnd = useMemo(() => {
    if (eventMode !== "timed") return null;
    const start = wallTimeToInstant(eventStartDate, eventStartTime, timezone);
    return start === null ? null : addMinutes(start, durationMinutes);
  }, [durationMinutes, eventMode, eventStartDate, eventStartTime, timezone]);

  const ambiguousOriginal =
    task.date_status === "ambiguous" &&
    (task.date_text !== null && task.date_text.trim() !== "");

  function changeEventMode(next: EventMode) {
    setEventMode(next);
    setError(null);
    if (next === "timed" && eventStartDate === "" && dueMode === "timed") {
      setEventStartDate(dueDate);
      setEventStartTime(dueTime);
      setDurationMinutes(DEFAULT_EVENT_DURATION_MINUTES);
    }
    if (next === "all_day" && eventStartDate === "" && dueMode === "all_day" && dueDate !== "") {
      setEventStartDate(dueDate);
      setEventEndDate(dateOnlyAddDays(dueDate, 1) ?? "");
    }
    if (next === "all_day" && eventStartDate !== "" && eventEndDate === "") {
      setEventEndDate(dateOnlyAddDays(eventStartDate, 1) ?? "");
    }
  }

  function buildPatch(): TaskPatch | string {
    if (titulo.trim() === "") return t("taskForm.titleRequired");
    const schedule = buildScheduleFields({
      dueMode,
      dueDate,
      dueTime,
      timezone,
      eventMode,
      eventStartDate,
      eventStartTime,
      eventEndDate,
      durationMinutes,
    });
    if (typeof schedule === "string") return schedule;

    return {
      version: task.version,
      ...(tipo !== task.tipo ? { tipo } : {}),
      titulo: titulo.trim(),
      detalle: detalle.trim() === "" ? null : detalle.trim(),
      ...schedule,
      date_reason: null,
    };
  }

  async function handleSubmit() {
    const patch = buildPatch();
    if (typeof patch === "string") {
      setError(patch);
      return;
    }
    setBusy(true);
    setError(null);
    setSavedDirect(false);
    try {
      const update = await patchTask(task.id, patch);
      setSavedRevision(update.revision_id);
      setSavedDirect(update.revision_id === null);
      onSaved(update);
    } catch (cause) {
      setError(fieldError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div ref={containerRef}>
      <section aria-label={t("taskForm.editLabel")} className="flex flex-col gap-4">
        <h2 className="lowercase text-xl text-mint">{t("taskForm.editLabel")}</h2>
        <div className="flex flex-col gap-4">
          <div className="grid gap-3 sm:grid-cols-2">
            <Input
              label={t("taskForm.title")}
              value={titulo}
              maxLength={200}
              onChange={(event) => setTitulo(event.target.value)}
            />
            <label className="capitalize font-pixel flex flex-col gap-1">
              <span className="text-mint">&gt; {t("taskForm.type")}</span>
              <select
                aria-label={t("taskForm.typeLabel")}
                value={tipo}
                onChange={(event) => setTipo(event.target.value)}
                className={selectClass}
              >
                {ACTIVITY_TYPES.map((option) => (
                  <option key={option} value={option}>
                    {t(`taskForm.activityTypes.${option}`)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <label className="capitalize font-pixel flex flex-col gap-1">
            <span className="text-mint">&gt; {t("taskForm.detail")}</span>
            <textarea
              aria-label={t("taskForm.detail")}
              rows={3}
              maxLength={2000}
              value={detalle}
              onChange={(event) => setDetalle(event.target.value)}
              className="min-h-20 resize-none bg-surface text-lavender border-2 border-lavender px-3 py-2 caret-mint focus:border-mint focus:outline-none"
            />
          </label>

        <fieldset className="flex flex-col gap-3 border-2 border-lavender/30 p-3">
          <legend className="capitalize px-1 text-mint">{t("taskForm.dueDate")}</legend>
          <label className="flex flex-col gap-1">
            <span className="text-lavender/60">{t("taskForm.mode")}</span>
            <select
              aria-label={t("taskForm.dueModeLabel")}
              value={dueMode}
              onChange={(event) => setDueMode(event.target.value as DueMode)}
              className={selectClass}
            >
              <option value="missing">{t("taskForm.noDate")}</option>
              <option value="all_day">{t("taskForm.allDay")}</option>
              <option value="timed">{t("taskForm.timed")}</option>
            </select>
          </label>
          {ambiguousOriginal && (
            <p role="status" className="text-mint">
              {t("taskForm.ambiguous", {
                text: task.date_text,
                reason: task.date_reason !== null ? ` (${task.date_reason})` : "",
              })}
            </p>
          )}
          {dueMode !== "missing" && (
            <div className="grid gap-3 sm:grid-cols-2">
              <DateInput
                label={t("taskForm.date")}
                value={dueDate}
                onChange={(event) => setDueDate(event.target.value)}
              />
              {dueMode === "timed" && (
                <Input
                  type="time"
                  label={t("taskForm.time")}
                  value={dueTime}
                  onChange={(event) => setDueTime(event.target.value)}
                />
              )}
              <TimezoneSelect
                label={t("taskForm.timezone")}
                value={timezone}
                onChange={(event) => setTimezone(event.target.value)}
              />
            </div>
          )}
        </fieldset>

        <fieldset className="flex flex-col gap-3 border-2 border-lavender/30 p-3">
          <legend className="capitalize px-1 text-mint">{t("taskForm.agendaBlock")}</legend>
          <p className="text-base text-lavender/60">
            {t("taskForm.agendaProposal")}
          </p>
          <label className="flex flex-col gap-1">
            <span className="text-lavender/60">{t("taskForm.mode")}</span>
            <select
              aria-label={t("taskForm.agendaModeLabel")}
              value={eventMode}
              onChange={(event) => changeEventMode(event.target.value as EventMode)}
              className={selectClass}
            >
              <option value="none">{t("taskForm.noBlock")}</option>
              <option value="all_day">{t("taskForm.allDay")}</option>
              <option value="timed">{t("taskForm.timed")}</option>
            </select>
          </label>
          {eventMode === "all_day" && (
            <div className="grid gap-3 sm:grid-cols-2">
              <DateInput
                label={t("taskForm.agendaStart")}
                value={eventStartDate}
                onChange={(event) => setEventStartDate(event.target.value)}
              />
              <DateInput
                label={t("taskForm.agendaEndExclusive")}
                value={eventEndDate}
                onChange={(event) => setEventEndDate(event.target.value)}
              />
            </div>
          )}
          {eventMode === "timed" && (
            <div className="grid gap-3 sm:grid-cols-2">
              <DateInput
                label={t("taskForm.agendaStart")}
                value={eventStartDate}
                onChange={(event) => setEventStartDate(event.target.value)}
              />
              <Input
                type="time"
                label={t("taskForm.agendaStartTime")}
                value={eventStartTime}
                onChange={(event) => setEventStartTime(event.target.value)}
              />
              <Input
                type="number"
                label={t("taskForm.agendaDuration")}
                min={1}
                max={1440}
                value={durationMinutes}
                onChange={(event) =>
                  setDurationMinutes(Math.max(1, Number(event.target.value) || 1))
                }
              />
              <p className="self-end text-sm text-lavender/80">
                {t("taskForm.computedEnd")}{" "}
                {eventEnd === null
                  ? t("taskForm.invalidTime")
                  : new Intl.DateTimeFormat(intlLocale(), {
                      dateStyle: "short",
                      timeStyle: "short",
                      timeZone: timezone,
                    }).format(new Date(eventEnd))}
              </p>
            </div>
          )}
          {eventMode === "all_day" && (
            <p className="text-base text-lavender/60">
              {t("taskForm.exclusiveEndHint")}
            </p>
          )}
        </fieldset>

        {error !== null && (
          <p role="alert" className="border-2 border-mint px-3 py-2 text-mint">
            &gt; {error}
          </p>
        )}
        {savedRevision !== null && (
          <p role="status" className="border-2 border-mint px-3 py-2 text-mint">
            &gt; {t("taskForm.revisionPending")}
          </p>
        )}
        {savedDirect && (
          <p role="status" className="border-2 border-mint px-3 py-2 text-mint">
            &gt; {t("taskForm.changesSaved")}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" disabled={busy} onClick={() => void handleSubmit()}>
            {busy ? t("taskForm.saving") : t("taskForm.saveChanges")}
          </Button>
          <Button type="button" variant="secondary" disabled={busy} onClick={onCancel}>
            {t("taskForm.cancel")}
          </Button>
        </div>
      </div>
      </section>
    </div>
  );
}
