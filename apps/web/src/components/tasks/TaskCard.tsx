import { TranscriptLink } from "@/components/transcript/TranscriptLink";
import { SyncStatus } from "@/components/calendar/SyncStatus";
import { Button } from "@/components/ui/Button";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { confidenceLabel, formatTaskDue } from "@/lib/tasks";
import type { Task, TaskQualityState } from "@/lib/tasks";

// D3.1 — one review card. It only presents the server's task shape: the status, date state,
// confidence and literal quote are never rebuilt from a class or from the model output.
export interface TaskCardProps {
  task: Task;
  quality: TaskQualityState | null;
  onEdit?: () => void;
  onReview?: () => void;
  onComplete?: () => void;
}

export function TaskCard({ task, quality, onEdit, onReview, onComplete }: TaskCardProps) {
  const due = formatTaskDue(task);
  const hasQualityWarning =
    quality !== null && (quality.unavailable || quality.signals.length > 0);

  return (
    <li className="lowercase flex flex-col gap-3 px-4 py-4">
      <div className="flex min-w-0 flex-col gap-1">
        <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
          <p className="min-w-0 wrap-anywhere capitalize text-lg text-lavender">{task.titulo}</p>
          <StatusBadge status={task.status} />
          <StatusBadge status={task.calendar_sync_status} />
        </div>
        <p className="text-sm text-lavender/60">
          {task.tipo}
          {task.subject !== null ? ` · ${task.subject.name}` : " · sin materia"}
          {` · ${task.source}`}
        </p>
      </div>

      <div className="flex flex-wrap items-center gap-2 text-sm text-lavender/60">
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
          <span className="text-lavender/60">({task.date_reason})</span>
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
        {hasQualityWarning && (
          <span className="border-2 border-lavender/30 px-1 text-lavender/80" title="calidad ASR: revisa la cita">
            asr ⚠
          </span>
        )}
      </div>

      <SyncStatus task={task} />

      {task.detalle !== null && task.detalle.trim() !== "" && (
        <p className="text-lavender/80">{task.detalle}</p>
      )}

      {task.evidence.length === 0 ? (
        <p className="text-lavender/60">sin cita textual; no se presenta como extracción.</p>
      ) : (
        <ul className="flex flex-col gap-2">
          {task.evidence.map((evidence) => (
            <li
              key={`${evidence.segment_id}-${evidence.char_start}-${evidence.char_end}`}
              className="flex flex-col gap-1 border-l-2 border-lavender/40 pl-3"
            >
              <blockquote className="text-base text-lavender/80">“{evidence.quote}”</blockquote>
              <TranscriptLink task={task} evidence={evidence} quality={quality} />
            </li>
          ))}
        </ul>
      )}

      {(onEdit !== undefined || onReview !== undefined || onComplete !== undefined) &&
        (task.status === "needs_review" ||
          task.status === "pending" ||
          task.status === "confirmed") && (
          <div className="flex flex-wrap items-center gap-2">
            {onReview !== undefined && (
              <Button type="button" size="sm" variant={task.status === "needs_review" ? "primary" : "secondary"}
                className="min-h-8"
                onClick={onReview}>
                revisar
              </Button>
            )}
            {onEdit !== undefined && (
              <Button type="button" size="sm" variant="ghost" className="min-h-8" onClick={onEdit}>
                editar
              </Button>
            )}
            {onComplete !== undefined && task.status === "confirmed" && (
              <Button
                type="button"
                size="sm"
                variant="secondary"
                className="ml-auto min-h-8"
                onClick={onComplete}
              >
                marcar completada
              </Button>
            )}
          </div>
        )}
    </li>
  );
}
