import { useMemo } from "react";

// Dashboard-only horizontal strip: the current week at a glance, no event data. The
// full duplicate-free agenda stays on /gestion/agenda.

function startOfCurrentWeek(now = new Date()): Date {
  const start = new Date(now);
  const day = start.getDay();
  start.setDate(start.getDate() + (day === 0 ? -6 : 1 - day));
  start.setHours(0, 0, 0, 0);
  return start;
}

function sameLocalDay(left: Date, right: Date): boolean {
  return (
    left.getFullYear() === right.getFullYear() &&
    left.getMonth() === right.getMonth() &&
    left.getDate() === right.getDate()
  );
}

export function WeekStrip() {
  const { days, monthLabel } = useMemo(() => {
    const start = startOfCurrentWeek();
    const next = Array.from({ length: 7 }, (_, index) => {
      const day = new Date(start);
      day.setDate(start.getDate() + index);
      return day;
    });
    return {
      days: next,
      monthLabel: new Intl.DateTimeFormat("es", {
        month: "long",
        year: "numeric",
      }).format(start),
    };
  }, []);
  const today = new Date();
  const weekday = new Intl.DateTimeFormat("es", { weekday: "short" });

  return (
    <section
      aria-label="días de la semana actual"
      className="border-2 border-pure-black bg-surface shadow-block"
    >
      <div className="flex items-center justify-between border-b-2 border-pure-black bg-mint px-3 py-2 text-on-accent">
        <span className="capitalize font-bold">semana actual</span>
        <span className="lowercase text-xs">{monthLabel}</span>
      </div>
      <ol className="grid grid-cols-7 gap-1 p-2">
        {days.map((day) => {
          const active = sameLocalDay(day, today);
          return (
            <li
              key={day.toISOString().slice(0, 10)}
              aria-current={active ? "date" : undefined}
              className={`flex min-w-0 flex-col items-center gap-0.5 border-2 px-1 py-1.5 ${
                active
                  ? "border-mint bg-mint text-on-accent"
                  : "border-lavender/25 text-lavender/70"
              }`}
            >
              <span className="lowercase text-xs">{weekday.format(day)}</span>
              <span className="text-lg">{day.getDate()}</span>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
