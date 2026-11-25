import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { ChatWorkspace } from "@/components/chat/ChatWorkspace";
import type { ChatMode, ConversationScope } from "@/lib/chat";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

// D2.6 — Chat académico: todas las clases, una materia o una clase. La ruta acepta
// `?mode=subject&subject_id=…` / `?mode=class&audio_id=…` para que biblioteca o materias
// puedan abrir el chat ya acotado sin inventar el alcance.
function modeFromParam(value: string | null): ChatMode {
  return value === "subject" || value === "class" ? value : "all";
}

export function ChatPage() {
  const { t } = useTranslation("study");
  useDocumentTitle(t("chatPage.docTitle"));
  const [params] = useSearchParams();
  const mode = modeFromParam(params.get("mode"));
  const initialScope: ConversationScope = {
    mode,
    subjectId: mode === "subject" ? params.get("subject_id") : null,
    audioId: mode === "class" ? params.get("audio_id") : null,
  };

  return (
    <div className="flex flex-col border-2 border-pure-black bg-surface shadow-block">
      <header className="flex flex-col gap-1 border-b-2 border-lavender/30 p-4 sm:p-6">
        <h1 className="capitalize text-2xl text-mint">{t("chatPage.title")}</h1>
        <p className="max-w-prose lowercase text-base text-lavender/80">
          {t("chatPage.lead")}
        </p>
      </header>
      <ChatWorkspace initialScope={initialScope} />
    </div>
  );
}
