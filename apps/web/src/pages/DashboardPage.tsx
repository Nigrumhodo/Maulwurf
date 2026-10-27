import { useAuth } from "@/components/auth/AuthProvider";
import { Typewriter } from "@/components/motion/Typewriter";
import { ChatButton } from "@/components/chat/ChatButton";
import { StatusTicker } from "@/components/dashboard/StatusTicker";
import { UploadZone } from "@/components/dashboard/UploadZone";
import { SoundFrequency } from "@/components/dashboard/SoundFrequency";
import { AgendaPanel } from "@/components/dashboard/AgendaPanel";

// D- (Dashboard) — Vista de comando (M9): subir clase (ancho completo),
// frecuencias de audio y agenda/tareas. Esqueleto S1 con datos ficticios solo
// para maquetar el aspecto; sin llamadas reales.
export function DashboardPage() {
  const { guest } = useAuth();

  return (
    <div className="relative flex flex-col gap-6">
      <div className="crt-overlay" aria-hidden="true" />

      <header className="lowercase">
        <h1 className="text-3xl text-mint">
          <Typewriter text="bienvenido de vuelta" speed={40} />
        </h1>
        <p className="mt-1 text-lavender">&gt; agenda, pendientes y estado de ingesta</p>
      </header>

      <StatusTicker guest={guest} />

      <UploadZone />

      <SoundFrequency />

      <AgendaPanel />

      <ChatButton />
    </div>
  );
}
