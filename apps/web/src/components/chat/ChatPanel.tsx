import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";

export interface ChatPanelProps {
  onClose: () => void;
}

// Placeholder del chat. La lógica real (SSE, historial, citas clicables) llega con
// el backend en D2.6/D2.7; aquí solo la carcasa visual.
export function ChatPanel({ onClose }: ChatPanelProps) {
  return (
    <Card title="chat" variant="accent" className="w-80">
      <div className="flex flex-col gap-3">
        <div className="flex h-48 flex-col gap-2 overflow-y-auto">
          {/* TODO(D2.6): historial de mensajes — GET /chat/conversations/{id}/messages */}
          <p className="lowercase text-lavender/60">— sin mensajes —</p>
        </div>

        <div className="flex items-center gap-2">
          <input
            type="text"
            placeholder="escribe…"
            className="min-w-0 flex-1 border-2 border-lavender bg-surface px-3 py-2 font-pixel lowercase text-lavender caret-mint placeholder:text-lavender/40 focus:border-mint focus:outline-none"
          />
          <Button size="sm" onClick={onClose}>
            cerrar
          </Button>
        </div>
      </div>
    </Card>
  );
}
