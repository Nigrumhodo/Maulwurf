import FullCalendar from "@fullcalendar/react";
import dayGridPlugin from "@fullcalendar/daygrid";
import timeGridPlugin from "@fullcalendar/timegrid";
import esLocale from "@fullcalendar/core/locales/es";
import type { DatesSetArg } from "@fullcalendar/core";
import { useEffect, useMemo, useState } from "react";
import { Link } from "react-router-dom";
import { CalendarFreshness } from "@/components/calendar/CalendarFreshness";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  activeTasksWithoutBlock,
  buildCalendarEvents,
  fetchCalendarEvents,
  type CalendarEventsResponse,
} from "@/lib/calendar";
import { fetchSubjects } from "@/lib/subjects";
import type { Subject } from "@/lib/subjects";
import { fetchTasks, formatTaskDue } from "@/lib/tasks";
import type { Task } from "@/lib/tasks";

// D3.6 - Google events + Maulwurf tasks in one agenda. The merge happens in
// `buildCalendarEvents`: a linked task is drawn once, and the mirror row it owns is
// discarded. Google is read only; this component never writes Calendar.

export interface CalendarViewProps {
  compact?: boolean;
}

interface Range {
  from: string;
  to: string;
}

function dateOnly(value: Date): string {
  const year = value.getFullYear();
  const month = String(value.getMonth() + 1).padStart(2, "0");
  const day = String(value.getDate()).padStart(2, "0");
  return `${year}-${month}-${day}`;
}

function monthRange(now = new Date()): Range {
  return {
    from: dateOnly(new Date(now.getFullYear(), now.getMonth(), 1)),
    to: dateOnly(new Date(now.getFullYear(), now.getMonth() + 1, 0)),
  };
}

function rangeFromCalendar(info: DatesSetArg): Range {
  const end = new Date(info.end);
  end.setDate(end.getDate() - 1);
  return { from: dateOnly(info.start), to: dateOnly(end) };
}

function activeSubjects(tasks: Task[], subjects: Subject[]): Subject[] {
  const used = new Set(
    tasks
      .filter(
        (task) =>
          task.subject !== null &&
          ["pending", "needs_review", "confirmed"].includes(task.status),
      )
      .map((task) => task.subject!.id),
  );
  return subjects.filter((subject) => used.has(subject.id));
}

export function CalendarView({ compact = false }: CalendarViewProps) {
  const [range, setRange] = useState<Range>(() => monthRange());
  const [data, setData] = useState<CalendarEventsResponse | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tasksWarning, setTasksWarning] = useState<string | null>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    fetchCalendarEvents(range.from, range.to)
      .then((next) => {
        if (!cancelled) setData(next);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setData(null);
        setError(
          cause instanceof Error
            ? cause.message
            : "no se pudo cargar el calendario; revisa tu conexión.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [range.from, range.to, reloadKey]);

  useEffect(() => {
    let cancelled = false;
    fetchTasks({ status: "all", limit: 200 })
      .then((page) => {
        if (cancelled) return;
        setTasks(Array.isArray(page.items) ? page.items : []);
        setTasksWarning(
          page.next_cursor === null
            ? null
            : "solo se muestran las primeras 200 tareas; la agenda puede estar incompleta.",
        );
      })
      .catch(() => {
        if (cancelled) return;
        setTasks([]);
        setTasksWarning("no se pudieron cargar las tareas; solo se muestran eventos de Google.");
      });
    fetchSubjects()
      .then((items) => {
        if (!cancelled) setSubjects(items);
      })
      .catch(() => {
        if (!cancelled) setSubjects([]);
      });
    return () => {
      cancelled = true;
    };
  }, [reloadKey]);

  const subjectColors = useMemo(
    () => new Map(subjects.map((subject) => [subject.id, subject.color])),
    [subjects],
  );
  const built = useMemo(
    () => buildCalendarEvents(data?.events ?? [], tasks, subjectColors),
    [data?.events, tasks, subjectColors],
  );
  const usedSubjects = useMemo(() => activeSubjects(tasks, subjects), [tasks, subjects]);
  const withoutBlock = useMemo(() => activeTasksWithoutBlock(tasks), [tasks]);

  return (
    <Card title={compact ? "agenda" : "calendario"}>
      <div className="flex flex-col gap-3">
        <div className="grid gap-3 border-2 border-lavender/25 bg-dark/40 px-3 py-2 sm:grid-cols-[minmax(0,1fr)_auto] sm:items-center">
          <CalendarFreshness
            lastSyncedAt={data?.last_synced_at ?? null}
            coverageOk={data?.coverage_ok ?? false}
            coverageNotice={data === null ? "never_synced" : data.coverage_notice}
            stale={data?.stale ?? false}
          />
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <span className="lowercase text-lavender/50">leyenda:</span>
            <span className="border-2 border-[#64748b] bg-[#64748b] px-1.5 py-0.5 text-white">
              Google
            </span>
            {usedSubjects.map((subject) => (
              <span
                key={subject.id}
                className="border-2 px-1.5 py-0.5 text-on-accent"
                style={{ backgroundColor: subject.color, borderColor: subject.color }}
              >
                {subject.name}
              </span>
            ))}
          </div>
          {data !== null && !data.coverage_ok && (
            <Link
              to="/settings"
              className="lowercase text-xs text-mint underline decoration-dotted"
            >
              {data.coverage_notice === "never_synced"
                ? "conectar google calendar"
                : "revisar la integración de calendar"}
            </Link>
          )}
        </div>

        <div className="grid gap-2 text-xs sm:grid-cols-3">
          <div className="flex items-center justify-between border-2 border-lavender/25 px-2 py-1">
            <span className="lowercase text-lavender/60">en el rango</span>
            <span className="text-mint">{built.events.length}</span>
          </div>
          <div className="flex items-center justify-between border-2 border-lavender/25 px-2 py-1">
            <span className="lowercase text-lavender/60">tareas dibujadas</span>
            <span className="text-mint">
              {built.events.filter((event) => event.extendedProps.kind === "task").length}
            </span>
          </div>
          <div className="flex items-center justify-between border-2 border-lavender/25 px-2 py-1">
            <span className="lowercase text-lavender/60">sin bloque</span>
            <span className="text-mint">{withoutBlock.length}</span>
          </div>
        </div>

        {error !== null && (
          <div className="flex flex-wrap items-center gap-2">
            <p role="alert" className="lowercase text-mint">
              &gt; {error}
            </p>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => setReloadKey((value) => value + 1)}
            >
              reintentar
            </Button>
          </div>
        )}

        {tasksWarning !== null && (
          <p role="status" className="lowercase text-lavender/60">
            &gt; {tasksWarning}
          </p>
        )}

        <div aria-busy={loading} className="calendar-shell">
          <FullCalendar
            plugins={[dayGridPlugin, timeGridPlugin]}
            initialView="dayGridMonth"
            locale={esLocale}
            headerToolbar={{
              left: "prev,next today",
              center: "title",
              right: "dayGridMonth,timeGridWeek",
            }}
            height={compact ? "auto" : "78vh"}
            expandRows={!compact}
            events={built.events}
            datesSet={(info) => {
              const next = rangeFromCalendar(info);
              setRange((current) =>
                current.from === next.from && current.to === next.to ? current : next,
              );
            }}
          />
        </div>

        {withoutBlock.length > 0 && (
          <section className="border-2 border-lavender/25 px-3 py-2">
            <h3 className="capitalize text-sm text-mint">tareas sin bloque de agenda</h3>
            <p className="lowercase text-xs text-lavender/60">
              no se dibujan en el calendario porque no tienen un bloque confirmado.
            </p>
            <ul className="mt-2 flex flex-col gap-1">
              {withoutBlock.map((task) => {
                const due = formatTaskDue(task);
                return (
                  <li
                    key={task.id}
                    className="flex flex-wrap items-center gap-2 border-b border-lavender/15 pb-1 text-xs last:border-b-0"
                  >
                    <span className="normal-case text-lavender">{task.titulo}</span>
                    <StatusBadge status={task.status} />
                    <span className="text-lavender/50">
                      {due ?? task.date_text ?? "sin fecha"}
                    </span>
                    <Link
                      to="/gestion/tareas"
                      className="ml-auto text-mint underline decoration-dotted"
                    >
                      revisar
                    </Link>
                  </li>
                );
              })}
            </ul>
          </section>
        )}
      </div>
    </Card>
  );
}
