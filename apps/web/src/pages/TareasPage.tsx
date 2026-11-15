import { Card } from "@/components/ui/Card";
import { TaskList } from "@/components/tasks/TaskList";

// Same `GET /tasks` list as the dashboard, so the two screens cannot disagree.
export function TareasPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="lowercase text-2xl text-mint">tareas</h1>

      <Card title="tus tareas">
        <TaskList />
      </Card>
    </div>
  );
}
