import { Link } from "react-router-dom";
import { ActivityFeed } from "@/components/dashboard/ActivityFeed";
import { DashboardSection } from "@/components/dashboard/DashboardSection";
import { StatusTicker } from "@/components/dashboard/StatusTicker";
import { TaskLog } from "@/components/dashboard/TaskLog";
import { WeekStrip } from "@/components/dashboard/WeekStrip";
import { SubjectStats } from "@/components/subjects/SubjectStats";
import { buttonClasses } from "@/components/ui/buttonStyles";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

// D- (Gestión) — Operational detail view: one main window with header (core action) and
// sections for status, week, activity and the task log. Navigation lives in the sidebar only.
export function GestionOverview() {
  useDocumentTitle("gestión");
  return (
    <div className="flex flex-col gap-6 border-2 border-pure-black bg-surface p-6 shadow-block">
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

      <div className="divide-y-2 divide-lavender/20 border-t-2 border-lavender/20 pt-6">
        <DashboardSection id="panel-estado" title="estado">
          <SubjectStats />
        </DashboardSection>
        <DashboardSection id="panel-semana" title="semana">
          <WeekStrip />
        </DashboardSection>
        <DashboardSection id="panel-actividad" title="actividad" meta="clases recientes">
          <ActivityFeed />
        </DashboardSection>
        <DashboardSection id="panel-tareas" title="registro de tareas">
          <TaskLog />
        </DashboardSection>
        <DashboardSection id="panel-sistema" title="sistema">
          <StatusTicker />
        </DashboardSection>
      </div>
    </div>
  );
}
