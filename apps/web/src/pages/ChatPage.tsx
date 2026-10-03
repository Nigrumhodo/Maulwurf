import { Card } from "@/components/ui/Card";

// D1.2 — Chat esqueleto. La conversación RAG con citas llega en D2.6/D2.7.
export function ChatPage() {
  return (
    <div className="flex flex-col gap-6">
      <h1 className="lowercase text-2xl text-mint">chat</h1>

      <Card title="conversación">
        <p className="lowercase text-lavender">el chat con tus apuntes llega en d2.6.</p>
      </Card>
    </div>
  );
}
