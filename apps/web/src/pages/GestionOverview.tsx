import { Link } from "react-router-dom";
import { GESTION_MODULES } from "@/components/layout/gestionNav";
import { buttonClasses } from "@/components/ui/buttonStyles";

// D- (Gestión) — Hub index. Technical subheader with quick actions, then one panel
// per module (materias & tareas · biblioteca & ingesta · terminal ia & agenda) and
// a status footer. Deliberately no pixel book shelf: the "tomos" shelf is deferred
// until a proper book image replaces the placeholder spines.
export function GestionOverview() {
  return (
    <div className="flex flex-col gap-4">
      <section className="border-2 border-pure-black bg-surface p-3 shadow-block">
        <div className="flex flex-wrap items-center justify-between gap-3 border-b-2 border-pure-black pb-2.5">
          <div className="flex flex-wrap items-center gap-2">
            <span className="animate-blink text-mint" aria-hidden="true">
              &gt;
            </span>
            <h1 className="lowercase text-xl text-mint">gestión // panel académico</h1>
            <span className="lowercase border-2 border-mint px-1.5 py-0.5 text-mint">[demo]</span>
          </div>
          <div className="flex items-center gap-2">
            <Link to="/gestion/materias" className={buttonClasses("primary", "sm")}>
              + nueva materia
            </Link>
            <Link to="/gestion/tareas" className={buttonClasses("secondary", "sm")}>
              + nueva tarea
            </Link>
          </div>
        </div>
        <div className="lowercase flex flex-wrap items-center justify-between gap-2 pt-2 text-lavender/60">
          <span>// elige un módulo o accede directo desde el menú lateral</span>
          <span>rack_id: shelf_bay_01 // bus_clock: 66mhz</span>
        </div>
      </section>

      <div className="grid gap-4 md:grid-cols-3">
        {GESTION_MODULES.map((module) => (
          <section
            key={module.id}
            className="flex flex-col border-2 border-pure-black bg-surface shadow-block"
          >
            <header className="flex items-center justify-between border-b-2 border-pure-black bg-mint px-3 py-2 text-on-accent">
              <span className="lowercase font-bold">
                {module.id} · {module.title}
              </span>
              <span className="lowercase border-2 border-on-accent px-1 text-xs">
                [{module.tag}]
              </span>
            </header>

            <div className="flex flex-1 flex-col gap-2 p-3">
              <p className="lowercase text-lavender/60">{module.desc}</p>

              <div className="mt-auto flex flex-col gap-2">
                {module.items.map((item) => (
                  <Link
                    key={item.to}
                    to={item.to}
                    className="lowercase flex items-center gap-2 border-2 border-pure-black bg-surface px-2 py-1.5 text-lavender hover:border-mint hover:text-mint"
                  >
                    <span aria-hidden="true">{item.glyph}</span>
                    <span>{item.label}</span>
                    <span className="ml-auto text-lavender/40" aria-hidden="true">
                      →
                    </span>
                  </Link>
                ))}
              </div>
            </div>
          </section>
        ))}
      </div>

      <footer className="lowercase flex flex-wrap items-center justify-between gap-2 border-2 border-pure-black bg-surface px-3 py-2 text-lavender/60">
        <span>audio no conservado · cifrado aes-gcm · solo texto persistido</span>
        <span>maulwurf@lab © 2026</span>
      </footer>
    </div>
  );
}
