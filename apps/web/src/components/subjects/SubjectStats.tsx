import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { useSubjectStats } from "@/components/subjects/useSubjectStats";
import type { Subject } from "@/lib/subjects";

// D4.4 — Metadata & text-only study statistics. Zero audio files, URLs, or playback.
// Renders as a section inside its view's main window, not as a window of its own.

export interface SubjectStatsProps {
  subjects?: Subject[];
}

export function SubjectStats({ subjects: initialSubjects }: SubjectStatsProps) {
  const { t } = useTranslation("plan");
  const { stats, loading, reload } = useSubjectStats(initialSubjects);

  return (
    <section className="flex flex-col gap-4">
      <h3 className="lowercase text-lg text-lavender">{t("subjectStats.title")}</h3>

      {loading && stats.subjectCount === 0 && stats.classCount === 0 ? (
        <p className="lowercase text-lavender/60">&gt; {t("subjectStats.calculating")}</p>
      ) : (
        <div className="lowercase flex flex-col gap-4">
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
            <div className="border-2 border-lavender/30 bg-dark p-2">
              <p className="text-base text-lavender/80">{t("subjectStats.subjects")}</p>
              <p className="text-2xl text-mint">{stats.subjectCount}</p>
            </div>
            <div className="border-2 border-lavender/30 bg-dark p-2">
              <p className="text-base text-lavender/80">{t("subjectStats.readyTotal")}</p>
              <p className="text-2xl text-mint">
                {stats.readyClassCount} / {stats.classCount}
              </p>
            </div>
            <div className="border-2 border-lavender/30 bg-dark p-2">
              <p className="text-base text-lavender/80">{t("subjectStats.duration")}</p>
              <p className="text-2xl text-mint">{stats.transcribedMinutes} min</p>
            </div>
            <div className="border-2 border-lavender/30 bg-dark p-2">
              <p className="text-base text-lavender/80">{t("subjectStats.tasks")}</p>
              <p className="text-2xl text-mint">
                {stats.confirmedTaskCount} / {stats.pendingTaskCount}
              </p>
              {stats.completedTaskCount > 0 && (
                <p className="text-base text-lavender/80">
                  {t("subjectStats.completed", { count: stats.completedTaskCount })}
                </p>
              )}
            </div>
          </div>

          {stats.subjects.length > 0 && (
            <ul className="flex flex-col divide-y-2 divide-lavender/20">
              {stats.subjects.map((item) => (
                <li
                  key={item.id}
                  className="flex flex-wrap items-center justify-between gap-2 py-2 text-base text-lavender"
                >
                  <span className="inline-flex items-center gap-2">
                    <span
                      aria-hidden="true"
                      className="inline-block h-3 w-3 shrink-0"
                      style={{ backgroundColor: item.color }}
                    />
                    <span className="font-bold text-mint">{item.name}</span>
                  </span>{" "}
                  <span className="text-lavender/80">
                    {t(
                      item.completedTasks > 0
                        ? "subjectStats.rowWithCompleted"
                        : "subjectStats.row",
                      {
                        ready: item.readyClasses,
                        total: item.totalClasses,
                        minutes: item.transcribedMinutes,
                        confirmed: item.confirmedTasks,
                        completed: item.completedTasks,
                        pending: item.pendingTasks,
                      },
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center justify-between gap-2 border-t-2 border-lavender/20 pt-3">
        <span className="text-base text-lavender/80">
          {t("subjectStats.privacy")}
        </span>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          disabled={loading}
          onClick={() => void reload()}
        >
          {loading ? t("subjectStats.refreshing") : t("subjectStats.refresh")}
        </Button>
      </div>
    </section>
  );
}
