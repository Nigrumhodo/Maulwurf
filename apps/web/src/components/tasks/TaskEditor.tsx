import { useMemo, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { DateInput } from "@/components/ui/DateInput";
import { Input } from "@/components/ui/Input";
import { TimezoneSelect } from "@/components/ui/TimezoneSelect";
import { ApiHttpError } from "@/lib/api";
import {
  DEFAULT_EVENT_DURATION_MINUTES,
  addMinutes,
  dateOnlyAddDays,
  instantToWall,
  patchTask,
  wallTimeToInstant,
} from "@/lib/tasks";
import type { Task, TaskEventBlockPatch, TaskPatch, TaskUpdate } from "@/lib/tasks";

type DueMode = "missing" | "all_day" | "timed";
type EventMode = "none" | "all_day" | "timed";

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
  if (error instanceof ApiHttpError) {
    const fields = error.details.fields;
    if (typeof fields === "object" && fields !== null && !Array.isArray(fields)) {
      const first = Object.values(fields)[0];
      if (typeof first === "string" && first !== "") return first;
    }
    return error.message;
  }
  return "no se pudo guardar la tarea; revisa la conexión.";
}

export interface TaskEditorProps {
  task: Task;
  onSaved: (update: TaskUpdate) => void;
  onCancel: () => void;
}

const selectClass =
  "bg-surface text-lavender border-2 border-lavender px-3 py-2 caret-mint focus:border-mint focus:outline-none";

export function TaskEditor({ task, onSaved, onCancel }: TaskEditorProps) {
  const dueWall = instantToWall(task.due_at, task.timezone);
  const eventStartWall = instantToWall(task.event_block.start_at, task.timezone);
  const initialDueMode = dueModeOf(task);
  const initialEventMode = eventModeOf(task);

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
    if (titulo.trim() === "") return "el título no puede estar vacío.";
    const resolvedDue =
      dueMode === "all_day"
        ? { due_date: dueDate, due_at: null, all_day: true }
        : dueMode === "timed"
          ? {
              due_date: null,
              due_at: wallTimeToInstant(dueDate, dueTime, timezone),
              all_day: false,
            }
          : { due_date: null, due_at: null, all_day: false };
    if (dueMode !== "missing" && resolvedDue.due_at === null && dueMode === "timed") {
      return "la hora de vencimiento no existe en esta zona horaria.";
    }
    if (dueMode === "all_day" && dueDate === "") return "elige la fecha de vencimiento.";

    let eventBlock: TaskEventBlockPatch | null = null;
    if (eventMode === "all_day") {
      if (eventStartDate === "" || eventEndDate === "") {
        return "el bloque de agenda necesita fecha de inicio y fin.";
      }
      if (eventEndDate <= eventStartDate) {
        return "el fin de agenda debe ser posterior al inicio.";
      }
      eventBlock = {
        start_at: null,
        end_at: null,
        start_date: eventStartDate,
        end_date_exclusive: eventEndDate,
      };
    } else if (eventMode === "timed") {
      const startAt = wallTimeToInstant(eventStartDate, eventStartTime, timezone);
      if (startAt === null) return "la hora de inicio de agenda no existe en esta zona.";
      eventBlock = {
        start_at: startAt,
        end_at: addMinutes(startAt, durationMinutes),
        start_date: null,
        end_date_exclusive: null,
      };
      if (eventBlock.end_at === null) return "la duración de agenda no es válida.";
    }

    return {
      version: task.version,
      titulo: titulo.trim(),
      detalle: detalle.trim() === "" ? null : detalle,
      date_status: dueMode === "missing" ? "missing" : "resolved",
      due_date: resolvedDue.due_date,
      due_at: resolvedDue.due_at,
      timezone,
      all_day: resolvedDue.all_day,
      date_text: dueMode === "missing" ? null : task.date_text,
      date_reason: dueMode === "missing" ? null : task.date_reason,
      event_block: eventBlock,
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
    try {
      const update = await patchTask(task.id, patch);
      setSavedRevision(update.revision_id);
      onSaved(update);
    } catch (cause) {
      setError(fieldError(cause));
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="editar tarea" variant="accent">
      <div className="flex flex-col gap-4">
        <div className="grid gap-3 sm:grid-cols-2">
          <Input
            label="título"
            value={titulo}
            maxLength={200}
            onChange={(event) => setTitulo(event.target.value)}
          />
          <label className="capitalize font-pixel flex flex-col gap-1">
            <span className="text-mint">&gt; detalle</span>
            <textarea
              aria-label="detalle"
              rows={3}
              maxLength={2000}
              value={detalle}
              onChange={(event) => setDetalle(event.target.value)}
              className="min-h-20 resize-none bg-surface text-lavender border-2 border-lavender px-3 py-2 caret-mint focus:border-mint focus:outline-none"
            />
          </label>
        </div>

        <fieldset className="flex flex-col gap-3 border-2 border-lavender/30 p-3">
          <legend className="capitalize px-1 text-mint">fecha de vencimiento</legend>
          <label className="flex flex-col gap-1">
            <span className="text-lavender/60">modo</span>
            <select
              aria-label="modo de vencimiento"
              value={dueMode}
              onChange={(event) => setDueMode(event.target.value as DueMode)}
              className={selectClass}
            >
              <option value="missing">sin fecha</option>
              <option value="all_day">todo el día</option>
              <option value="timed">con hora</option>
            </select>
          </label>
          {ambiguousOriginal && (
            <p role="status" className="text-mint">
              la fecha original era ambigua: “{task.date_text}”
              {task.date_reason !== null ? ` (${task.date_reason})` : ""}. Elige una fecha
              concreta o guárdala sin fecha.
            </p>
          )}
          {dueMode !== "missing" && (
            <div className="grid gap-3 sm:grid-cols-2">
              <DateInput
                label="fecha"
                value={dueDate}
                onChange={(event) => setDueDate(event.target.value)}
              />
              {dueMode === "timed" && (
                <Input
                  type="time"
                  label="hora"
                  value={dueTime}
                  onChange={(event) => setDueTime(event.target.value)}
                />
              )}
              <TimezoneSelect
                label="zona horaria"
                value={timezone}
                onChange={(event) => setTimezone(event.target.value)}
              />
            </div>
          )}
        </fieldset>

        <fieldset className="flex flex-col gap-3 border-2 border-lavender/30 p-3">
          <legend className="capitalize px-1 text-mint">bloque de agenda</legend>
          <p className="text-xs text-lavender/50">
            propuesta editable de Maulwurf; no proviene del docente.
          </p>
          <label className="flex flex-col gap-1">
            <span className="text-lavender/60">modo</span>
            <select
              aria-label="modo de agenda"
              value={eventMode}
              onChange={(event) => changeEventMode(event.target.value as EventMode)}
              className={selectClass}
            >
              <option value="none">sin bloque</option>
              <option value="all_day">todo el día</option>
              <option value="timed">con hora</option>
            </select>
          </label>
          {eventMode === "all_day" && (
            <div className="grid gap-3 sm:grid-cols-2">
              <DateInput
                label="inicio de agenda"
                value={eventStartDate}
                onChange={(event) => setEventStartDate(event.target.value)}
              />
              <DateInput
                label="fin de agenda (exclusivo)"
                value={eventEndDate}
                onChange={(event) => setEventEndDate(event.target.value)}
              />
            </div>
          )}
          {eventMode === "timed" && (
            <div className="grid gap-3 sm:grid-cols-2">
              <DateInput
                label="inicio de agenda"
                value={eventStartDate}
                onChange={(event) => setEventStartDate(event.target.value)}
              />
              <Input
                type="time"
                label="hora de inicio de agenda"
                value={eventStartTime}
                onChange={(event) => setEventStartTime(event.target.value)}
              />
              <Input
                type="number"
                label="duración de agenda (minutos)"
                min={1}
                max={1440}
                value={durationMinutes}
                onChange={(event) =>
                  setDurationMinutes(Math.max(1, Number(event.target.value) || 1))
                }
              />
              <p className="self-end text-sm text-lavender/70">
                fin calculado:{" "}
                {eventEnd === null
                  ? "hora inválida"
                  : new Intl.DateTimeFormat("es", {
                      dateStyle: "short",
                      timeStyle: "short",
                      timeZone: timezone,
                    }).format(new Date(eventEnd))}
              </p>
            </div>
          )}
          {eventMode === "all_day" && (
            <p className="text-xs text-lavender/50">
              fin exclusivo: el día indicado no se muestra en el evento.
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
            &gt; cambio pendiente de confirmación; la tarea activa no se modificó.
          </p>
        )}

        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" disabled={busy} onClick={() => void handleSubmit()}>
            {busy ? "guardando…" : "guardar cambios"}
          </Button>
          <Button type="button" variant="secondary" disabled={busy} onClick={onCancel}>
            cancelar
          </Button>
        </div>
      </div>
    </Card>
  );
}
