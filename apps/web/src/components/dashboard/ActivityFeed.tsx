import { Link } from "react-router-dom";
import { buttonClasses } from "@/components/ui/buttonStyles";
import { StatusBadge } from "@/components/ui/StatusBadge";

// Dashboard — "radar de actividad" (recent classes feed). Mock data: subject pill,
// honest per-class status (`ready` / `processing`), summary snippet and actions.
// There is deliberately no "listen" button: audio is not kept and has no playback.
interface ClassEntry {
  id: string;
  subject: string;
  title: string;
  meta: string;
  summary: string;
  status: string;
  processing: boolean;
  when: string;
}

const CLASSES: ClassEntry[] = [
  {
    id: "c1",
    subject: "tomo 01 · inteligencia artificial",
    title: "clase 08: redes neuronales profundas",
    meta: "prof. aris thorne · 1h 24m · calidad 98%",
    summary: "derivación de gradientes en capas densas y funciones relu.",
    status: "ready",
    processing: false,
    when: "hace 42m",
  },
  {
    id: "c2",
    subject: "tomo 02 · cálculo diferencial",
    title: "clase 05: integrales múltiples",
    meta: "prof. clara méndez · 52m · audio normalizado",
    summary: "extrayendo teoremas matemáticos y fórmulas latex... (72%)",
    status: "processing",
    processing: true,
    when: "hace 2h",
  },
  {
    id: "c3",
    subject: "tomo 03 · arquitectura de sistemas",
    title: "clase 03: kernel y llamadas al sistema",
    meta: "prof. ing. valenzuela · 1h 10m · 12 diagramas",
    summary: "cambios de contexto, interrupciones por temporizador y tablas pcb.",
    status: "ready",
    processing: false,
    when: "ayer",
  },
];

export function ActivityFeed() {
  return (
    <section className="flex flex-col gap-4">
      <div className="flex items-center justify-between border-2 border-pure-black bg-surface px-3 py-2 shadow-block">
        <div className="lowercase flex items-center gap-2 text-lavender">
          <span className="h-2.5 w-2.5 border border-pure-black bg-mint" aria-hidden="true" />
          <span className="font-bold tracking-wider">02 · radar de actividad // clases recientes</span>
        </div>
        <span className="lowercase border-2 border-pure-black bg-mint px-1.5 py-0.5 text-on-accent">
          demo
        </span>
      </div>

      {CLASSES.map((c) => (
        <article
          key={c.id}
          className="flex flex-col gap-2 border-2 border-pure-black bg-surface p-3 shadow-block hover:border-mint"
        >
          <div className="flex items-center justify-between border-b-2 border-lavender/15 pb-2">
            <div className="lowercase flex items-center gap-2 text-lavender">
              <span className="border-2 border-pure-black bg-mint px-1.5 py-0.5 text-on-accent">
                {c.subject.split(" · ")[0]}
              </span>
              <span className="text-lavender">{c.subject}</span>
            </div>
            <StatusBadge status={c.status} />
          </div>

          <div>
            <h3 className="lowercase text-lavender">{c.title}</h3>
            <p className="lowercase mt-0.5 text-lavender/50">{c.meta}</p>
          </div>

          <div className="lowercase border-l-4 border-mint bg-dark px-2 py-1.5 text-lavender/70">
            <span className="font-bold text-mint">&gt; resumen ia:</span> {c.summary}
          </div>

          <div className="flex items-center justify-between pt-1">
            <div className="flex items-center gap-2">
              <Link to="/gestion/biblioteca" className={buttonClasses("secondary", "sm")}>
                ver transcripción
              </Link>
              <Link
                to="/gestion/biblioteca"
                className={buttonClasses(
                  "primary",
                  "sm",
                  c.processing ? "pointer-events-none opacity-40" : "",
                )}
                aria-disabled={c.processing}
              >
                {c.processing ? "generando apunte..." : "abrir apunte .md"}
              </Link>
            </div>
            <span className="lowercase text-lavender/50">{c.when}</span>
          </div>
        </article>
      ))}

      <Link
        to="/gestion/biblioteca"
        className="lowercase flex items-center justify-center gap-2 border-2 border-pure-black bg-mint p-3 text-on-accent shadow-block hover:bg-surface hover:text-mint"
      >
        ir a la estantería completa de materias →
      </Link>
    </section>
  );
}
