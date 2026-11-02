import { Card } from "@/components/ui/Card";
import { ChatWorkspace } from "@/components/chat/ChatWorkspace";

export interface ChatPanelProps {
  onClose: () => void;
}

// D2.6 — Panel del inicio: chat general, siempre sobre todas las clases. No ofrece selector
// de materia/clase; ese contexto vive en `/gestion/chat`. Tampoco escribe Calendar ni envía
// correo: solo responde con evidencia textual.
export function ChatPanel({ onClose }: ChatPanelProps) {
  return (
    <Card
      variant="accent"
      showHeader={false}
      className="w-96 max-w-[calc(100vw-3rem)]"
    >
      <ChatWorkspace generalOnly compact onClose={onClose} />
    </Card>
  );
}
