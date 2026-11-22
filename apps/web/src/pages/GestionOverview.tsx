import { Link } from "react-router-dom";
import { SubjectStats } from "@/components/subjects/SubjectStats";
import { buttonClasses } from "@/components/ui/buttonStyles";

// D- (Gestión) — Hub index: page header with the core action (subir clase) and
// metadata-only stats. Navigation lives in the sidebar only, so no module cards here.
export function GestionOverview() {
  return (
    <div className="flex flex-col gap-6">
      <header className="flex flex-wrap items-start justify-between gap-4">
        <div className="flex flex-col gap-1">
          <h1 className="capitalize text-2xl text-mint">gestión</h1>
          <p className="lowercase max-w-prose text-base text-lavender/80">
            panel académico: materias, clases, tareas y agenda.
          </p>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <Link to="/gestion/subir" className={buttonClasses("primary", "md")}>
            ↑ subir clase
          </Link>
          <Link to="/gestion/materias" className={buttonClasses("ghost", "sm", "min-h-10")}>
            + nueva materia
          </Link>
          <Link to="/gestion/tareas" className={buttonClasses("ghost", "sm", "min-h-10")}>
            + nueva tarea
          </Link>
        </div>
      </header>

      <SubjectStats />
    </div>
  );
}
