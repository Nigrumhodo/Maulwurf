import { useSearchParams } from "react-router-dom";
import { ChatWorkspace } from "@/components/chat/ChatWorkspace";
import type { ChatMode, ConversationScope } from "@/lib/chat";

// D2.6 — Chat académico: todas las clases, una materia o una clase. La ruta acepta
// `?mode=subject&subject_id=…` / `?mode=class&audio_id=…` para que biblioteca o materias
// puedan abrir el chat ya acotado sin inventar el alcance.
function modeFromParam(value: string | null): ChatMode {
  return value === "subject" || value === "class" ? value : "all";
}

export function ChatPage() {
  const [params] = useSearchParams();
  const mode = modeFromParam(params.get("mode"));
  const initialScope: ConversationScope = {
    mode,
    subjectId: mode === "subject" ? params.get("subject_id") : null,
    audioId: mode === "class" ? params.get("audio_id") : null,
  };

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="capitalize text-2xl text-mint">chat</h1>
        <p className="lowercase text-lavender/60">
          &gt; pregunta sobre todas tus clases, una materia o una clase concreta. Las respuestas
          se apoyan en fragmentos de la biblioteca.
        </p>
      </div>
      <ChatWorkspace initialScope={initialScope} />
    </div>
  );
}
