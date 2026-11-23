import { useCallback, useEffect, useState } from "react";
import { fetchAudios } from "@/lib/audios";
import type { AudioListItemOut } from "@/lib/audios";
import { computeSubjectStats } from "@/lib/stats";
import type { GlobalSubjectStats } from "@/lib/stats";
import { fetchSubjects } from "@/lib/subjects";
import type { Subject } from "@/lib/subjects";
import { fetchTaskPage } from "@/lib/tasks";
import type { Task } from "@/lib/tasks";

// D4.4 — shared loader for the metadata-only study statistics, used by the dashboard
// `SubjectStats` card and by the subjects list rows. Zero audio files, URLs or playback.
export function useSubjectStats(initialSubjects?: Subject[]): {
  stats: GlobalSubjectStats;
  loading: boolean;
  reload: () => Promise<void>;
} {
  const [subjects, setSubjects] = useState<Subject[]>(initialSubjects ?? []);
  const [audios, setAudios] = useState<AudioListItemOut[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    if (initialSubjects !== undefined) setSubjects(initialSubjects);
  }, [initialSubjects]);

  const reload = useCallback(async () => {
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
    void reload();
    function onVisible() {
      if (document.visibilityState === "visible") {
        void reload();
      }
    }
    document.addEventListener("visibilitychange", onVisible);
    return () => {
      document.removeEventListener("visibilitychange", onVisible);
    };
  }, [reload]);

  return { stats: computeSubjectStats(subjects, audios, tasks), loading, reload };
}
