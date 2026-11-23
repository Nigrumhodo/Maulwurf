import { useMemo, useState } from "react";
import { Button } from "@/components/ui/Button";
import { DateInput } from "@/components/ui/DateInput";
import { Input } from "@/components/ui/Input";
import { TimezoneSelect } from "@/components/ui/TimezoneSelect";
import { ApiHttpError } from "@/lib/api";
import type { Subject } from "@/lib/subjects";
import {
  DEFAULT_EVENT_DURATION_MINUTES,
  addMinutes,
  buildScheduleFields,
  createTask,
  dateOnlyAddDays,
  wallTimeToInstant,
} from "@/lib/tasks";
import type { DueMode, EventMode, Task, TaskCreateInput } from "@/lib/tasks";
import { TIMEZONES } from "@/lib/timezones";

const ACTIVITY_TYPES = [
  "tarea",
  "entrega",
  "examen",
  "lectura",
  "proyecto",
  "recordatorio",
  "recomendacion",
] as const;

const selectClass =
  "bg-surface text-lavender border-2 border-lavender px-3 py-2 caret-mint focus:border-mint focus:outline-none";

function defaultTimezone(): string {
  try {
    const resolved = Intl.DateTimeFormat().resolvedOptions().timeZone;
    if ((TIMEZONES as readonly string[]).includes(resolved)) {
      return resolved;
    }
  } catch {
    // Fall back to Bogota when Intl does not report an IANA identifier.
  }
  return "America/Bogota";
}

function fieldError(error: unknown): string {
  if (error instanceof ApiHttpError) return error.firstFieldMessage()?.message ?? error.message;
  return "no se pudo crear la tarea; revisa la conexión.";
}

export interface TaskCreateFormProps {
  subjects: Subject[];
  defaultSubjectId?: string;
  onCreated: (task: Task, openReview: boolean) => void;
  onCancel: () => void;
}

export function TaskCreateForm({
  subjects,
  defaultSubjectId = "",
  onCreated,
  onCancel,
}: TaskCreateFormProps) {
  const [titulo, setTitulo] = useState("");
  const [tipo, setTipo] = useState<string>("tarea");
  const [subjectId, setSubjectId] = useState<string>(defaultSubjectId);
  const [detalle, setDetalle] = useState("");
  const [dueMode, setDueMode] = useState<DueMode>("all_day");
  const [dueDate, setDueDate] = useState("");
  const [dueTime, setDueTime] = useState("09:00");
  const [timezone, setTimezone] = useState(defaultTimezone);
  const [eventMode, setEventMode] = useState<EventMode>("all_day");
  const [eventTouched, setEventTouched] = useState(false);
  const [eventStartDate, setEventStartDate] = useState("");
  const [eventStartTime, setEventStartTime] = useState("09:00");
  const [eventEndDate, setEventEndDate] = useState("");
  const [durationMinutes, setDurationMinutes] = useState(DEFAULT_EVENT_DURATION_MINUTES);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const eventEnd = useMemo(() => {
    if (eventMode !== "timed") return null;
    const start = wallTimeToInstant(eventStartDate, eventStartTime, timezone);
    return start === null ? null : addMinutes(start, durationMinutes);
  }, [durationMinutes, eventMode, eventStartDate, eventStartTime, timezone]);

  function handleDueModeChange(next: DueMode) {
    setDueMode(next);
    setError(null);
    if (!eventTouched) {
      if (next === "missing") {
        setEventMode("none");
      } else if (next === "all_day") {
        setEventMode("all_day");
        if (dueDate !== "") {
          setEventStartDate(dueDate);
          setEventEndDate(dateOnlyAddDays(dueDate, 1) ?? "");
        }
      } else {
        setEventMode("timed");
        if (dueDate !== "") {
          setEventStartDate(dueDate);
        }
        setEventStartTime(dueTime);
      }
    }
  }

  function handleDueDateChange(nextDate: string) {
    setDueDate(nextDate);
    setError(null);
    if (!eventTouched && nextDate !== "") {
      setEventStartDate(nextDate);
      setEventEndDate(dateOnlyAddDays(nextDate, 1) ?? "");
    }
  }

  function handleDueTimeChange(nextTime: string) {
    setDueTime(nextTime);
    setError(null);
    if (!eventTouched) {
      setEventStartTime(nextTime);
    }
  }

  function handleEventModeChange(next: EventMode) {
    setEventTouched(true);
    setEventMode(next);
    setError(null);
    if (next === "all_day" && eventStartDate === "" && dueDate !== "") {
      setEventStartDate(dueDate);
      setEventEndDate(dateOnlyAddDays(dueDate, 1) ?? "");
    } else if (next === "timed" && eventStartDate === "" && dueDate !== "") {
      setEventStartDate(dueDate);
      setEventStartTime(dueTime);
    }
  }

  function buildPayload(): TaskCreateInput | string {
    if (titulo.trim() === "") return "el título no puede estar vacío.";

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
      source: "manual",
      tipo,
      titulo: titulo.trim(),
      detalle: detalle.trim() === "" ? null : detalle.trim(),
      subject_id: subjectId !== "" ? subjectId : null,
      ...schedule,
    };
  }

  async function submit(openReview: boolean) {
    const payload = buildPayload();
    if (typeof payload === "string") {
      setError(payload);
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const created = await createTask(payload);
      onCreated(created, openReview);
    } catch (cause) {
      setError(fieldError(cause));
    } finally {
      setBusy(false);
    }
  }

  const canReviewImmediately = dueMode !== "missing" && eventMode !== "none";

  return (
    <section aria-label="nueva tarea manual" className="flex flex-col gap-4">
      <h2 className="lowercase text-xl text-mint">nueva tarea manual</h2>
      <div className="flex flex-col gap-4">
        <div className="grid gap-3 sm:grid-cols-3">
          <div className="sm:col-span-2">
            <Input
              label="título"
              placeholder="ej. Entregar taller de simulación"
              value={titulo}
              maxLength={200}
              onChange={(event) => setTitulo(event.target.value)}
            />
          </div>
          <label className="capitalize font-pixel flex flex-col gap-1">
            <span className="text-mint">&gt; tipo</span>
            <select
              aria-label="tipo de actividad nueva"
              value={tipo}
              onChange={(event) => setTipo(event.target.value)}
              className={selectClass}
            >
              {ACTIVITY_TYPES.map((option) => (
                <option key={option} value={option}>
                  {option}
                </option>
              ))}
            </select>
          </label>
        </div>

        <div className="grid gap-3 sm:grid-cols-2">
          <label className="capitalize font-pixel flex flex-col gap-1">
            <span className="text-mint">&gt; materia (opcional)</span>
            <select
              aria-label="materia de la nueva tarea"
              value={subjectId}
              onChange={(event) => setSubjectId(event.target.value)}
              className={selectClass}
            >
              <option value="">sin materia</option>
              {subjects.map((subject) => (
                <option key={subject.id} value={subject.id}>
                  {subject.name}
                </option>
              ))}
            </select>
          </label>
          <TimezoneSelect
            label="zona horaria"
            value={timezone}
            onChange={(event) => setTimezone(event.target.value)}
          />
        </div>

        <label className="capitalize font-pixel flex flex-col gap-1">
          <span className="text-mint">&gt; detalle (opcional)</span>
          <textarea
            aria-label="detalle de la nueva tarea"
            rows={2}
            maxLength={2000}
            value={detalle}
            onChange={(event) => setDetalle(event.target.value)}
            className="min-h-16 resize-none bg-surface text-lavender border-2 border-lavender px-3 py-2 caret-mint focus:border-mint focus:outline-none"
          />
        </label>

        <fieldset className="flex flex-col gap-3 border-2 border-lavender/30 p-3">
          <legend className="capitalize px-1 text-mint">fecha de vencimiento</legend>
          <div className="grid gap-3 sm:grid-cols-3">
            <label className="flex flex-col gap-1">
              <span className="text-lavender/60">modo</span>
              <select
                aria-label="modo de vencimiento nueva tarea"
                value={dueMode}
                onChange={(event) => handleDueModeChange(event.target.value as DueMode)}
                className={selectClass}
              >
                <option value="all_day">todo el día</option>
                <option value="timed">con hora</option>
                <option value="missing">sin fecha</option>
              </select>
            </label>
            {dueMode !== "missing" && (
              <DateInput
                label="fecha"
                value={dueDate}
                onChange={(event) => handleDueDateChange(event.target.value)}
              />
            )}
            {dueMode === "timed" && (
              <Input
                type="time"
                label="hora"
                value={dueTime}
                onChange={(event) => handleDueTimeChange(event.target.value)}
              />
            )}
          </div>
        </fieldset>

        <fieldset className="flex flex-col gap-3 border-2 border-lavender/30 p-3">
          <legend className="capitalize px-1 text-mint">bloque de agenda (calendar)</legend>
          <p className="text-base text-lavender/60">
            se rellena automáticamente con tu fecha de vencimiento; puedes ajustarlo o dejarlo sin
            bloque si aún no quieres agendarla.
          </p>
          <label className="flex flex-col gap-1">
            <span className="text-lavender/60">modo</span>
            <select
              aria-label="modo de agenda nueva tarea"
              value={eventMode}
              onChange={(event) => handleEventModeChange(event.target.value as EventMode)}
              className={selectClass}
            >
              <option value="all_day">todo el día</option>
              <option value="timed">con hora</option>
              <option value="none">sin bloque</option>
            </select>
          </label>
          {eventMode === "all_day" && (
            <div className="grid gap-3 sm:grid-cols-2">
              <DateInput
                label="inicio de agenda"
                value={eventStartDate}
                onChange={(event) => {
                  setEventTouched(true);
                  setEventStartDate(event.target.value);
                  if (eventEndDate === "" || eventEndDate <= event.target.value) {
                    setEventEndDate(dateOnlyAddDays(event.target.value, 1) ?? "");
                  }
                }}
              />
              <DateInput
                label="fin de agenda (exclusivo)"
                value={eventEndDate}
                onChange={(event) => {
                  setEventTouched(true);
                  setEventEndDate(event.target.value);
                }}
              />
            </div>
          )}
          {eventMode === "timed" && (
            <div className="grid gap-3 sm:grid-cols-2">
              <DateInput
                label="inicio de agenda"
                value={eventStartDate}
                onChange={(event) => {
                  setEventTouched(true);
                  setEventStartDate(event.target.value);
                }}
              />
              <Input
                type="time"
                label="hora de inicio de agenda"
                value={eventStartTime}
                onChange={(event) => {
                  setEventTouched(true);
                  setEventStartTime(event.target.value);
                }}
              />
              <Input
                type="number"
                label="duración de agenda (minutos)"
                min={1}
                max={1440}
                value={durationMinutes}
                onChange={(event) => {
                  setEventTouched(true);
                  setDurationMinutes(Math.max(1, Number(event.target.value) || 1));
                }}
              />
              <p className="self-end text-sm text-lavender/80">
                fin calculado:{" "}
                {eventEnd === null
                  ? "pendiente de fecha"
                  : new Intl.DateTimeFormat("es", {
                      dateStyle: "short",
                      timeStyle: "short",
                      timeZone: timezone,
                    }).format(new Date(eventEnd))}
              </p>
            </div>
          )}
        </fieldset>

        {error !== null && (
          <p role="alert" className="border-2 border-mint px-3 py-2 text-mint">
            &gt; {error}
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2">
          {canReviewImmediately && (
            <Button type="button" disabled={busy} onClick={() => void submit(true)}>
              {busy ? "creando…" : "crear y revisar"}
            </Button>
          )}
          <Button
            type="button"
            variant={canReviewImmediately ? "secondary" : "primary"}
            disabled={busy}
            onClick={() => void submit(false)}
          >
            {busy ? "creando…" : "guardar tarea"}
          </Button>
          <Button type="button" variant="ghost" disabled={busy} onClick={onCancel}>
            cancelar
          </Button>
        </div>
      </div>
    </section>
  );
}
