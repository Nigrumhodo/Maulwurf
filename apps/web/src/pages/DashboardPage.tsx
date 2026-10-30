import { useAuth } from "@/components/auth/AuthProvider";
import { ChatButton } from "@/components/chat/ChatButton";
import { StatusTicker } from "@/components/dashboard/StatusTicker";
import { TelemetryBanner } from "@/components/dashboard/TelemetryBanner";
import { TapeDeck } from "@/components/dashboard/TapeDeck";
import { ActivityFeed } from "@/components/dashboard/ActivityFeed";
import { TaskLog } from "@/components/dashboard/TaskLog";
import { MoleCompanion } from "@/components/dashboard/MoleCompanion";

// D- (Dashboard / M9) — command center layout: telemetry strip on top, then a
// three-column grid (ingest deck · activity radar · tasks + mole companion) and a
// footer status marquee. Static mock data for now; real wiring lands in D2.x/D3.x.
export function DashboardPage() {
  const { guest } = useAuth();

  return (
    <div className="relative flex flex-col gap-4">
      <div className="crt-overlay" aria-hidden="true" />

      <TelemetryBanner guest={guest} />

      <main className="grid grid-cols-1 items-start gap-4 lg:grid-cols-12">
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
        <StatusTicker guest={guest} />
        <div className="lowercase flex flex-wrap items-center justify-between gap-2 border-2 border-pure-black bg-surface px-3 py-1.5 text-lavender/60">
          <span>audio no conservado · cifrado aes-gcm · solo texto persistido</span>
          <span>maulwurf@lab © 2026</span>
        </div>
      </footer>

      <ChatButton />
    </div>
  );
}
