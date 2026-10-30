import { Card } from "@/components/ui/Card";

// D3.6 (esqueleto) — Agenda: vista de calendario con eventos de Google y tareas
// Maulwurf coloreadas por materia, sin dibujar dos veces la tarea y su evento.
export function AgendaPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="lowercase text-2xl text-mint">agenda</h1>

      <Card title="calendario">
        <p className="lowercase text-lavender">aún no hay eventos sincronizados.</p>
        <p className="lowercase text-lavender">la agenda con google calendar llega en d3.x.</p>
      </Card>
    </div>
  );
}
