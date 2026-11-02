// D2.6 — C-S2-DA-05 (soporte unitario): contrato de conversaciones, parseo de eventos y
// consumo del POST SSE con `client_message_id` estable.
import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiHttpError } from "@/lib/api";
import { setCsrfToken, clearCsrfToken } from "@/lib/csrf";
import {
  chatActionError,
  createConversation,
  dedupeCitations,
  deleteConversation,
  fetchConversationMessages,
  fetchConversations,
  parseChatEvent,
  sendChatMessage,
} from "@/lib/chat";
import type { ChatStreamEvent } from "@/lib/chat";

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  clearCsrfToken();
});

describe("parseChatEvent", () => {
  it("parsea snapshot, delta, citation, done y error", () => {
    expect(
      parseChatEvent("snapshot", JSON.stringify({ message_id: "m1", status: "streaming" })),
    ).toEqual({ type: "snapshot", messageId: "m1", status: "streaming" });
    expect(parseChatEvent("delta", JSON.stringify({ text: "hola" }))).toEqual({
      type: "delta",
      text: "hola",
    });
    expect(
      parseChatEvent(
        "citation",
        JSON.stringify({
          index: 1,
          audio_id: "a1",
          transcript_version: 1,
          chunk_id: "c1",
          segment_id: "s1",
          t_start: 0,
          t_end: 5,
          label: "clase · 00:00",
          url: "/biblioteca/a1?segment=s1",
        }),
      ),
    ).toEqual({
      type: "citation",
      citation: {
        index: 1,
        offset: undefined,
        audio_id: "a1",
        transcript_id: null,
        transcript_version: 1,
        chunk_id: "c1",
        segment_id: "s1",
        t_start: 0,
        t_end: 5,
        label: "clase · 00:00",
        url: "/biblioteca/a1?segment=s1",
        quote: null,
      },
    });
    expect(
      parseChatEvent(
        "done",
        JSON.stringify({
          message_id: "m1",
          conversation_id: "c1",
          status: "completed",
          sources: [{ kind: "transcript", audio_id: "a1" }],
        }),
      ),
    ).toEqual({
      type: "done",
      messageId: "m1",
      conversationId: "c1",
      status: "completed",
      sources: [{ kind: "transcript", audio_id: "a1" }],
    });
    expect(
      parseChatEvent("error", JSON.stringify({ code: "provider_unavailable", message: "sin modelo" })),
    ).toEqual({ type: "error", code: "provider_unavailable", message: "sin modelo" });
  });

  it("ignora eventos desconocidos y payloads inválidos", () => {
    expect(parseChatEvent("message", "{}")).toBeNull();
    expect(parseChatEvent("delta", "{")).toBeNull();
    expect(parseChatEvent("snapshot", JSON.stringify({ status: "streaming" }))).toBeNull();
    expect(parseChatEvent("done", JSON.stringify({ status: "completed" }))).toBeNull();
  });
});

describe("contrato de conversaciones", () => {
  it("deduplica citas persistidas por índice", () => {
    const citation = {
      index: 1,
      audio_id: "a1",
      transcript_version: 1,
      chunk_id: "c1",
      segment_id: "s1",
      t_start: 0,
      t_end: 5,
      label: "clase · 0:05",
      url: "/biblioteca/a1?segment=s1",
    };
    expect(
      dedupeCitations([
        citation,
        { ...citation, chunk_id: "c2" },
        { ...citation, index: 2, chunk_id: "c3" },
      ]).map((item) => item.index),
    ).toEqual([1, 2]);
  });

  it("lista y pagina con no-store", async () => {
    const page = { items: [], next_cursor: "next" };
    const fetchMock = vi.fn().mockResolvedValue(json(page));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchConversations("abc")).resolves.toEqual(page);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/chat/conversations?limit=50&cursor=abc");
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).cache).toBe("no-store");
  });

  it("crea el alcance correcto y no envía ids ajenos al modo", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      json(
        {
          id: "c1",
          mode: "class",
          subject_id: null,
          audio_id: "a1",
          title: null,
          created_at: "",
          updated_at: "",
          last_message_at: null,
        },
        201,
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await createConversation({ mode: "class", audioId: "a1", subjectId: "ignored" });
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ mode: "class", audio_id: "a1" });
  });

  it("carga historial y borra conversación con el método correcto", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(json({ items: [], next_cursor: null }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await fetchConversationMessages("c1");
    await deleteConversation("c1");
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/chat/conversations/c1/messages?limit=50");
    expect((fetchMock.mock.calls[1]?.[1] as RequestInit).method).toBe("DELETE");
  });

  it("mapea errores de límite y capacidad", () => {
    expect(
      chatActionError(new ApiHttpError(429, { code: "rate_limited", message: "Límite." })),
    ).toContain("demasiados streams");
    expect(
      chatActionError(new ApiHttpError(503, { code: "capacity_unavailable", message: "Ocupado." })),
    ).toContain("ocupado");
  });
});

describe("sendChatMessage", () => {
  it("consume el SSE, manda CSRF y devuelve terminal", async () => {
    setCsrfToken("csrf-chat");
    const sse = [
      'event: snapshot\nid: 0\ndata: {"message_id":"m1","status":"streaming"}\n\n',
      'event: delta\nid: 3\ndata: {"text":"hola"}\n\n',
      'event: done\nid: 4\ndata: {"message_id":"m1","conversation_id":"c1","status":"completed","sources":[]}\n\n',
    ].join("");
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(sse, { status: 200, headers: { "content-type": "text/event-stream" } }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const events: ChatStreamEvent[] = [];
    const controller = new AbortController();

    const terminal = await sendChatMessage({
      conversationId: "c1",
      content: "pregunta",
      clientMessageId: "client-1",
      signal: controller.signal,
      lastEventId: "0",
      onEvent: (event) => events.push(event),
    });

    expect(terminal).toBe(true);
    expect(events.map((event) => event.type)).toEqual(["snapshot", "delta", "done"]);
    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect((fetchMock.mock.calls[0]?.[0] as string)).toBe(
      "/chat/conversations/c1/messages",
    );
    expect(init.method).toBe("POST");
    expect(new Headers(init.headers).get("X-CSRF-Token")).toBe("csrf-chat");
    expect(new Headers(init.headers).get("Last-Event-ID")).toBe("0");
    expect(JSON.parse(String(init.body))).toEqual({
      content: "pregunta",
      client_message_id: "client-1",
    });
  });

  it("devuelve false si el stream corta sin terminal", async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(
        'event: snapshot\nid: 0\ndata: {"message_id":"m1","status":"streaming"}\n\n' +
          'event: delta\nid: 3\ndata: {"text":"parcial"}\n\n',
        { status: 200, headers: { "content-type": "text/event-stream" } },
      ),
    );
    vi.stubGlobal("fetch", fetchMock);

    await expect(
      sendChatMessage({
        conversationId: "c1",
        content: "pregunta",
        clientMessageId: "client-1",
        signal: new AbortController().signal,
        onEvent: () => undefined,
      }),
    ).resolves.toBe(false);
  });
});
