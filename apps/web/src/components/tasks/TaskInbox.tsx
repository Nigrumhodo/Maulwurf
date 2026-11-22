import { TaskCard } from "@/components/tasks/TaskCard";
import { qualitySummary } from "@/lib/tasks";
import type { Task, TaskQualityState } from "@/lib/tasks";

// D3.1 — presentational inbox list. Fetching, filters and pagination stay in the page so
// the inbox cannot silently retry a read or invent a status.
export interface TaskInboxProps {
  tasks: Task[];
  qualityByAudio: Record<string, TaskQualityState>;
  onEdit?: (task: Task) => void;
  onReview?: (task: Task) => void;
  onComplete?: (task: Task) => void;
}

export function TaskInbox({
  tasks,
  qualityByAudio,
  onEdit,
  onReview,
  onComplete,
}: TaskInboxProps) {
  const qualities = tasks.flatMap((task) => {
    const state = task.audio_id === null ? undefined : qualityByAudio[task.audio_id];
    return state === undefined ? [] : [state];
  });
  const anyUnavailable = qualities.some((state) => state.unavailable);
  const signals = [
    ...new Set(
      qualities.flatMap((state) => state.signals.map((signal) => qualitySummary(signal))),
    ),
  ];

  return (
    <div className="flex flex-col">
      {(anyUnavailable || signals.length > 0) && (
        <div
          role="status"
          className="capitalize border-b-2 border-lavender/20 px-4 py-2 text-base text-mint"
        >
          {signals.length > 0 && <p>calidad ASR baja: {signals.join(" · ")}</p>}
          {anyUnavailable && (
            <p>calidad ASR no disponible en algunas clases; revisa la cita antes de decidir.</p>
          )}
        </div>
      )}
      <ul aria-label="tareas para revisar" className="flex flex-col divide-y-2 divide-lavender/20">
        {tasks.map((task) => (
          <TaskCard
            key={task.id}
            task={task}
            quality={task.audio_id === null ? null : (qualityByAudio[task.audio_id] ?? null)}
            onEdit={onEdit === undefined ? undefined : () => onEdit(task)}
            onReview={onReview === undefined ? undefined : () => onReview(task)}
            onComplete={onComplete === undefined ? undefined : () => onComplete(task)}
          />
        ))}
      </ul>
    </div>
  );
}
