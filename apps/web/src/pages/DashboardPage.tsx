import { useTranslation } from "react-i18next";
import { AccessTiles } from "@/components/dashboard/AccessTiles";
import { MoleCompanion } from "@/components/dashboard/MoleCompanion";
import { statusLine, upcomingTasks } from "@/components/dashboard/summary";
import { UpcomingTasks } from "@/components/dashboard/UpcomingTasks";
import { useAuth } from "@/components/auth/AuthProvider";
import { GridSprite } from "@/components/motion/GridSprite";
import { spriteEscribiendoCrt, spriteEscuchandoNotas } from "@/components/Visual";
import { fetchAudios } from "@/lib/audios";
import { fetchTasks } from "@/lib/tasks";
import { useDocumentTitle } from "@/lib/useDocumentTitle";
import { useServerList } from "@/lib/useServerList";

function greetingName(user: { display_name: string | null; email: string } | null): string {
  const display = user?.display_name?.trim();
  if (display) return display;
  return user?.email.split("@")[0] ?? "";
}

// Inicio: a calm entry screen. Detail (stats, week, activity, task log) lives in `/gestion`.
// Classes and tasks come from the same endpoints as the gestión pages. Chat lives only at
// `/gestion/chat`.
export function DashboardPage() {
  const { t } = useTranslation("plan");
  useDocumentTitle(t("dashboard.title"));
  const { user } = useAuth();
  const audios = useServerList(fetchAudios);
  const tasks = useServerList(fetchTasks);
  const name = greetingName(user);
  const summary =
    audios.loading || tasks.loading ? t("dashboard.loading") : statusLine(audios.items, tasks.items);

  return (
    <div className="relative">
      <div className="crt-overlay" aria-hidden="true" />
      <div className="relative flex flex-col gap-6 border-2 border-pure-black bg-surface p-6 shadow-block lg:flex-row lg:items-start">
        <div className="flex min-w-0 flex-1 flex-col gap-6">
          <header className="flex items-center gap-3">
            <div
              aria-hidden="true"
              className="hidden shrink-0 border-2 border-pure-black bg-dark p-1 sm:block"
            >
              <span className="img-dark-only">
                <GridSprite
                  src={spriteEscribiendoCrt}
                  sheetWidth={988}
                  sheetHeight={192}
                  columns={4}
                  rows={1}
                  height={40}
                  fps={4}
                />
              </span>
              <span className="img-light-only">
                <GridSprite
                  src={spriteEscuchandoNotas}
                  sheetWidth={636}
                  sheetHeight={224}
                  columns={4}
                  rows={1}
                  height={40}
                  fps={4}
                />
              </span>
            </div>
            <div className="flex flex-col gap-1">
              <h1 className="capitalize text-2xl text-mint">{name ? t("dashboard.greetingNamed", { name }) : t("dashboard.greeting")}</h1>
              <p className="lowercase max-w-prose text-base text-lavender/80" aria-live="polite">
                {summary}
              </p>
            </div>
          </header>

          <AccessTiles />

          <section aria-labelledby="inicio-siguiente" className="flex flex-col gap-3">
            <h2 id="inicio-siguiente" className="lowercase text-xl text-mint">
              {t("dashboard.next")}
            </h2>
            <UpcomingTasks
              tasks={upcomingTasks(tasks.items)}
              loading={tasks.loading}
              error={tasks.error}
            />
          </section>
        </div>

        <div className="hidden w-56 shrink-0 lg:block">
          <MoleCompanion />
        </div>
      </div>
    </div>
  );
}
