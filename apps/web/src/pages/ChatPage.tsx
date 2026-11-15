import { Card } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";

// The only conversation surface. The dashboard does not open a second chat panel.
export function ChatPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="lowercase text-2xl text-mint">chat</h1>

      <Card title="conversación">
        <EmptyState
          title="aún no hay conversaciones"
          description="las preguntas sobre tus apuntes se hacen en esta pantalla."
        />
      </Card>
    </div>
  );
}
