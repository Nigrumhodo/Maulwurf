import { Link } from "react-router-dom";
import { TaskList } from "@/components/tasks/TaskList";

// Dashboard task log. The rows are `TaskOut` from `GET /tasks`, the same list as TareasPage.
export function TaskLog() {
  return (
    <section className="border-2 border-pure-black bg-surface shadow-block">
      <header className="flex items-center justify-between border-b-2 border-pure-black bg-mint px-3 py-2 text-on-accent">
        <span className="lowercase flex items-center gap-2 font-bold">
          <span aria-hidden="true">!</span>
          03 · tareas
        </span>
      </header>

      <div className="flex flex-col gap-2 p-3">
        <TaskList />
        <Link
          to="/gestion/tareas"
          className="lowercase mt-1 border-2 border-dashed border-pure-black bg-surface p-2 text-center text-lavender hover:border-mint hover:text-mint"
        >
          ver tareas
        </Link>
      </div>
    </section>
  );
}
