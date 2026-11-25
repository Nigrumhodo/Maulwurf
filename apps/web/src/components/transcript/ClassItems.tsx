import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { TranscriptLink } from "@/components/transcript/TranscriptLink";
import { Button } from "@/components/ui/Button";
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
  const { t } = useTranslation("study");
  const items = ordered(Array.isArray(tasks) ? tasks : []);

  return (
    <section aria-label={t("classItems.title")} className="lowercase py-5 first:pt-0 last:pb-0">
      <div className="flex flex-col gap-3">
        <h2 className="capitalize text-xl text-mint">{t("classItems.title")}</h2>
        {loading ? (
          <p className="text-base text-lavender/60">{t("classItems.loading")}</p>
        ) : error !== null ? (
          <div className="flex flex-wrap items-center gap-2">
            <p role="alert" className="text-mint">
              &gt; {error}
            </p>
            <Button type="button" size="sm" variant="secondary" onClick={onRefresh}>
              {t("classItems.retry")}
            </Button>
          </div>
        ) : items.length === 0 ? (
          <p className="text-base text-lavender/60">
            {t("classItems.empty")}
          </p>
        ) : (
          <ul
            aria-label={t("classItems.title")}
            className="flex flex-col divide-y-2 divide-lavender/20"
          >
            {items.map((task) => {
              const due = formatTaskDue(task);
              const evidence = task.evidence[0];
              return (
                <li
                  key={task.id}
                  className="flex flex-col gap-1 py-3 first:pt-0 last:pb-0"
                >
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="normal-case text-lavender">{task.titulo}</span>
                    <StatusBadge status={task.status} />
                    <span className="text-sm text-lavender/60">{task.tipo}</span>
                    {due !== null && (
                      <time className="text-sm text-lavender/60" dateTime={task.due_date ?? task.due_at ?? undefined}>
                        {due}
                      </time>
                    )}
                  </div>
                  <div className="flex flex-wrap items-center gap-2">
                    {evidence !== undefined && (
                      <TranscriptLink task={task} evidence={evidence} quality={quality} />
                    )}
                    {ACTIVE_STATUSES.has(task.status) && (
                      <Link
                        to={`/gestion/tareas?status=all&edit=${encodeURIComponent(task.id)}`}
                        aria-label={t("classItems.editAria", { title: task.titulo })}
                        className="inline-flex min-h-8 items-center border-2 border-lavender/40 px-2 py-1.5 text-sm text-lavender hover:border-mint hover:text-mint"
                      >
                        {t("classItems.edit")}
                      </Link>
                    )}
                  </div>
                </li>
              );
            })}
          </ul>
        )}

        <Link to="/gestion/tareas" className="inline-block py-2 text-sm text-mint underline decoration-dotted">
          {t("classItems.openInbox")}
        </Link>

        {items.some((task) => !ACTIVE_STATUSES.has(task.status)) && (
          <p className="text-sm text-lavender/60">
            {t("classItems.closedAtEnd", {
              count: items.filter((task) => !ACTIVE_STATUSES.has(task.status)).length,
            })}
          </p>
        )}
      </div>
    </section>
  );
}


