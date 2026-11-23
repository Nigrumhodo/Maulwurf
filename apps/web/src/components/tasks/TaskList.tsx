import { StatusBadge } from "@/components/ui/StatusBadge";
import { fetchTasks, taskSchedule } from "@/lib/tasks";
import type { TaskOut } from "@/lib/tasks";
import { useServerList } from "@/lib/useServerList";

function TaskRow({ task }: { task: TaskOut }) {
  return (
    <li className="lowercase flex flex-col gap-1 py-3">
      <div className="flex items-center justify-between gap-2">
        <span className="min-w-0 wrap-anywhere text-lavender">{task.titulo}</span>
        <StatusBadge status={task.status} />
      </div>
      <div className="flex flex-wrap items-center gap-2 text-base text-lavender/60">
        <span>{task.tipo}</span>
        <span>materia: {task.subject?.name ?? "sin materia"}</span>
        <span>{taskSchedule(task)}</span>
        <StatusBadge status={task.calendar_sync_status} />
      </div>
    </li>
  );
}

/** The one task list. The dashboard and `/gestion/tareas` both render it. */
export function TaskList() {
  const { items, loading, error } = useServerList(fetchTasks);

  if (loading) {
    return <p className="lowercase text-lavender/60">&gt; cargando…</p>;
  }
  if (error !== null) {
    return (
      <p role="alert" className="lowercase text-mint">
        &gt; {error}
      </p>
    );
  }
  if (items.length === 0) {
    return <p className="lowercase text-lavender">aún no hay tareas</p>;
  }
  return (
    <ul className="flex flex-col divide-y-2 divide-lavender/20">
      {items.map((task) => (
        <TaskRow key={task.id} task={task} />
      ))}
    </ul>
  );
}
