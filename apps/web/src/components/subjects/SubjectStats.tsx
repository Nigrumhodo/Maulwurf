import { Button } from "@/components/ui/Button";
import { useSubjectStats } from "@/components/subjects/useSubjectStats";
import type { Subject } from "@/lib/subjects";

// D4.4 — Metadata & text-only study statistics. Zero audio files, URLs, or playback.
// Renders as a section inside its view's main window, not as a window of its own.

export interface SubjectStatsProps {
  subjects?: Subject[];
}

export function SubjectStats({ subjects: initialSubjects }: SubjectStatsProps) {
  const { stats, loading, reload } = useSubjectStats(initialSubjects);

  return (
    <section className="flex flex-col gap-4">
      <h3 className="lowercase text-lg text-lavender">estadísticas por materia</h3>

      {loading && stats.subjectCount === 0 && stats.classCount === 0 ? (
        <p className="lowercase text-lavender/60">&gt; calculando métricas de texto…</p>
      ) : (
        <div className="lowercase flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="border-2 border-lavender/30 bg-dark p-2">
              <p className="text-base text-lavender/80">materias</p>
              <p className="text-2xl text-mint">{stats.subjectCount}</p>
            </div>
            <div className="border-2 border-lavender/30 bg-dark p-2">
              <p className="text-base text-lavender/80">clases listas / total</p>
              <p className="text-2xl text-mint">
                {stats.readyClassCount} / {stats.classCount}
              </p>
            </div>
            <div className="border-2 border-lavender/30 bg-dark p-2">
              <p className="text-base text-lavender/80">duración transcrita</p>
              <p className="text-2xl text-mint">{stats.transcribedMinutes} min</p>
            </div>
            <div className="border-2 border-lavender/30 bg-dark p-2">
              <p className="text-base text-lavender/80">tareas confirmadas / por revisar</p>
              <p className="text-2xl text-mint">
                {stats.confirmedTaskCount} / {stats.pendingTaskCount}
              </p>
              {stats.completedTaskCount > 0 && (
                <p className="text-base text-lavender/80">
                  {stats.completedTaskCount} completada(s)
                </p>
              )}
            </div>
          </div>

          {stats.subjects.length > 0 && (
            <ul className="flex flex-col divide-y-2 divide-lavender/20">
              {stats.subjects.map((item) => (
                <li
                  key={item.id}
                  className="flex flex-wrap items-center justify-between gap-2 py-2 text-base text-lavender"
                >
                  <span className="inline-flex items-center gap-2">
                    <span
                      aria-hidden="true"
                      className="inline-block h-3 w-3 shrink-0"
                      style={{ backgroundColor: item.color }}
                    />
                    <span className="font-bold text-mint">{item.name}</span>
                  </span>{" "}
                  <span className="text-lavender/80">
                    {item.readyClasses}/{item.totalClasses} clases listas · {item.transcribedMinutes}{" "}
                    min · {item.confirmedTasks} confirmadas
                    {item.completedTasks > 0 ? ` · ${item.completedTasks} completadas` : ""} /{" "}
                    {item.pendingTasks} pendientes
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 border-t-2 border-lavender/20 pt-3">
        <span className="text-base text-lavender/80">
          solo metadatos y texto · audio original no conservado (g1)
        </span>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={loading}
          onClick={() => void reload()}
        >
          {loading ? "actualizando…" : "actualizar"}
        </Button>
      </div>
    </section>
  );
}
