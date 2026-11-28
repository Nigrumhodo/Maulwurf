import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent } from "react";
import { useTranslation } from "react-i18next";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Button } from "@/components/ui/Button";
import { RetroVisual } from "@/components/ui/RetroVisual";
import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  estadoBusquedaRag,
  estadoBusquedaRagAnim,
  estadoLeyendoCitas,
  estadoLeyendoCitasAnim,
  iconChatCrt,
  iconChatCrtOscuro,
  loaderCassetteAmbar,
  loaderCassetteAmbarStatic,
  loaderCassetteLavanda,
  loaderCassetteLavandaStatic,
} from "@/components/Visual";
import i18n from "@/i18n";
import { CitationList } from "@/components/chat/Citation";
import { MarkdownBody } from "@/components/chat/MarkdownBody";
import { ProposalCard } from "@/components/chat/ProposalCard";
import { ToolBadge } from "@/components/chat/ToolBadge";
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
  ChatProposal,
  ChatStreamEvent,
  ChatToolCall,
  ConversationScope,
} from "@/lib/chat";

// D2.6 — Chat UI (M7). `/gestion/chat` permite conversaciones generales, de materia o de
// clase; el panel del inicio usa `generalOnly` y siempre habla de todas las clases. El
// streaming va por `fetch` POST (no EventSource), la clave de idempotencia vive en memoria
// y un corte de red se reconcilia desde el historial persistido, nunca se reenvía solo.

const MAX_CHAT_CHARS = 4000;

function isChatProposal(payload: Record<string, unknown>): payload is Record<string, unknown> & ChatProposal {
  return (
    typeof payload.task_id === "string" &&
    typeof payload.status === "string" &&
    (payload.evidence === "corpus" || payload.evidence === "general_suggestion")
  );
}

interface UiMessage {
  key: string;
  id: string | null;
  role: "user" | "assistant" | "system";
  content: string;
  status: "streaming" | "completed" | "failed" | "cancelled";
  clientMessageId: string | null;
  citations: ChatCitation[];
  toolCalls: ChatToolCall[];
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
    toolCalls: Array.isArray(message.tool_calls) ? message.tool_calls : [],
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
    if (event.type === "tool") {
      return { ...message, toolCalls: [...message.toolCalls, event.tool] };
    }
    if (event.type === "done") {
      return {
        ...message,
        status: event.status,
        toolCalls: event.tools.length > 0 ? event.tools : message.toolCalls,
      };
    }
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
      ? { error: i18n.t("study:chatWorkspace.pickSubject") }
      : { scope: { mode: "subject", subjectId } };
  }
  return audioId === ""
    ? { error: i18n.t("study:chatWorkspace.pickClass") }
    : { scope: { mode: "class", audioId } };
}

export function ChatWorkspace({
  generalOnly = false,
  compact = false,
  initialScope,
  onClose,
}: ChatWorkspaceProps) {
  const { t } = useTranslation("study");
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
  // Below lg only one pane is visible; both stay mounted so state and the stream survive a toggle.
  const [showList, setShowList] = useState(false);

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
      return subjectNames.get(conversation.subject_id ?? "") ?? t("chatWorkspace.subjectFallback");
    }
    if (conversation.mode === "class") {
      const audio = audios.find((item) => item.id === conversation.audio_id);
      return audio !== undefined ? audioLabel(audio) : t("chatWorkspace.classFallback");
    }
    return t("chatWorkspace.allClasses");
  }

  const selectedConversation =
    conversations.find((conversation) => conversation.id === conversationId) ?? null;

  async function openNewConversation(): Promise<string | null> {
    const built = scopeFor(generalOnly, scopeMode, scopeSubjectId, scopeAudioId);
    if (built.error !== undefined || built.scope === undefined) {
      setNotice(built.error ?? t("chatWorkspace.invalidScope"));
      return null;
    }
    setCreating(true);
    setNotice(null);
    try {
      const created = await createConversation(built.scope);
      skipNextHistoryRef.current = created.id;
      setConversations((current) => [created, ...current]);
      setConversationId(created.id);
      setShowList(false);
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
    if (content.length > MAX_CHAT_CHARS) {
      setNotice(t("chatWorkspace.tooLong", { max: MAX_CHAT_CHARS }));
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
        toolCalls: [],
      },
      {
        key: assistantKey,
        id: null,
        role: "assistant",
        content: "",
        status: "streaming",
        clientMessageId,
        citations: [],
        toolCalls: [],
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
      if (conversationIdRef.current !== conversationId) return;
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
    setShowList(false);
    if (id === conversationId) return;
    setConversationId(id);
    setMessages([]);
    setHistoryCursor(null);
    setNotice(null);
  }

  const sidebar = !generalOnly ? (
    <section aria-label={t("chatWorkspace.conversations")} className="min-w-0">
      <div className="lowercase flex flex-col gap-3">
        <h2 className="capitalize text-lg text-mint">{t("chatWorkspace.conversations")}</h2>
        <div className="flex flex-col gap-2 border-b-2 border-lavender/30 pb-3">
          <p className="text-mint">{t("chatWorkspace.newConversationHeading")}</p>
          <select
            aria-label={t("chatWorkspace.scopeAria")}
            value={scopeMode}
            onChange={(event) => {
              setScopeMode(event.target.value as ChatMode);
              setNotice(null);
            }}
            className="min-h-10 bg-surface text-lavender border-2 border-lavender px-2 py-1"
          >
            <option value="all">{t("chatLib.modeAll")}</option>
            <option value="subject">{t("chatLib.modeSubject")}</option>
            <option value="class">{t("chatLib.modeClass")}</option>
          </select>
          {scopeMode === "subject" && (
            <select
              aria-label={t("chatWorkspace.subjectAria")}
              value={scopeSubjectId}
              onChange={(event) => setScopeSubjectId(event.target.value)}
              className="min-h-10 bg-surface text-lavender border-2 border-lavender px-2 py-1"
            >
              <option value="">{t("chatWorkspace.selectSubject")}</option>
              {subjects.map((subject) => (
                <option key={subject.id} value={subject.id}>
                  {subject.name}
                </option>
              ))}
            </select>
          )}
          {scopeMode === "class" && (
            <select
              aria-label={t("chatWorkspace.classAria")}
              value={scopeAudioId}
              onChange={(event) => setScopeAudioId(event.target.value)}
              className="min-h-10 bg-surface text-lavender border-2 border-lavender px-2 py-1"
            >
              <option value="">{t("chatWorkspace.selectClass")}</option>
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
            variant="secondary"
            disabled={creating}
            onClick={() => void openNewConversation()}
          >
            {t("chatWorkspace.createAndOpen")}
          </Button>
        </div>

        {loadingConversations ? (
          <p className="text-lavender/80">{t("chatWorkspace.loadingConversations")}</p>
        ) : conversations.length === 0 ? (
          <p className="text-lavender/80">{t("chatWorkspace.noConversations")}</p>
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
                  <span className="block text-sm text-lavender/60">
                    {conversation.mode === "all"
                      ? t("chatWorkspace.general")
                      : modeLabel(conversation.mode)}
                  </span>
                </button>
                <button
                  type="button"
                  aria-label={t("chatWorkspace.deleteAria", { label: conversationLabel(conversation) })}
                  onClick={() => setDeleteTarget(conversation)}
                  className="min-h-8 border-2 border-lavender/30 px-2 text-mint hover:border-mint"
                >
                  ×
                </button>
              </li>
            ))}
          </ul>
        )}
        {nextConversations !== null && (
          <Button type="button" size="sm" variant="secondary" onClick={() => void loadMoreConversations()}>
            {t("chatWorkspace.loadMore")}
          </Button>
        )}
      </div>
    </section>
  ) : null;

  const chatBody = (
    <div className={`lowercase flex min-h-0 flex-col gap-3 ${compact ? "h-[24rem]" : "h-[30rem]"}`}>
      <div className="flex flex-wrap items-center justify-between gap-2 border-b-2 border-lavender/20 pb-2">
        <div className="min-w-0">
          <p className="truncate capitalize text-mint">
            &gt;{" "}
            {generalOnly
              ? t("chatWorkspace.generalChat")
              : selectedConversation !== null
                ? conversationLabel(selectedConversation)
                : t("chatWorkspace.newConversation")}
          </p>
          <p className="text-lavender/60">
            {generalOnly ? t("chatWorkspace.generalSub") : t("chatWorkspace.evidenceSub")}
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
              {t("chatWorkspace.new")}
            </Button>
          )}
          {onClose !== undefined && (
            <Button type="button" size="sm" variant="secondary" onClick={onClose}>
              {t("chatWorkspace.close")}
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
          <p className="text-lavender/80">{t("chatWorkspace.loadingMessages")}</p>
        ) : messages.length === 0 ? (
          <div className="flex flex-1 flex-col items-center justify-center gap-3 py-6 text-center">
            <div className="border-2 border-pure-black bg-dark p-2 shadow-block-sm">
              <span className="img-dark-only">
                <RetroVisual
                  src={estadoBusquedaRagAnim}
                  staticSrc={estadoBusquedaRag}
                  className={compact ? "h-24 w-24 object-contain" : "h-36 w-36 object-contain"}
                />
              </span>
              <span className="img-light-only">
                <RetroVisual
                  src={estadoLeyendoCitasAnim}
                  staticSrc={estadoLeyendoCitas}
                  className={compact ? "h-24 w-24 object-contain" : "h-36 w-36 object-contain"}
                />
              </span>
            </div>
            <p className="text-lavender/80">
              {conversationId === null
                ? t("chatWorkspace.emptyNew")
                : t("chatWorkspace.emptyNoMessages")}
            </p>
          </div>
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
                {loadingOlder ? t("chatWorkspace.loading") : t("chatWorkspace.loadOlder")}
              </Button>
            )}
            {messages.map((message) => (
              <div
                key={message.key}
                className={`flex max-w-[92%] flex-col gap-1 ${
                  message.role === "user" ? "self-end items-end" : "self-start items-start"
                }`}
              >
                <div className="flex items-center gap-2 text-sm text-lavender/60">
                  {message.role === "assistant" && (
                    <RetroVisual
                      src={iconChatCrtOscuro}
                      lightSrc={iconChatCrt}
                      className="h-5 w-5 object-contain"
                    />
                  )}
                  <span>{message.role === "user" ? t("chatWorkspace.you") : t("chatWorkspace.assistant")}</span>
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
                    <span className="inline-flex items-center gap-2">
                      <span className="img-dark-only">
                        <RetroVisual
                          src={loaderCassetteLavanda}
                          staticSrc={loaderCassetteLavandaStatic}
                          className="h-6 w-auto object-contain"
                        />
                      </span>
                      <span className="img-light-only">
                        <RetroVisual
                          src={loaderCassetteAmbar}
                          staticSrc={loaderCassetteAmbarStatic}
                          className="h-6 w-auto object-contain"
                        />
                      </span>
                      <span>…</span>
                    </span>
                  ) : message.status === "failed" ? (
                    t("chatWorkspace.answerFailed")
                  ) : message.status === "cancelled" ? (
                    t("chatWorkspace.answerCancelled")
                  ) : (
                    "-"
                  )}
                </div>
                {message.role === "assistant" && message.toolCalls.length > 0 && (
                  <div className="flex flex-wrap gap-1">
                    {message.toolCalls.map((tool, index) => (
                      <ToolBadge key={`${tool.name}-${index}`} tool={tool} />
                    ))}
                  </div>
                )}
                {message.role === "assistant" &&
                  message.toolCalls
                    .flatMap((tool, index) =>
                      tool.name === "propose_task" && tool.ok && isChatProposal(tool.payload)
                        ? [<ProposalCard key={`proposal-${index}`} proposal={tool.payload} />]
                        : [],
                    )}
                <CitationList citations={message.citations} />

              </div>
            ))}
          </>
        )}
      </div>

      <form ref={formRef} onSubmit={(event) => void handleSend(event)} className="flex flex-col gap-2">
        <textarea
          aria-label={t("chatWorkspace.question")}
          value={draft}
          maxLength={MAX_CHAT_CHARS}
          rows={compact ? 2 : 3}
          disabled={streaming || loadingHistory || loadingConversations}
          onChange={(event) => setDraft(event.target.value)}
          onKeyDown={handleComposerKey}
          placeholder={t("chatWorkspace.placeholder")}
          className="min-h-16 w-full resize-none border-2 border-lavender bg-surface px-3 py-2 font-pixel text-lavender caret-mint placeholder:text-lavender/40 focus:border-mint focus:outline-none disabled:opacity-50"
        />
        <div className="flex flex-wrap items-center justify-between gap-2">
          <span className="text-sm text-lavender/60">{draft.length}/{MAX_CHAT_CHARS}</span>
          <div className="flex items-center gap-2">
            {streaming && (
              <Button type="button" size="sm" variant="danger" onClick={() => void handleCancel()}>
                {t("chatWorkspace.cancel")}
              </Button>
            )}
            <Button
              type="submit"
              size="sm"
              disabled={streaming || creating || draft.trim() === ""}
            >
              {t("chatWorkspace.send")}
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
    <div className="grid lg:grid-cols-[18rem_minmax(0,1fr)]">
      <div className="flex items-center gap-2 border-b-2 border-lavender/30 p-3 lg:hidden">
        <Button
          type="button"
          size="sm"
          variant="secondary"
          aria-expanded={showList}
          onClick={() => setShowList((open) => !open)}
        >
          {showList ? t("chatWorkspace.backToConversation") : t("chatWorkspace.showConversations")}
        </Button>
      </div>
      <div className={`p-4 ${showList ? "block" : "hidden"} lg:block`}>{sidebar}</div>
      <section
        aria-label={t("chatWorkspace.conversation")}
        className={`min-w-0 p-4 lg:border-l-2 lg:border-lavender/30 ${showList ? "hidden" : "block"} lg:block`}
      >
        {chatBody}
      </section>
      <ConfirmDialog
        open={deleteTarget !== null}
        title={t("chatWorkspace.deleteTitle")}
        description={t("chatWorkspace.deleteDescription")}
        confirmLabel={t("chatWorkspace.deleteConfirm")}
        cancelLabel={t("shared.cancel")}
        destructive
        onConfirm={() => void confirmDelete()}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
