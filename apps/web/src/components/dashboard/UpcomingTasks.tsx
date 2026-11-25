import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { buttonClasses } from "@/components/ui/buttonStyles";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { formatTaskDue } from "@/lib/tasks";
import type { Task } from "@/lib/tasks";

export function UpcomingTasks({
  tasks,
  loading,
  error,
}: {
  tasks: Task[];
  loading: boolean;
  error: string | null;
}) {
  const { t } = useTranslation("plan");
  return (
    <div className="flex flex-col gap-3">
      {loading ? (
        <p className="lowercase text-base text-lavender/60">&gt; {t("upcoming.loading")}</p>
      ) : error !== null ? (
        <p role="alert" className="lowercase text-base text-mint">
          &gt; {error}
        </p>
      ) : tasks.length === 0 ? (
        <p className="lowercase text-base text-lavender/80">{t("upcoming.empty")}</p>
      ) : (
        <ul className="divide-y-2 divide-lavender/20 border-y-2 border-lavender/20">
          {tasks.map((task) => (
            <li key={task.id} className="flex flex-wrap items-center justify-between gap-2 py-2">
              <span className="lowercase flex flex-col">
                <span className="text-lg text-lavender">{task.titulo}</span>
                <span className="text-base text-lavender/60">
                  {task.subject?.name ?? t("upcoming.noSubject")} · {formatTaskDue(task) ?? t("upcoming.noDate")}
                </span>
              </span>
              <StatusBadge status={task.status} />
            </li>
          ))}
        </ul>
      )}
      <Link
        to="/gestion/tareas"
        className={buttonClasses("ghost", "sm", "min-h-10 self-start border-lavender/30")}
      >
        {t("upcoming.viewAll")}
      </Link>
    </div>
  );
}
