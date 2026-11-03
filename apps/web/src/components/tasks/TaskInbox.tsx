import { TaskCard } from "@/components/tasks/TaskCard";
import type { Task, TaskQualityState } from "@/lib/tasks";

// D3.1 — presentational inbox list. Fetching, filters and pagination stay in the page so
// the inbox cannot silently retry a read or invent a status.
export interface TaskInboxProps {
  tasks: Task[];
  qualityByAudio: Record<string, TaskQualityState>;
}

export function TaskInbox({ tasks, qualityByAudio }: TaskInboxProps) {
  return (
    <ul aria-label="tareas para revisar" className="flex flex-col gap-3">
      {tasks.map((task) => (
        <TaskCard
          key={task.id}
          task={task}
          quality={task.audio_id === null ? null : (qualityByAudio[task.audio_id] ?? null)}
        />
      ))}
    </ul>
  );
}
