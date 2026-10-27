import { Card } from "@/components/ui/Card";

// D- (Gestión) — Índice del hub de gestión. Sólo orienta; cada sección vive en
// su ruta (biblioteca, chat, materias, tareas, agenda).
export function GestionOverview() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="lowercase text-2xl text-mint">gestión</h1>

      <Card title="panel académico">
        <p className="lowercase text-lavender">
          &gt; organiza tu estudio: sube clase, revisa la biblioteca, pregunta al
          chat, gestiona materias, tareas y agenda.
        </p>
        <p className="mt-2 lowercase text-lavender/60">
          &gt; elige una sección en el menú lateral.
        </p>
      </Card>
    </div>
  );
}
