import { Link } from "react-router-dom";
import { TranscriptLink } from "@/components/transcript/TranscriptLink";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { formatTaskDue } from "@/lib/tasks";
import type { Task, TaskQualityState } from "@/lib/tasks";

// D3.5 - detected items for one class. Read-only: review/edit stays in the inbox, while
// every item keeps its literal evidence link and contract status.

const ACTIVE_STATUSES = new Set(["pending", "needs_review", "confirmed"]);

export interface ClassItemsProps {
  tasks: Task[];
  quality: TaskQualityState | null;
  loading: boolean;
  error: string | null;
  onRefresh: () => void;
}

function ordered(tasks: Task[]): Task[] {
  return [...tasks].sort((left, right) => {
    const leftActive = ACTIVE_STATUSES.has(left.status) ? 0 : 1;
    const rightActive = ACTIVE_STATUSES.has(right.status) ? 0 : 1;
    return leftActive - rightActive;
  });
}

export function ClassItems({
  tasks,
  quality,
  loading,
  error,
  onRefresh,
}: ClassItemsProps) {
  const items = ordered(Array.isArray(tasks) ? tasks : []);

  return (
    <Card title="elementos detectados">
      <div className="lowercase flex flex-col gap-3">
        {loading ? (
          <p className="text-lavender/60">&gt; cargando elementos detectadosÃ¢â‚¬Â¦</p>
        ) : error !== null ? (
          <div className="flex flex-wrap items-center gap-2">
            <p role="alert" className="text-mint">
              &gt; {error}
            </p>
            <Button type="button" size="sm" variant="secondary" onClick={onRefresh}>
              reintentar
            </Button>
          </div>
        ) : items.length === 0 ? (
          <p className="text-lavender/60">
            &gt; no se detectaron tareas en esta clase.
          </p>
        ) : (
          <ul aria-label="elementos detectados" className="flex flex-col gap-2">
            {items.map((task) => {
              const due = formatTaskDue(task);
              const evidence = task.evidence[0];
              return (
                <li
                  key={task.id}
                  className="flex flex-col gap-1 border-2 border-lavender/25 px-3 py-2"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="normal-case text-lavender">{task.titulo}</span>
                    <StatusBadge status={task.status} />
                    <span className="text-xs text-lavender/60">{task.tipo}</span>
                    {due !== null && (
                      <time className="text-xs text-lavender/60" dateTime={task.due_date ?? task.due_at ?? undefined}>
                        {due}
                      </time>
                    )}
                  </div>
                  {evidence !== undefined && (
                    <TranscriptLink task={task} evidence={evidence} quality={quality} />
                  )}
                </li>
              );
            })}
          </ul>
        )}

        <Link to="/gestion/tareas" className="text-xs text-mint underline decoration-dotted">
          abrir la bandeja de tareas
        </Link>

        {items.some((task) => !ACTIVE_STATUSES.has(task.status)) && (
          <p className="text-xs text-lavender/50">
            {items.filter((task) => !ACTIVE_STATUSES.has(task.status)).length} elemento(s)
            cerrado(s) al final.
          </p>
        )}
      </div>
    </Card>
  );
}


