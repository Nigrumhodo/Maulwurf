import { useCallback, useEffect, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { GoogleReconnect } from "@/components/calendar/GoogleReconnect";
import { TaskCreateForm } from "@/components/tasks/TaskCreateForm";
import { TaskEditor } from "@/components/tasks/TaskEditor";
import { TaskInbox } from "@/components/tasks/TaskInbox";
import { TaskReviewDialog } from "@/components/tasks/TaskReviewDialog";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { ApiHttpError } from "@/lib/api";
import { fetchIntegrationsStatus } from "@/lib/integrations";
import type { GoogleStatus } from "@/lib/integrations";
import { fetchAudioDetail } from "@/lib/library";
import { fetchSubjects } from "@/lib/subjects";
import type { Subject } from "@/lib/subjects";
import {
  TASK_STATUSES,
  completeTask,
  fetchTask,
  fetchTaskPage,
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
        ? {
            signals: qualitySignals(result.value),
            unavailable: false,
            timestamp_precision:
              result.value.transcript?.timestamp_precision ?? null,
            duration_seconds: result.value.duration_seconds ?? null,
          }
        : {
            signals: [],
            unavailable: true,
            timestamp_precision: null,
            duration_seconds: null,
          };
  });
  return quality;
}

// D3.1 — inbox for extracted/manual proposals. It reads the frozen M4 task shape, shows
// status and date state separately, and never exposes a confirmation or Calendar action.
function parseInitialStatus(value: string | null): TaskStatusFilter {
  if (value === "all") return "all";
  if (value !== null && (TASK_STATUSES as readonly string[]).includes(value)) {
    return value as TaskStatusFilter;
  }
  return "needs_review";
}

export function TareasPage() {
  const [searchParams, setSearchParams] = useSearchParams();
  const initialEditId = searchParams.get("edit");
  const handledEditRef = useRef<string | null>(null);
  const [statusFilter, setStatusFilter] = useState<TaskStatusFilter>(() =>
    initialEditId !== null && searchParams.get("status") === null
      ? "all"
      : parseInitialStatus(searchParams.get("status")),
  );
  const [subjectFilter, setSubjectFilter] = useState("");
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [qualityByAudio, setQualityByAudio] = useState<Record<string, TaskQualityState>>({});
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [savedNotice, setSavedNotice] = useState<string | null>(null);
  const [creatingTask, setCreatingTask] = useState(false);
  const [editingTask, setEditingTask] = useState<Task | null>(null);
  const [reviewingTask, setReviewingTask] = useState<Task | null>(null);
  const [google, setGoogle] = useState<GoogleStatus | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchIntegrationsStatus()
      .then((status) => {
        if (!cancelled) setGoogle(status.google ?? null);
      })
      .catch(() => {
        if (!cancelled) setGoogle(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

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
      const page = await fetchTaskPage({
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

  useEffect(() => {
    const editId = searchParams.get("edit");
    if (editId === null || editId === "" || loading || handledEditRef.current === editId) {
      return;
    }
    handledEditRef.current = editId;
    const found = tasks.find((item) => item.id === editId);
    const nextParams = new URLSearchParams(searchParams);
    nextParams.delete("edit");
    setSearchParams(nextParams, { replace: true });
    if (found !== undefined) {
      setEditingTask(found);
      return;
    }
    void fetchTask(editId)
      .then((loaded) => {
        setTasks((current) =>
          current.some((item) => item.id === loaded.id) ? current : [loaded, ...current],
        );
        setEditingTask(loaded);
      })
      .catch(() => {
        // Ignore unknown/stale task id; inbox remains usable.
      });
  }, [loading, searchParams, setSearchParams, tasks]);

  async function loadMore() {
    if (nextCursor === null || loadingMore) return;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await fetchTaskPage({
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

  function handleCreated(created: Task, openReview: boolean) {
    setCreatingTask(false);
    setTasks((current) => {
      const matchesFilter =
        (statusFilter === "all" || created.status === statusFilter) &&
        (subjectFilter === "" || created.subject?.id === subjectFilter);
      if (!matchesFilter) return current;
      return [created, ...current.filter((item) => item.id !== created.id)];
    });
    if (openReview) {
      setSavedNotice(null);
      setReviewingTask(created);
    } else {
      setSavedNotice(
        statusFilter !== "all" && created.status !== statusFilter
          ? `tarea «${created.titulo}» creada en estado «por confirmar».`
          : `tarea «${created.titulo}» creada.`,
      );
    }
  }

  function handleSaved(update: TaskUpdate) {
    setTasks((current) =>
      current.map((item) => (item.id === update.task.id ? update.task : item)),
    );
    setEditingTask(null);
    setSavedNotice(
      update.revision_id !== null
        ? `cambio pendiente de confirmación en «${update.task.titulo}»; usa revisar para aplicarlo.`
        : `cambios guardados en «${update.task.titulo}».`,
    );
  }

  function handleReviewed(task: Task) {
    setTasks((current) =>
      current.map((item) => (item.id === task.id ? task : item)),
    );
    setReviewingTask(task);
  }

  function handleReviewActionComplete(updatedTask: Task, message: string) {
    setTasks((current) =>
      statusFilter === "all" || updatedTask.status === statusFilter
        ? current.map((item) => (item.id === updatedTask.id ? updatedTask : item))
        : current.filter((item) => item.id !== updatedTask.id),
    );
    setReviewingTask(null);
    setSavedNotice(message);
  }

  async function handleCompleteFromCard(target: Task) {
    setError(null);
    setSavedNotice(null);
    try {
      const result = await completeTask(target.id, target.version);
      setTasks((current) =>
        statusFilter === "all" || result.task.status === statusFilter
          ? current.map((item) => (item.id === result.task.id ? result.task : item))
          : current.filter((item) => item.id !== result.task.id),
      );
      setSavedNotice(
        result.calendar_event_kept
          ? `Tarea «${result.task.titulo}» marcada como completada. El evento de Calendar se conserva.`
          : `Tarea «${result.task.titulo}» marcada como completada.`,
      );
    } catch (cause) {
      setError(errorMessage(cause));
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div className="flex flex-col gap-1">
          <h1 className="capitalize text-2xl text-mint">tareas</h1>
          <p className="lowercase text-base text-lavender/80 max-w-prose">
            &gt; crea tareas propias o revisa propuestas del análisis; confirma para enviarlas a Calendar.
          </p>
        </div>
        {!creatingTask && (
          <Button
            type="button"
            variant={tasks.some((item) => item.status === "needs_review") ? "secondary" : "primary"}
            onClick={() => {
              setSavedNotice(null);
              setError(null);
              setEditingTask(null);
              setCreatingTask(true);
            }}
          >
            + nueva tarea
          </Button>
        )}
      </div>

      {google !== null && !google.calendar_write_ready && (
        <GoogleReconnect google={google} compact />
      )}

      {creatingTask && (
        <TaskCreateForm
          subjects={subjects}
          defaultSubjectId={subjectFilter}
          onCreated={handleCreated}
          onCancel={() => setCreatingTask(false)}
        />
      )}

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
          key={`edit-${editingTask.id}-${editingTask.version}`}
          task={editingTask}
          onSaved={handleSaved}
          onCancel={() => setEditingTask(null)}
        />
      )}

      {reviewingTask !== null && (
        <TaskReviewDialog
          key={`review-${reviewingTask.id}`}
          task={reviewingTask}
          onUpdated={handleReviewed}
          onActionComplete={handleReviewActionComplete}
          onEdit={(target) => {
            setReviewingTask(null);
            setSavedNotice(null);
            setEditingTask(target);
          }}
          onClose={() => setReviewingTask(null)}
        />
      )}

      {savedNotice !== null && (
        <p role="status" className="normal-case border-2 border-mint px-3 py-2 text-mint">
          &gt; {savedNotice}
        </p>
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
              onEdit={(target) => {
                setSavedNotice(null);
                setEditingTask(target);
              }}
              onReview={(target) => {
                setSavedNotice(null);
                setReviewingTask(target);
              }}
              onComplete={(target) => void handleCompleteFromCard(target)}
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
