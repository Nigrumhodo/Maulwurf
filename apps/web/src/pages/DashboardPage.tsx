import { Card } from "@/components/ui/Card";

// D1.2 — Dashboard esqueleto (M9). Solo estructura de navegación; el contenido
// real (agenda, pendientes, ingesta) llega en D2.x/D3.x/D4.x.
export function DashboardPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="lowercase text-2xl text-mint">panel de mando</h1>

      <div className="grid grid-cols-1 gap-6 sm:grid-cols-3">
        <Card title="próximos vencimientos">
          <p className="lowercase text-lavender">— sin tareas confirmadas</p>
        </Card>
        <Card title="para confirmar">
          <p className="lowercase text-lavender">— bandeja vacía</p>
        </Card>
        <Card title="estado de ingesta">
          <p className="lowercase text-lavender">— sin clases subidas</p>
        </Card>
      </div>
    </div>
  );
}
