// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { ChatPage } from "@/pages/ChatPage";

function conversation(id: string, title: string) {
  return {
    id,
    mode: "all",
    subject_id: null,
    audio_id: null,
    title,
    created_at: "2026-11-02T12:00:00Z",
    updated_at: "2026-11-02T12:00:00Z",
    last_message_at: null,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ChatWorkspace delete", () => {
  it("loads the history of the conversation selected after deleting the active one", async () => {
    const message = {
      id: "m2",
      role: "user",
      content: "mensaje de la segunda conversación",
      status: "completed",
      client_message_id: null,
      citas: [],
      tool_calls: [],
      created_at: "2026-11-02T12:01:00Z",
    };
    vi.stubGlobal(
      "fetch",
      vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
        const url = String(input);
        const method = (init?.method ?? "GET").toUpperCase();
        if (url === "/chat/conversations?limit=50") {
          return json({
            items: [conversation("c1", "primera"), conversation("c2", "segunda")],
            next_cursor: null,
          });
        }
        if (url === "/chat/conversations/c2/messages?limit=50") {
          return json({ items: [message], next_cursor: null });
        }
        if (method === "DELETE") return new Response(null, { status: 204 });
        if (url.includes("/messages")) return json({ items: [], next_cursor: null });
        if (url === "/subjects" || url.startsWith("/audios")) return json({ items: [] });
        return json({});
      }),
    );
    render(
      <MemoryRouter initialEntries={["/gestion/chat"]}>
        <ChatPage />
      </MemoryRouter>,
    );
    fireEvent.click(await screen.findByRole("button", { name: /borrar conversación primera/ }));
    fireEvent.click(await screen.findByRole("button", { name: "borrar" }));
    await waitFor(() => {
      expect(screen.getByText("mensaje de la segunda conversación")).toBeTruthy();
    });
  });
});
