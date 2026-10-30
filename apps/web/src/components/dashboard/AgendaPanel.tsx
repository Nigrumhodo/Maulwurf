import { Card } from "@/components/ui/Card";

// D- (Dashboard) — Agenda/tareas (maqueta). Datos ficticios solo para ver el
// aspecto: métricas, un diagrama de carga por día y un checklist. Se sustituyen
// por los contratos reales (D3.1, M5) más adelante.
const METRICS = [
  { label: "pendientes", value: "07" },
  { label: "esta semana", value: "12" },
  { label: "para confirmar", value: "03" },
];

const BARS = [
  { day: "lun", value: 2 },
  { day: "mar", value: 5 },
  { day: "mié", value: 3 },
  { day: "jue", value: 7 },
  { day: "vie", value: 4 },
  { day: "sáb", value: 1 },
  { day: "dom", value: 6 },
];

const TASKS = [
  { label: "repasar integrales por partes", done: true },
  { label: "leer capítulo 4 de cálculo", done: false },
  { label: "entregar taller de ecuaciones", done: false },
  { label: "confirmar fecha del parcial", done: false },
];

export function AgendaPanel() {
  return (
    <Card title="05 · agenda / tareas" className="glitch-hover hover:border-mint">
      <div className="grid gap-6 md:grid-cols-3">
        {/* Métricas ficticias. */}
        <div className="flex flex-col gap-2">
          {METRICS.map((m) => (
            <div
              key={m.label}
              className="flex items-baseline justify-between border-b-2 border-lavender/20 pb-1 lowercase"
            >
              <span className="text-lavender">{m.label}</span>
              <span className="text-3xl text-mint">{m.value}</span>
            </div>
          ))}
        </div>

        {/* Diagrama de carga por día (barras). */}
        <div className="flex flex-col">
          <p className="lowercase text-lavender/60">carga por día</p>
          <div className="mt-2 flex h-32 items-end gap-2">
            {BARS.map((b) => (
              <div key={b.day} className="flex flex-1 flex-col items-center gap-1">
                <div
                  className="w-full bg-mint"
                  style={{ height: `${b.value * 14}px` }}
                  title={`${b.value} tareas`}
                />
                <span className="lowercase text-lavender/60">{b.day}</span>
              </div>
            ))}
          </div>
        </div>

        {/* Checklist ficticio. */}
        <div className="flex flex-col gap-2 lowercase">
          <p className="text-lavender/60">checklist</p>
          {TASKS.map((t) => (
            <div key={t.label} className="flex items-center gap-2 text-lavender">
              <span
                className={`inline-block h-4 w-4 shrink-0 border-2 border-pure-black ${
                  t.done ? "bg-mint" : "bg-transparent"
                }`}
                aria-hidden="true"
              />
              <span className={t.done ? "line-through opacity-50" : ""}>{t.label}</span>
            </div>
          ))}
        </div>
      </div>
    </Card>
  );
}
