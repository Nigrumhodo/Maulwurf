import {
  GOOGLE_CONNECT_PATH,
  GOOGLE_RECONNECT_PATH,
  syncStatusDescription,
} from "@/lib/integrations";
import type { Task } from "@/lib/tasks";

// D3.7 - per-task Calendar sync explanation (C-S3-DA-08). The raw status stays in the
// badge; this only adds context. Only `blocked` offers a real navigation to reconnect:
// `failed` is an external edit the user resolves in Google, so it is info-only and never
// pretends there is an automatic retry.

export interface SyncStatusProps {
  task: Task;
  showAction?: boolean;
}

export function SyncStatus({ task, showAction = true }: SyncStatusProps) {
  const description = syncStatusDescription(task.calendar_sync_status);
  const blocked = task.calendar_sync_status === "blocked";
  const href = task.calendar_id === null ? GOOGLE_CONNECT_PATH : GOOGLE_RECONNECT_PATH;
  const label =
    task.calendar_id === null ? "conectar google calendar" : "reconectar google calendar";

  if (description === null && !blocked) return null;

  return (
    <div className="lowercase flex flex-col gap-1 text-xs text-lavender/80">
      {description !== null && <p>{description}</p>}
      {showAction && blocked && (
        <a
          className="inline-flex w-fit items-center gap-1 border-2 border-lavender/40 px-2 py-0.5 text-mint underline decoration-dotted hover:border-mint hover:bg-mint hover:text-on-accent"
          href={href}
        >
          {label}
        </a>
      )}
    </div>
  );
}
