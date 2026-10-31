import { Card } from "@/components/ui/Card";

// D3.1 (esqueleto) — Bandeja «para confirmar»: tareas propuestas por análisis
// de clase, pendientes de confirmación humana antes de escribir en Calendar.
export function TareasPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="lowercase text-2xl text-mint">tareas</h1>

      <Card title="para confirmar">
        <p className="lowercase text-lavender">aún no hay tareas propuestas.</p>
        <p className="lowercase text-lavender">el análisis de clases llega en d3.x.</p>
      </Card>
    </div>
  );
}
