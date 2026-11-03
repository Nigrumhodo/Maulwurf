import { useCallback, useEffect, useState } from "react";
import { TaskEditor } from "@/components/tasks/TaskEditor";
import { TaskInbox } from "@/components/tasks/TaskInbox";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ApiHttpError } from "@/lib/api";
import { fetchAudioDetail } from "@/lib/library";
import { fetchSubjects } from "@/lib/subjects";
import type { Subject } from "@/lib/subjects";
import {
  fetchTasks,
  qualitySignals,
  taskStatusFilters,
} from "@/lib/tasks";
import type {
  Task,
  TaskQualityState,
  TaskStatusFilter,
  TaskUpdate,
} from "@/lib/tasks";

const TASK_PAGE_SIZE = 20;

function errorMessage(error: unknown): string {
  return error instanceof ApiHttpError
    ? error.message
    : "no se pudieron cargar las tareas; revisa tu conexión.";
}

async function loadQuality(tasks: Task[]): Promise<Record<string, TaskQualityState>> {
  const audioIds = [
    ...new Set(
      tasks
        .map((task) => task.audio_id)
        .filter((audioId): audioId is string => audioId !== null),
    ),
  ];
  const results = await Promise.allSettled(audioIds.map((audioId) => fetchAudioDetail(audioId)));
  const quality: Record<string, TaskQualityState> = {};
  audioIds.forEach((audioId, index) => {
    const result = results[index];
    quality[audioId] =
      result?.status === "fulfilled"
        ? { signals: qualitySignals(result.value), unavailable: false }
        : { signals: [], unavailable: true };
  });
  return quality;
}

// D3.1 — inbox for extracted/manual proposals. It reads the frozen M4 task shape, shows
// status and date state separately, and never exposes a confirmation or Calendar action.
export function TareasPage() {
  const [statusFilter, setStatusFilter] = useState<TaskStatusFilter>("needs_review");
  const [subjectFilter, setSubjectFilter] = useState("");
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [qualityByAudio, setQualityByAudio] = useState<Record<string, TaskQualityState>>({});
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [editingTask, setEditingTask] = useState<Task | null>(null);

  useEffect(() => {
    let cancelled = false;
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
  }, []);

  const loadFirst = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const page = await fetchTasks({
        status: statusFilter,
        subjectId: subjectFilter,
        limit: TASK_PAGE_SIZE,
      });
      const quality = await loadQuality(page.items);
      setTasks(page.items);
      setQualityByAudio(quality);
      setNextCursor(page.next_cursor);
    } catch (cause) {
      setError(errorMessage(cause));
      setTasks([]);
      setQualityByAudio({});
      setNextCursor(null);
    } finally {
      setLoading(false);
    }
  }, [statusFilter, subjectFilter]);

  useEffect(() => {
    void loadFirst();
  }, [loadFirst]);

  async function loadMore() {
    if (nextCursor === null || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await fetchTasks({
        status: statusFilter,
        subjectId: subjectFilter,
        limit: TASK_PAGE_SIZE,
        cursor: nextCursor,
      });
      const quality = await loadQuality(page.items);
      setTasks((current) => [...current, ...page.items]);
      setQualityByAudio((current) => ({ ...current, ...quality }));
      setNextCursor(page.next_cursor);
    } catch (cause) {
      setError(errorMessage(cause));
    } finally {
      setLoadingMore(false);
    }
  }

  const filters = taskStatusFilters();
  const empty =
    !loading &&
    tasks.length === 0 &&
    error === null;

  function handleSaved(update: TaskUpdate) {
    setTasks((current) =>
      current.map((item) => (item.id === update.task.id ? update.task : item)),
    );
    setEditingTask(update.task);
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="capitalize text-2xl text-mint">tareas</h1>
        <p className="lowercase text-lavender/60">
          &gt; propuestas del análisis para revisar; ninguna se escribe en Calendar desde aquí.
        </p>
      </div>

      <Card title="filtros">
        <div className="lowercase flex flex-wrap items-end gap-3">
          <label className="flex flex-col gap-1">
            <span className="capitalize text-mint">estado</span>
            <select
              aria-label="estado de la tarea"
              value={statusFilter}
              onChange={(event) => {
                setStatusFilter(event.target.value as TaskStatusFilter);
              }}
              className="capitalize bg-surface text-lavender border-2 border-lavender px-3 py-2"
            >
              {filters.map((filter) => (
                <option key={filter.value} value={filter.value}>
                  {filter.label}
                </option>
              ))}
            </select>
          </label>
          <label className="flex flex-col gap-1">
            <span className="capitalize text-mint">materia</span>
            <select
              aria-label="materia de la tarea"
              value={subjectFilter}
              onChange={(event) => {
                setSubjectFilter(event.target.value);
              }}
              className="capitalize bg-surface text-lavender border-2 border-lavender px-3 py-2"
            >
              <option value="">todas las materias</option>
              {subjects.map((subject) => (
                <option key={subject.id} value={subject.id}>
                  {subject.name}
                </option>
              ))}
            </select>
          </label>
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled={loading}
            onClick={() => void loadFirst()}
          >
            actualizar
          </Button>
        </div>
      </Card>

      {editingTask !== null && (
        <TaskEditor
          key={editingTask.id}
          task={editingTask}
          onSaved={handleSaved}
          onCancel={() => setEditingTask(null)}
        />
      )}

      {error !== null && (
        <p role="alert" className="lowercase border-2 border-mint px-3 py-2 text-mint">
          &gt; {error}
        </p>
      )}

      {loading ? (
        <p className="lowercase text-lavender/60">&gt; cargando tareas…</p>
      ) : empty ? (
        <Card title="para confirmar">
          <EmptyState
            title="no hay tareas en este estado"
            description="cuando un análisis proponga una tarea, aparecerá aquí con su cita."
          />
        </Card>
      ) : (
        <Card title="propuestas">
          <div aria-busy={loading} className="flex flex-col gap-4">
            <TaskInbox
              tasks={tasks}
              qualityByAudio={qualityByAudio}
              onEdit={setEditingTask}
            />
            {nextCursor !== null && (
              <Button
                type="button"
                size="sm"
                variant="secondary"
                disabled={loadingMore}
                onClick={() => void loadMore()}
              >
                {loadingMore ? "cargando…" : "cargar más"}
              </Button>
            )}
          </div>
        </Card>
      )}
    </div>
  );
}
