import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { CitationList } from "@/components/chat/Citation";
import { MarkdownBody } from "@/components/chat/MarkdownBody";
import { audioLabel, fetchAudios } from "@/lib/library";
import type { LibraryAudio } from "@/lib/library";
import { fetchSubjects } from "@/lib/subjects";
import type { Subject } from "@/lib/subjects";
import {
  cancelChatMessage,
  chatActionError,
  createConversation,
  dedupeCitations,
  deleteConversation,
  fetchConversationMessages,
  fetchConversations,
  modeLabel,
  sendChatMessage,
} from "@/lib/chat";
import type {
  ChatCitation,
  ChatConversation,
  ChatMessage,
  ChatMode,
  ConversationScope,
  ChatStreamEvent,
} from "@/lib/chat";

// D2.6 — Chat UI (M7). `/gestion/chat` permite conversaciones generales, de materia o de
// clase; el panel del inicio usa `generalOnly` y siempre habla de todas las clases. El
// streaming va por `fetch` POST (no EventSource), la clave de idempotencia vive en memoria
// y un corte de red se reconcilia desde el historial persistido, nunca se reenvía solo.

interface UiMessage {
  key: string;
  id: string | null;
  role: "user" | "assistant" | "system";
  content: string;
  status: "streaming" | "completed" | "failed" | "cancelled";
  clientMessageId: string | null;
  citations: ChatCitation[];
}

export interface ChatWorkspaceProps {
  generalOnly?: boolean;
  compact?: boolean;
  initialScope?: ConversationScope;
  onClose?: () => void;
}

function fromServer(message: ChatMessage): UiMessage {
  return {
    key: message.id,
    id: message.id,
    role: message.role,
    content: message.content,
    status: message.status,
    clientMessageId: message.client_message_id,
    citations: Array.isArray(message.citas) ? dedupeCitations(message.citas) : [],
  };
}

function newClientMessageId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID();
  }
  return `web-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

function applyStreamEvent(messages: UiMessage[], key: string, event: ChatStreamEvent): UiMessage[] {
  return messages.map((message) => {
    if (message.key !== key) return message;
    if (event.type === "snapshot") {
      return { ...message, id: event.messageId, status: event.status };
    }
    if (event.type === "delta") {
      return { ...message, content: message.content + event.text };
    }
    if (event.type === "citation") {
      if (message.citations.some((citation) => citation.index === event.citation.index)) {
        return message;
      }
      return { ...message, citations: [...message.citations, event.citation] };
    }
    if (event.type === "done") return { ...message, status: event.status };
    if (event.type === "error") return { ...message, status: "failed" };
    return message;
  });
}

function markCancelled(messages: UiMessage[], key: string): UiMessage[] {
  return messages.map((message) =>
    message.key === key ? { ...message, status: "cancelled" } : message,
  );
}

function markFailed(messages: UiMessage[], key: string): UiMessage[] {
  return messages.map((message) =>
    message.key === key ? { ...message, status: "failed" } : message,
  );
}

function scopeFor(
  generalOnly: boolean,
  mode: ChatMode,
  subjectId: string,
  audioId: string,
): { scope?: ConversationScope; error?: string } {
  if (generalOnly || mode === "all") return { scope: { mode: "all" } };
  if (mode === "subject") {
    return subjectId === ""
      ? { error: "elige una materia para la conversación" }
      : { scope: { mode: "subject", subjectId } };
  }
  return audioId === ""
    ? { error: "elige una clase para la conversación" }
    : { scope: { mode: "class", audioId } };
}

export function ChatWorkspace({
  generalOnly = false,
  compact = false,
  initialScope,
  onClose,
}: ChatWorkspaceProps) {
  const [conversations, setConversations] = useState<ChatConversation[]>([]);
  const [nextConversations, setNextConversations] = useState<string | null>(null);
  const [conversationId, setConversationId] = useState<string | null>(null);
  const [messages, setMessages] = useState<UiMessage[]>([]);
  const [historyCursor, setHistoryCursor] = useState<string | null>(null);
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [audios, setAudios] = useState<LibraryAudio[]>([]);
  const [scopeMode, setScopeMode] = useState<ChatMode>(initialScope?.mode ?? "all");
  const [scopeSubjectId, setScopeSubjectId] = useState(initialScope?.subjectId ?? "");
  const [scopeAudioId, setScopeAudioId] = useState(initialScope?.audioId ?? "");
  const [loadingConversations, setLoadingConversations] = useState(true);
  const [loadingHistory, setLoadingHistory] = useState(false);
  const [loadingOlder, setLoadingOlder] = useState(false);
  const [creating, setCreating] = useState(false);
  const [streaming, setStreaming] = useState(false);
  const [draft, setDraft] = useState("");
  const [notice, setNotice] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<ChatConversation | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const assistantIdRef = useRef<string | null>(null);
  const assistantKeyRef = useRef<string | null>(null);
  const skipNextHistoryRef = useRef<string | null>(null);
  const conversationIdRef = useRef<string | null>(null);
  const listRef = useRef<HTMLDivElement | null>(null);
  const formRef = useRef<HTMLFormElement | null>(null);

  useEffect(() => {
    conversationIdRef.current = conversationId;
  }, [conversationId]);

  useEffect(() => {
    listRef.current?.scrollTo?.({ top: listRef.current.scrollHeight });
  }, [messages]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
    };
  }, []);

  useEffect(() => {
    let cancelled = false;
    setLoadingConversations(true);
    fetchConversations()
      .then((page) => {
        if (cancelled) return;
        setConversations(page.items);
        setNextConversations(page.next_cursor);
        const eligible = generalOnly
          ? page.items.filter((conversation) => conversation.mode === "all")
          : page.items;
        setConversationId(eligible[0]?.id ?? null);
      })
      .catch((error: unknown) => {
        if (!cancelled) setNotice(chatActionError(error));
      })
      .finally(() => {
        if (!cancelled) setLoadingConversations(false);
      });

    if (!generalOnly) {
      Promise.all([fetchSubjects(), fetchAudios()])
        .then(([nextSubjects, nextAudios]) => {
          if (cancelled) return;
          setSubjects(nextSubjects);
          setAudios(nextAudios);
        })
        .catch((error: unknown) => {
          if (!cancelled) setNotice(chatActionError(error));
        });
    }

    return () => {
      cancelled = true;
    };
  }, [generalOnly]);

  useEffect(() => {
    if (conversationId === null) {
      setMessages([]);
      setHistoryCursor(null);
      return;
    }
    if (skipNextHistoryRef.current === conversationId) {
      skipNextHistoryRef.current = null;
      setLoadingHistory(false);
      return;
    }
    let cancelled = false;
    setLoadingHistory(true);
    setNotice(null);
    fetchConversationMessages(conversationId)
      .then((page) => {
        if (cancelled) return;
        // A send can race the first history load. Keep the optimistic turn if it is already
        // on screen; recovery after a cut uses `recover()` and does replace the list.
        setMessages((current) =>
          current.length === 0 ? page.items.map(fromServer) : current,
        );
        setHistoryCursor(page.next_cursor);
      })
      .catch((error: unknown) => {
        if (!cancelled) setNotice(chatActionError(error));
      })
      .finally(() => {
        if (!cancelled) setLoadingHistory(false);
      });
    return () => {
      cancelled = true;
    };
  }, [conversationId]);

  const subjectNames = useMemo(
    () => new Map(subjects.map((subject) => [subject.id, subject.name])),
    [subjects],
  );

  function conversationLabel(conversation: ChatConversation): string {
    if (conversation.title !== null && conversation.title.trim() !== "") {
      return conversation.title;
    }
    if (conversation.mode === "subject") {
      return subjectNames.get(conversation.subject_id ?? "") ?? "materia";
    }
    if (conversation.mode === "class") {
      const audio = audios.find((item) => item.id === conversation.audio_id);
      return audio !== undefined ? audioLabel(audio) : "clase";
    }
    return "todas las clases";
  }

  const selectedConversation =
    conversations.find((conversation) => conversation.id === conversationId) ?? null;

  async function openNewConversation(): Promise<string | null> {
    const built = scopeFor(generalOnly, scopeMode, scopeSubjectId, scopeAudioId);
    if (built.error !== undefined || built.scope === undefined) {
      setNotice(built.error ?? "alcance de conversación inválido");
      return null;
    }
    setCreating(true);
    setNotice(null);
    try {
      const created = await createConversation(built.scope);
      skipNextHistoryRef.current = created.id;
      setConversations((current) => [created, ...current]);
      setConversationId(created.id);
      setMessages([]);
      setHistoryCursor(null);
      return created.id;
    } catch (error) {
      setNotice(chatActionError(error));
      return null;
    } finally {
      setCreating(false);
    }
  }

  async function recover(conversation: string): Promise<void> {
    try {
      const page = await fetchConversationMessages(conversation);
      if (conversationIdRef.current !== conversation) return;
      setMessages(page.items.map(fromServer));
      setHistoryCursor(page.next_cursor);
    } catch {
      // Keep the local partial visible; the user can reopen the conversation.
    }
  }

  async function handleSend(event: FormEvent) {
    event.preventDefault();
    const content = draft.trim();
    if (content === "" || streaming || creating) return;
    if (content.length > 4000) {
      setNotice("la pregunta supera el máximo de 4000 caracteres.");
      return;
    }

    let targetConversation = conversationId;
    if (targetConversation === null) {
      targetConversation = await openNewConversation();
      if (targetConversation === null) return;
    }

    setDraft("");
    setNotice(null);
    const clientMessageId = newClientMessageId();
    const userKey = `${clientMessageId}-user`;
    const assistantKey = `${clientMessageId}-assistant`;
    assistantKeyRef.current = assistantKey;
    assistantIdRef.current = null;
    setMessages((current) => [
      ...current,
      {
        key: userKey,
        id: null,
        role: "user",
        content,
        status: "completed",
        clientMessageId,
        citations: [],
      },
      {
        key: assistantKey,
        id: null,
        role: "assistant",
        content: "",
        status: "streaming",
        clientMessageId,
        citations: [],
      },
    ]);

    const controller = new AbortController();
    abortRef.current = controller;
    setStreaming(true);
    try {
      const terminal = await sendChatMessage({
        conversationId: targetConversation,
        content,
        clientMessageId,
        signal: controller.signal,
        onEvent: (streamEvent) => {
          if (streamEvent.type === "snapshot") assistantIdRef.current = streamEvent.messageId;
          if (streamEvent.type === "error") setNotice(streamEvent.message);
          setMessages((current) => applyStreamEvent(current, assistantKey, streamEvent));
        },
      });
      if (!terminal) await recover(targetConversation);
    } catch (error) {
      if (controller.signal.aborted) return;
      setNotice(chatActionError(error));
      setMessages((current) => markFailed(current, assistantKey));
      await recover(targetConversation);
    } finally {
      setStreaming(false);
      abortRef.current = null;
      assistantKeyRef.current = null;
      assistantIdRef.current = null;
    }
  }

  async function handleCancel() {
    const assistantId = assistantIdRef.current;
    const assistantKey = assistantKeyRef.current;
    const targetConversation = conversationId;
    abortRef.current?.abort();
    if (assistantKey !== null) {
      setMessages((current) => markCancelled(current, assistantKey));
    }
    setStreaming(false);
    if (targetConversation !== null && assistantId !== null) {
      try {
        await cancelChatMessage(targetConversation, assistantId);
      } catch (error) {
        setNotice(chatActionError(error));
      }
      await recover(targetConversation);
    }
  }

  async function loadOlder() {
    if (conversationId === null || historyCursor === null || loadingOlder) return;
    setLoadingOlder(true);
    try {
      const page = await fetchConversationMessages(conversationId, historyCursor);
      setMessages((current) => [...page.items.map(fromServer), ...current]);
      setHistoryCursor(page.next_cursor);
    } catch (error) {
      setNotice(chatActionError(error));
    } finally {
      setLoadingOlder(false);
    }
  }

  async function loadMoreConversations() {
    if (nextConversations === null) return;
    try {
      const page = await fetchConversations(nextConversations);
      setConversations((current) => [...current, ...page.items]);
      setNextConversations(page.next_cursor);
    } catch (error) {
      setNotice(chatActionError(error));
    }
  }

  async function confirmDelete() {
    const target = deleteTarget;
    if (target === null) return;
    setDeleteTarget(null);
    try {
      await deleteConversation(target.id);
      const remaining = conversations.filter((conversation) => conversation.id !== target.id);
      setConversations(remaining);
      if (conversationId === target.id) {
        skipNextHistoryRef.current = remaining[0]?.id ?? null;
        setConversationId(remaining[0]?.id ?? null);
        setMessages([]);
        setHistoryCursor(null);
      }
    } catch (error) {
      setNotice(chatActionError(error));
    }
  }

  function handleComposerKey(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      formRef.current?.requestSubmit();
    }
  }

  function selectConversation(id: string) {
    if (id === conversationId) return;
    setConversationId(id);
    setMessages([]);
    setHistoryCursor(null);
    setNotice(null);
  }

  const sidebar = !generalOnly ? (
    <Card title="conversaciones">
      <div className="lowercase flex flex-col gap-3">
        <div className="flex flex-col gap-2 border-2 border-lavender/30 px-3 py-2">
          <p className="text-mint">&gt; nueva conversación</p>
          <select
            aria-label="alcance de la conversación"
            value={scopeMode}
            onChange={(event) => {
              setScopeMode(event.target.value as ChatMode);
              setNotice(null);
            }}
            className="bg-surface text-lavender border-2 border-lavender px-2 py-1"
          >
            <option value="all">todas las clases</option>
            <option value="subject">una materia</option>
            <option value="class">una clase</option>
          </select>
          {scopeMode === "subject" && (
            <select
              aria-label="materia de la conversación"
              value={scopeSubjectId}
              onChange={(event) => setScopeSubjectId(event.target.value)}
              className="bg-surface text-lavender border-2 border-lavender px-2 py-1"
            >
              <option value="">selecciona materia</option>
              {subjects.map((subject) => (
                <option key={subject.id} value={subject.id}>
                  {subject.name}
                </option>
              ))}
            </select>
          )}
          {scopeMode === "class" && (
            <select
              aria-label="clase de la conversación"
              value={scopeAudioId}
              onChange={(event) => setScopeAudioId(event.target.value)}
              className="bg-surface text-lavender border-2 border-lavender px-2 py-1"
            >
              <option value="">selecciona clase</option>
              {audios.map((audio) => (
                <option key={audio.id} value={audio.id}>
                  {audio.subject.name} · {audioLabel(audio)}
                </option>
              ))}
            </select>
          )}
          <Button
            type="button"
            size="sm"
            disabled={creating}
            onClick={() => void openNewConversation()}
          >
            crear y abrir
          </Button>
        </div>

        {loadingConversations ? (
          <p className="text-lavender/60">cargando conversaciones…</p>
        ) : conversations.length === 0 ? (
          <p className="text-lavender/60">aún no hay conversaciones.</p>
        ) : (
          <ul className="flex flex-col gap-1">
            {conversations.map((conversation) => (
              <li key={conversation.id} className="flex items-stretch gap-1">
                <button
                  type="button"
                  onClick={() => selectConversation(conversation.id)}
                  className={`min-w-0 flex-1 border-2 px-2 py-1 text-left ${
                    conversation.id === conversationId
                      ? "border-mint text-mint"
                      : "border-lavender/30 text-lavender hover:border-mint"
                  }`}
                >
                  <span className="block truncate">{conversationLabel(conversation)}</span>
                  <span className="block text-xs text-lavender/50">
                    {conversation.mode === "all" ? "general" : modeLabel(conversation.mode)}
                  </span>
                </button>
                <button
                  type="button"
                  aria-label={`borrar conversación ${conversationLabel(conversation)}`}
                  onClick={() => setDeleteTarget(conversation)}
                  className="border-2 border-lavender/30 px-2 text-mint hover:border-mint"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
        {nextConversations !== null && (
          <Button type="button" size="sm" variant="secondary" onClick={() => void loadMoreConversations()}>
            cargar más
          </Button>
        )}
      </div>
    </Card>
  ) : null;

  const chatBody = (
    <div className={`lowercase flex min-h-0 flex-col gap-3 ${compact ? "h-[24rem]" : "h-[30rem]"}`}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b-2 border-lavender/20 pb-2">
        <div className="min-w-0">
          <p className="truncate text-mint">
            &gt;{" "}
            {generalOnly
              ? "chat general · todas las clases"
              : selectedConversation !== null
                ? conversationLabel(selectedConversation)
                : "nueva conversación"}
          </p>
          <p className="text-lavender/50">
            {generalOnly ? "preguntas generales sobre tus apuntes" : "conversación con evidencia"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          {generalOnly && (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              disabled={creating}
              onClick={() => void openNewConversation()}
            >
              nueva
            </Button>
          )}
          {onClose !== undefined && (
            <Button type="button" size="sm" variant="secondary" onClick={onClose}>
              cerrar
            </Button>
          )}
        </div>
      </div>

      {notice !== null && (
        <p role="alert" className="border-2 border-mint px-2 py-1 text-mint">
          &gt; {notice}
        </p>
      )}

      <div ref={listRef} className="flex min-h-0 flex-1 flex-col gap-3 overflow-y-auto pr-1">
        {loadingHistory ? (
          <p className="text-lavender/60">cargando mensajes…</p>
        ) : messages.length === 0 ? (
          <p className="text-lavender/60">
            &gt;{" "}
            {conversationId === null
              ? "escribe una pregunta para abrir la conversación."
              : "sin mensajes todavía."}
          </p>
        ) : (
          <>
            {historyCursor !== null && (
              <Button
                type="button"
                size="sm"
                variant="secondary"
                disabled={loadingOlder}
                onClick={() => void loadOlder()}
              >
                {loadingOlder ? "cargando…" : "cargar mensajes anteriores"}
              </Button>
            )}
            {messages.map((message) => (
              <div
                key={message.key}
                className={`flex max-w-[92%] flex-col gap-1 ${
                  message.role === "user" ? "self-end items-end" : "self-start items-start"
                }`}
              >
                <div className="flex items-center gap-2 text-xs text-lavender/50">
                  <span>{message.role === "user" ? "tú" : "asistente"}</span>
                  {message.role === "assistant" && <StatusBadge status={message.status} />}
                </div>
                <div
                  className={`border-2 px-3 py-2 ${
                    message.role === "user"
                      ? "whitespace-pre-wrap border-mint bg-mint/10 text-lavender"
                      : "border-lavender/30 text-lavender"
                  }`}
                >
                  {message.content !== "" ? (
                    message.role === "assistant" ? (
                      <MarkdownBody content={message.content} />
                    ) : (
                      message.content
                    )
                  ) : message.status === "streaming" ? (
                    "…"
                  ) : message.status === "failed" ? (
                    "la respuesta falló; vuelve a preguntar."
                  ) : message.status === "cancelled" ? (
                    "respuesta cancelada."
                  ) : (
                    "-"
                  )}
                </div>
                <CitationList citations={message.citations} />

              </div>
            ))}
          </>
        )}
      </div>

      <form ref={formRef} onSubmit={(event) => void handleSend(event)} className="flex flex-col gap-2">
        <textarea
          aria-label="pregunta"
          value={draft}
          maxLength={4000}
          rows={compact ? 2 : 3}
          disabled={streaming || loadingHistory || loadingConversations}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleComposerKey}
          placeholder="pregunta sobre tus clases…"
          className="min-h-16 w-full resize-none border-2 border-lavender bg-surface px-3 py-2 font-pixel text-lavender caret-mint placeholder:text-lavender/40 focus:border-mint focus:outline-none disabled:opacity-50"
        />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-xs text-lavender/50">{draft.length}/4000</span>
          <div className="flex items-center gap-2">
            {streaming && (
              <Button type="button" size="sm" variant="danger" onClick={() => void handleCancel()}>
                cancelar
              </Button>
            )}
            <Button
              type="submit"
              size="sm"
              disabled={streaming || creating || draft.trim() === ""}
            >
              enviar
            </Button>
          </div>
        </div>
      </form>
    </div>
  );

  if (generalOnly) {
    return chatBody;
  }

  return (
    <div className="grid gap-4 lg:grid-cols-[18rem_minmax(0,1fr)]">
      {sidebar}
      <Card title="conversación">{chatBody}</Card>
      <ConfirmDialog
        open={deleteTarget !== null}
        title="borrar conversación"
        description="se borrarán sus mensajes y fuentes. esta acción no se puede deshacer."
        confirmLabel="borrar"
        destructive
        onConfirm={() => void confirmDelete()}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
