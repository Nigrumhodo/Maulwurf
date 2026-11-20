import { useEffect, useState } from "react";
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

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoading(true);
      const [subjectsRes, audiosRes, tasksRes] = await Promise.allSettled([
        initialSubjects !== undefined ? Promise.resolve(initialSubjects) : fetchSubjects(),
        fetchAudios(),
        fetchTaskPage({ limit: 100 }),
      ]);
      if (cancelled) return;
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
    }
    void load();
    return () => {
      cancelled = true;
    };
  }, [initialSubjects]);

  const stats = computeSubjectStats(subjects, audios, tasks);

  return (
    <Card
      title="estadísticas por materia"
      footer={
        <span className="text-xs text-lavender/70">
          solo metadatos y texto · audio original no conservado (g1)
        </span>
      }
    >
      {loading && stats.subjectCount === 0 && stats.classCount === 0 ? (
        <p className="lowercase text-lavender/60">&gt; calculando métricas de texto…</p>
      ) : (
        <div className="lowercase flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="border-2 border-pure-black bg-dark p-2">
              <p className="text-xs text-lavender/60">materias</p>
              <p className="text-2xl text-mint">{stats.subjectCount}</p>
            </div>
            <div className="border-2 border-pure-black bg-dark p-2">
              <p className="text-xs text-lavender/60">clases listas / total</p>
              <p className="text-2xl text-mint">
                {stats.readyClassCount} / {stats.classCount}
              </p>
            </div>
            <div className="border-2 border-pure-black bg-dark p-2">
              <p className="text-xs text-lavender/60">duración transcrita</p>
              <p className="text-2xl text-mint">{stats.transcribedMinutes} min</p>
            </div>
            <div className="border-2 border-pure-black bg-dark p-2">
              <p className="text-xs text-lavender/60">tareas confirmadas / por revisar</p>
              <p className="text-2xl text-mint">
                {stats.confirmedTaskCount} / {stats.pendingTaskCount}
              </p>
            </div>
          </div>

          {stats.subjects.length > 0 && (
            <ul className="flex flex-col gap-2">
              {stats.subjects.map((item) => (
                <li
                  key={item.id}
                  className="flex flex-wrap items-center justify-between gap-2 border-2 border-pure-black bg-dark px-3 py-2 text-sm text-lavender"
                >
                  <span className="inline-flex items-center gap-2">
                    <span
                      aria-hidden="true"
                      className="inline-block h-3 w-3 shrink-0"
                      style={{ backgroundColor: item.color }}
                    />
                    <span className="font-bold text-mint">{item.name}</span>
                  </span>
                  <span className="text-lavender/80">
                    {item.readyClasses}/{item.totalClasses} clases listas · {item.transcribedMinutes}{" "}
                    min · {item.confirmedTasks} confirmadas / {item.pendingTasks} pendientes
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
