import { Link } from "react-router-dom";
import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  confidenceLabel,
  formatTaskDue,
  qualitySummary,
  taskEvidenceHref,
} from "@/lib/tasks";
import type { Task, TaskQualityState } from "@/lib/tasks";

// D3.1 — one review card. It only presents the server's task shape: the status, date state,
// confidence and literal quote are never rebuilt from a class or from the model output.
export interface TaskCardProps {
  task: Task;
  quality: TaskQualityState | null;
}

export function TaskCard({ task, quality }: TaskCardProps) {
  const due = formatTaskDue(task);
  const hasQualityWarning =
    quality !== null && (quality.unavailable || quality.signals.length > 0);

  return (
    <li className="lowercase flex flex-col gap-3 border-2 border-pure-black bg-surface px-3 py-3">
      <div className="flex flex-wrap items-start justify-between gap-2">
        <div className="min-w-0">
          <p className="capitalize text-lg text-lavender">{task.titulo}</p>
          <p className="text-xs text-lavender/60">
            {task.tipo}
            {task.subject !== null ? ` · ${task.subject.name}` : " · sin materia"}
            {` · ${task.source}`}
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-1">
          <StatusBadge status={task.status} />
          <StatusBadge status={task.calendar_sync_status} />
        </div>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-xs text-lavender/70">
        <span>fecha</span>
        <StatusBadge status={task.date_status} />
        {due !== null ? (
          <time dateTime={task.due_date ?? task.due_at ?? undefined}>{due}</time>
        ) : (
          <span>
            {task.date_text !== null && task.date_text.trim() !== ""
              ? task.date_text
              : "fecha no resuelta"}
          </span>
        )}
        {task.date_reason !== null && task.date_reason.trim() !== "" && (
          <span className="text-lavender/50">({task.date_reason})</span>
        )}
        <span
          className="text-lavender/60"
          title="señal no calibrada; no autoriza confirmar ni programar automáticamente"
        >
          {confidenceLabel(task.confidence_score)}
        </span>
        {task.pending_revision !== null && (
          <span className="border-2 border-mint px-1 text-mint">cambio pendiente</span>
        )}
      </div>

      {hasQualityWarning && (
        <div role="status" className="capitalize border-2 border-mint px-2 py-1 text-mint">
          {quality?.unavailable ? (
            <span>calidad ASR no disponible; revisa la cita antes de decidir.</span>
          ) : (
            <span>
              calidad ASR baja:{" "}
              {quality?.signals.map((signal) => qualitySummary(signal)).join(" · ")}
            </span>
          )}
        </div>
      )}

      {task.detalle !== null && task.detalle.trim() !== "" && (
        <p className="text-lavender/80">{task.detalle}</p>
      )}

      {task.evidence.length === 0 ? (
        <p className="text-lavender/60">sin cita textual; no se presenta como extracción.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {task.evidence.map((evidence) => {
            const href = taskEvidenceHref(task, evidence);
            return (
              <li
                key={`${evidence.segment_id}-${evidence.char_start}-${evidence.char_end}`}
                className="flex flex-col gap-1 border-l-2 border-lavender/40 pl-3"
              >
                <blockquote className="text-lavender/80">“{evidence.quote}”</blockquote>
                {href !== null && (
                  <Link
                    to={href}
                    className="capitalize text-xs text-mint underline decoration-dotted hover:text-on-accent hover:bg-mint"
                  >
                    ver cita en la clase
                  </Link>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </li>
  );
}
