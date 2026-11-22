import { StatusTicker } from "@/components/dashboard/StatusTicker";
import { TelemetryBanner } from "@/components/dashboard/TelemetryBanner";
import { WeekStrip } from "@/components/dashboard/WeekStrip";
import { TapeDeck } from "@/components/dashboard/TapeDeck";
import { ActivityFeed } from "@/components/dashboard/ActivityFeed";
import { TaskLog } from "@/components/dashboard/TaskLog";
import { MoleCompanion } from "@/components/dashboard/MoleCompanion";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

// Command center. Classes and tasks come from the same endpoints as the gestión pages.
// Chat lives only at `/gestion/chat`.
export function DashboardPage() {
  useDocumentTitle("inicio");
  return (
    <div className="relative flex flex-col gap-4">
      <div className="crt-overlay" aria-hidden="true" />

      <TelemetryBanner />

      <main className="grid grid-cols-1 items-start gap-4 lg:grid-cols-12">
        <section className="lg:col-span-12">
          <WeekStrip />
        </section>

        <section className="flex flex-col gap-4 lg:col-span-4">
          <TapeDeck />
        </section>

        <section className="flex flex-col gap-4 lg:col-span-5">
          <ActivityFeed />
        </section>

        <section className="flex flex-col gap-4 lg:col-span-3">
          <TaskLog />
          <MoleCompanion />
        </section>
      </main>

      <footer className="flex flex-col gap-2">
        <StatusTicker />
        <div className="lowercase flex flex-wrap items-center justify-between gap-2 border-2 border-pure-black bg-surface px-3 py-1.5 text-lavender/60">
          <span>audio no conservado · cifrado aes-gcm · solo texto persistido</span>
          <span>maulwurf@lab © 2026</span>
        </div>
      </footer>
    </div>
  );
}
