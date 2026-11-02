// @vitest-environment jsdom
//
// D2.6 — C-S2-DA-05: chat académico, panel general, streaming y recuperación por historial.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ChatPage } from "@/pages/ChatPage";
import { ChatPanel } from "@/components/chat/ChatPanel";
import type { ChatConversation, ChatMessage } from "@/lib/chat";

const CONVERSATION: ChatConversation = {
  id: "c1",
  mode: "all",
  subject_id: null,
  audio_id: null,
  title: null,
  created_at: "2026-11-02T12:00:00Z",
  updated_at: "2026-11-02T12:00:00Z",
  last_message_at: null,
};

const USER_MESSAGE: ChatMessage = {
  id: "u1",
  role: "user",
  content: "pregunta persistida",
  status: "completed",
  client_message_id: null,
  citas: [],
  created_at: "2026-11-02T12:01:00Z",
};

const ASSISTANT_MESSAGE: ChatMessage = {
  id: "m1",
  role: "assistant",
  content: "parcial persistido",
  status: "cancelled",
  client_message_id: "client-1",
  citas: [],
  created_at: "2026-11-02T12:01:01Z",
};

const SSE_HAPPY = [
  'event: snapshot\nid: 0\ndata: {"message_id":"m1","status":"streaming"}\n\n',
  'event: delta\nid: 15\ndata: {"text":"respuesta sintética"}\n\n',
  'event: citation\nid: 15\ndata: {"index":1,"audio_id":"a1","transcript_version":1,"chunk_id":"ch1","segment_id":"s1","t_start":0,"t_end":5,"label":"clase · 0:05","url":"/biblioteca/a1?segment=s1"}\n\n',
  'event: done\nid: 16\ndata: {"message_id":"m1","conversation_id":"c1","status":"completed","sources":[]}\n\n',
].join("");

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function sse(body: string): Response {
  return new Response(body, {
    status: 200,
    headers: { "content-type": "text/event-stream" },
  });
}

function installChatFetch(options: {
  sseBody?: string;
  hang?: boolean;
  histories?: Array<{ items: ChatMessage[]; next_cursor: string | null }>;
}) {
  const calls: Array<{ url: string; init: RequestInit }> = [];
  let historyIndex = 0;
  const histories = options.histories ?? [{ items: [], next_cursor: null }];
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = (init?.method ?? "GET").toUpperCase();
    calls.push({ url, init: init ?? {} });
    if (method === "GET" && url === "/chat/conversations?limit=50") {
      return json({ items: [CONVERSATION], next_cursor: null });
    }
    if (method === "GET" && url === "/chat/conversations/c1/messages?limit=50") {
      const page = histories[Math.min(historyIndex, histories.length - 1)];
      historyIndex += 1;
      return json(page);
    }
    if (method === "GET" && url === "/subjects") return json({ items: [] });
    if (method === "GET" && url === "/audios?limit=100") return json({ items: [] });
    if (method === "POST" && url === "/chat/conversations/c1/messages") {
      if (options.hang === true) {
        const encoder = new TextEncoder();
        return new Response(
          new ReadableStream({
            start(controller) {
              controller.enqueue(
                encoder.encode(
                  'event: snapshot\nid: 0\ndata: {"message_id":"m1","status":"streaming"}\n\n' +
                    'event: delta\nid: 15\ndata: {"text":"parcial en vivo"}\n\n',
                ),
              );
              init?.signal?.addEventListener("abort", () => {
                controller.error(new DOMException("Aborted", "AbortError"));
              });
            },
          }),
          { status: 200, headers: { "content-type": "text/event-stream" } },
        );
      }
      return sse(options.sseBody ?? SSE_HAPPY);
    }
    if (
      method === "POST" &&
      url === "/chat/conversations/c1/messages/m1/cancel"
    ) {
      return json({ message_id: "m1", status: "cancelled" }, 202);
    }
    return json({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return { calls, fetchMock };
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function waitForComposer() {
  await waitFor(() => {
    expect((screen.getByLabelText("pregunta") as HTMLTextAreaElement).disabled).toBe(false);
  });
}

function sendQuestion(text = "¿qué es una integral?") {
  fireEvent.change(screen.getByLabelText("pregunta"), { target: { value: text } });
  fireEvent.click(screen.getByRole("button", { name: "enviar" }));
}

describe("ChatPage", () => {
  it("envía, renderiza snapshot/delta/citation/done y conserva el client id", async () => {
    const { calls } = installChatFetch({});
    render(
      <MemoryRouter initialEntries={["/gestion/chat"]}>
        <ChatPage />
      </MemoryRouter>,
    );
    await waitForComposer();
    expect(screen.getByLabelText("alcance de la conversación")).toBeTruthy();

    sendQuestion();

    expect(await screen.findByText("respuesta sintética")).toBeTruthy();
    expect(screen.getByText("[1] clase · 0:05")).toBeTruthy();
    expect(screen.getByText("completed")).toBeTruthy();
    const post = calls.find(
      (call) =>
        call.url === "/chat/conversations/c1/messages" &&
        (call.init.method ?? "GET") === "POST",
    );
    const body = JSON.parse(String(post?.init.body)) as {
      content: string;
      client_message_id: string;
    };
    expect(body.content).toBe("¿qué es una integral?");
    expect(body.client_message_id).toMatch(/[a-z0-9-]{8,}/i);
  });

  it("ofrece alcance materia/clase en gestión, pero no en el panel general", async () => {
    installChatFetch({});
    const { unmount } = render(
      <MemoryRouter initialEntries={["/gestion/chat"]}>
        <ChatPage />
      </MemoryRouter>,
    );
    await waitForComposer();
    const scope = screen.getByLabelText("alcance de la conversación");
    fireEvent.change(scope, { target: { value: "subject" } });
    expect(screen.getByLabelText("materia de la conversación")).toBeTruthy();
    unmount();

    installChatFetch({});
    render(
      <MemoryRouter>
        <ChatPanel onClose={vi.fn()} />
      </MemoryRouter>,
    );
    expect(await screen.findByText(/chat general · todas las clases/)).toBeTruthy();
    expect(screen.queryByLabelText("alcance de la conversación")).toBeNull();
    expect(screen.getByRole("button", { name: "nueva" })).toBeTruthy();
  });

  it("cancela el stream, conserva el parcial y refresca el historial", async () => {
    const { calls } = installChatFetch({
      hang: true,
      histories: [
        { items: [], next_cursor: null },
        { items: [USER_MESSAGE, ASSISTANT_MESSAGE], next_cursor: null },
      ],
    });
    render(
      <MemoryRouter initialEntries={["/gestion/chat"]}>
        <ChatPage />
      </MemoryRouter>,
    );
    await waitForComposer();

    sendQuestion("pregunta persistida");
    expect(await screen.findByText("parcial en vivo")).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "cancelar" }));

    expect(await screen.findByText("parcial persistido")).toBeTruthy();
    expect(screen.getByText("cancelled")).toBeTruthy();
    expect(
      calls.some(
        (call) =>
          call.url === "/chat/conversations/c1/messages/m1/cancel" &&
          (call.init.method ?? "GET") === "POST",
      ),
    ).toBe(true);
  });

  it("un EOF sin terminal recupera el historial persistido sin duplicar la pregunta", async () => {
    const { calls } = installChatFetch({
      sseBody:
        'event: snapshot\nid: 0\ndata: {"message_id":"m1","status":"streaming"}\n\n' +
        'event: delta\nid: 15\ndata: {"text":"parcial en vivo"}\n\n',
      histories: [
        { items: [], next_cursor: null },
        { items: [USER_MESSAGE, ASSISTANT_MESSAGE], next_cursor: null },
      ],
    });
    render(
      <MemoryRouter initialEntries={["/gestion/chat"]}>
        <ChatPage />
      </MemoryRouter>,
    );
    await waitForComposer();

    sendQuestion("pregunta persistida");

    expect(await screen.findByText("parcial persistido")).toBeTruthy();
    expect(screen.queryByText("parcial en vivo")).toBeNull();
    expect(screen.getAllByText("pregunta persistida")).toHaveLength(1);
    expect(screen.getByText("cancelled")).toBeTruthy();
    expect(calls.filter((call) => call.url === "/chat/conversations/c1/messages")).toHaveLength(1);
  });
});
