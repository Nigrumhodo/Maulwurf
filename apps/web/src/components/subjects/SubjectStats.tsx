import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { fetchAudios } from "@/lib/audios";
import type { AudioListItemOut } from "@/lib/audios";
import { computeSubjectStats } from "@/lib/stats";
import { fetchSubjects } from "@/lib/subjects";
import type { Subject } from "@/lib/subjects";
import { fetchTaskPage } from "@/lib/tasks";
import type { Task } from "@/lib/tasks";

// D4.4 — Metadata & text-only study statistics. Zero audio files, URLs, or playback.

export interface SubjectStatsProps {
  subjects?: Subject[];
}

export function SubjectStats({ subjects: initialSubjects }: SubjectStatsProps) {
  const [subjects, setSubjects] = useState<Subject[]>(initialSubjects ?? []);
  const [audios, setAudios] = useState<AudioListItemOut[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (initialSubjects !== undefined) setSubjects(initialSubjects);
  }, [initialSubjects]);

  const load = useCallback(async () => {
    setLoading(true);
    const [subjectsRes, audiosRes, tasksRes] = await Promise.allSettled([
      initialSubjects !== undefined ? Promise.resolve(initialSubjects) : fetchSubjects(),
      fetchAudios(),
      fetchTaskPage({ status: "all", limit: 200 }),
    ]);
    if (subjectsRes.status === "fulfilled" && Array.isArray(subjectsRes.value)) {
      setSubjects(subjectsRes.value);
    }
    if (audiosRes.status === "fulfilled" && Array.isArray(audiosRes.value)) {
      setAudios(audiosRes.value);
    }
    if (tasksRes.status === "fulfilled" && Array.isArray(tasksRes.value?.items)) {
      setTasks(tasksRes.value.items);
    }
    setLoading(false);
  }, [initialSubjects]);

  useEffect(() => {
    void load();
    function onVisible() {
      if (document.visibilityState === "visible") {
        void load();
      }
    }
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [load]);

  const stats = computeSubjectStats(subjects, audios, tasks);

  return (
    <Card
      title="estadísticas por materia"
      footer={
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-sm text-lavender/80">
            solo metadatos y texto · audio original no conservado (g1)
          </span>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={loading}
            onClick={() => void load()}
          >
            {loading ? "actualizando…" : "actualizar"}
          </Button>
        </div>
      }
    >
      {loading && stats.subjectCount === 0 && stats.classCount === 0 ? (
        <p className="lowercase text-lavender/60">&gt; calculando métricas de texto…</p>
      ) : (
        <div className="lowercase flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="border-2 border-lavender/30 bg-dark p-2">
              <p className="text-sm text-lavender/80">materias</p>
              <p className="text-2xl text-mint">{stats.subjectCount}</p>
            </div>
            <div className="border-2 border-lavender/30 bg-dark p-2">
              <p className="text-sm text-lavender/80">clases listas / total</p>
              <p className="text-2xl text-mint">
                {stats.readyClassCount} / {stats.classCount}
              </p>
            </div>
            <div className="border-2 border-lavender/30 bg-dark p-2">
              <p className="text-sm text-lavender/80">duración transcrita</p>
              <p className="text-2xl text-mint">{stats.transcribedMinutes} min</p>
            </div>
            <div className="border-2 border-lavender/30 bg-dark p-2">
              <p className="text-sm text-lavender/80">tareas confirmadas / por revisar</p>
              <p className="text-2xl text-mint">
                {stats.confirmedTaskCount} / {stats.pendingTaskCount}
              </p>
              {stats.completedTaskCount > 0 && (
                <p className="text-sm text-lavender/80">
                  {stats.completedTaskCount} completada(s)
                </p>
              )}
            </div>
          </div>

          {stats.subjects.length > 0 && (
            <ul className="flex flex-col gap-2">
              {stats.subjects.map((item) => (
                <li
                  key={item.id}
                  className="flex flex-wrap items-center justify-between gap-2 border-2 border-lavender/30 bg-dark px-3 py-2 text-sm text-lavender"
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
    </Card>
  );
}
