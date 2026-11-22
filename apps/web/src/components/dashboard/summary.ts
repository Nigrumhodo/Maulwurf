import type { AudioListItemOut } from "@/lib/audios";
import type { Task } from "@/lib/tasks";

const OPEN_TASK = new Set(["pending", "needs_review", "confirmed"]);

function count(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`;
}

/** One-line status of the space, built only from the audio and task lists already loaded. */
export function statusLine(audios: AudioListItemOut[], tasks: Task[]): string {
  const ready = audios.filter((a) => a.aggregate_status === "ready").length;
  const processing = audios.filter((a) => a.aggregate_status === "processing").length;
  const review = tasks.filter((t) => t.status === "pending" || t.status === "needs_review").length;
  const parts: string[] = [];
  if (ready > 0) parts.push(count(ready, "clase lista", "clases listas"));
  if (processing > 0) parts.push(`${processing} procesando`);
  if (review > 0) parts.push(count(review, "tarea por revisar", "tareas por revisar"));
  return parts.length > 0 ? parts.join(" · ") : "aún no hay clases ni tareas";
}

function dueKey(task: Task): string {
  return task.due_at ?? task.due_date ?? "";
}

/** Open tasks, soonest due first and undated last, capped for the entry screen. */
export function upcomingTasks(tasks: Task[], limit = 3): Task[] {
  return tasks
    .filter((t) => OPEN_TASK.has(t.status))
    .sort((a, b) => {
      const left = dueKey(a);
      const right = dueKey(b);
      if (left === right) return 0;
      if (left === "") return 1;
      if (right === "") return -1;
      return left < right ? -1 : 1;
    })
    .slice(0, limit);
}
