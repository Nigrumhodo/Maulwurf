import { Link } from "react-router-dom";
import { TaskList } from "@/components/tasks/TaskList";
import { buttonClasses } from "@/components/ui/buttonStyles";

// Task log. The rows are `TaskOut` from `GET /tasks`, the same list as TareasPage.
export function TaskLog() {
  return (
    <div className="flex flex-col gap-3">
      <TaskList />
      <Link
        to="/gestion/tareas"
        className={buttonClasses("secondary", "sm", "min-h-10 self-start")}
      >
        ver tareas
      </Link>
    </div>
  );
}
