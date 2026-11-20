// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { AccountDeletionPanel } from "@/components/settings/AccountDeletionPanel";

const logoutMock = vi.fn(async () => {});

vi.mock("@/components/auth/AuthProvider", () => ({
  useAuth: () => ({
    user: {
      id: "u-1",
      email: "ana@example.test",
      display_name: "Ana",
      timezone: "America/Bogota",
      csrf_token: "tok",
    },
    loading: false,
    unavailable: false,
    retrySession: async () => {},
    logout: logoutMock,
  }),
}));

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  logoutMock.mockClear();
});

describe("AccountDeletionPanel (D4.2 / A4.4)", () => {
  it("defaults delete_maulwurf_events=false, enforces typed ELIMINAR, and allows resuming pending deletions", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (url === "/me" && method === "DELETE") {
        return json({ deletion_operation_id: "del-123" }, 202);
      }
      if (url === "/me/deletion/del-123" && method === "GET") {
        return json({
          id: "del-123",
          status: "pending",
          delete_maulwurf_events: false,
          created_at: "2026-11-19T12:00:00Z",
          completed_at: null,
          expires_at: "2026-12-19T12:00:00Z",
          error_summary: "purge_interrupted",
        });
      }
      if (url === "/me/deletion/del-123/resume" && method === "POST") {
        return json({
          id: "del-123",
          status: "completed",
          delete_maulwurf_events: false,
          created_at: "2026-11-19T12:00:00Z",
          completed_at: "2026-11-19T12:00:10Z",
          expires_at: "2026-12-19T12:00:00Z",
          error_summary: null,
        });
      }
      return json({});
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<AccountDeletionPanel />);

    const calendarCheckbox = screen.getByRole("checkbox") as HTMLInputElement;
    expect(calendarCheckbox.checked).toBe(false);

    fireEvent.click(screen.getByRole("button", { name: /solicitar eliminación de cuenta/i }));

    const confirmBtn = screen.getByRole("button", { name: "eliminar cuenta" }) as HTMLButtonElement;
    expect(confirmBtn.disabled).toBe(true);

    fireEvent.change(screen.getByLabelText(/escribe ELIMINAR/i), {
      target: { value: "ELIMINAR" },
    });
    expect(confirmBtn.disabled).toBe(false);
    fireEvent.click(confirmBtn);

    expect(await screen.findByText(/operación del-123/i)).toBeTruthy();

    const deleteCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === "/me" && init?.method === "DELETE",
    );
    expect(JSON.parse(String(deleteCall?.[1]?.body))).toEqual({
      delete_maulwurf_events: false,
    });

    fireEvent.click(screen.getByRole("button", { name: /reanudar borrado/i }));

    await waitFor(() => expect(screen.getByText("completed")).toBeTruthy());
  });
});
