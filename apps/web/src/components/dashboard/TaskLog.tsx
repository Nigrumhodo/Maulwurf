import { Link } from "react-router-dom";

// Dashboard — "bitácora de tareas urgentes" (task log). Static mock; real tasks
// land in D3.1 (M4). The checkbox is decorative for now.
interface Task {
  id: string;
  tag: string;
  due: string;
  title: string;
  subject: string;
}

const TASKS: Task[] = [
  { id: "t1", tag: "[!] urgente", due: "en 2 días", title: "entrega proyecto backprop", subject: "ia" },
  { id: "t2", tag: "[*] lectura", due: "viernes", title: "leer cap. 4 guía protocolos", subject: "redes" },
  { id: "t3", tag: "[?] estudio", due: "próx. semana", title: "repasar parcial de integrales", subject: "cálculo" },
];

export function TaskLog() {
  return (
    <section className="border-2 border-pure-black bg-surface shadow-block">
      <header className="flex items-center justify-between border-b-2 border-pure-black bg-mint px-3 py-2 text-on-accent">
        <span className="lowercase flex items-center gap-2 font-bold">
          <span aria-hidden="true">!</span>
          03 · bitácora de tareas urgentes
        </span>
        <span className="lowercase border-2 border-on-accent px-1.5 py-0.5 text-xs">
          pendientes: 3
        </span>
      </header>

      <div className="flex flex-col gap-2 p-3">
        {TASKS.map((t) => (
          <div
            key={t.id}
            className="lowercase flex flex-col gap-1 border-2 border-pure-black bg-surface p-2 hover:border-mint"
          >
            <div className="flex items-center justify-between">
              <span className="border-2 border-pure-black bg-mint px-1 text-on-accent">{t.tag}</span>
              <span className="text-lavender/50">{t.due}</span>
            </div>
            <p className="text-lavender">{t.title}</p>
            <div className="flex items-center justify-between border-t-2 border-lavender/15 pt-1 text-lavender/60">
              <span>materia: {t.subject}</span>
              <input
                type="checkbox"
                aria-label={`marcar ${t.title} como hecha`}
                className="h-4 w-4 cursor-pointer accent-mint"
              />
            </div>
          </div>
        ))}

        <Link
          to="/gestion/tareas"
          className="lowercase mt-1 border-2 border-dashed border-pure-black bg-surface p-2 text-center text-lavender hover:border-mint hover:text-mint"
        >
          + agregar nueva tarea
        </Link>
      </div>
    </section>
  );
}
