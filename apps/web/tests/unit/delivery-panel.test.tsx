// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { DeliveryPanel } from "@/components/settings/DeliveryPanel";
import type { NotificationItem } from "@/lib/notifications";

function item(overrides: Partial<NotificationItem> = {}): NotificationItem {
  return {
    id: "n-1",
    type: "reminder_t24",
    status: "delivery_unknown",
    task_id: "t-1",
    occurrence: "2026-11-20",
    due_version: 1,
    occurrence_seq: 0,
    scheduled_at: "2026-11-19T12:00:00Z",
    attempts: 1,
    sent_at: null,
    last_error_code: "response_lost",
    created_at: "2026-11-19T11:55:00Z",
    updated_at: "2026-11-19T12:00:10Z",
    ...overrides,
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
  vi.restoreAllMocks();
});

describe("DeliveryPanel (D4.2)", () => {
  it("lists notifications, explains delivery_unknown, and supports status filters + cursor pagination", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("cursor=c2")) {
        return json({
          items: [item({ id: "n-2", status: "sent", sent_at: "2026-11-19T12:01:00Z", last_error_code: null })],
          next_cursor: null,
        });
      }
      if (url.includes("status=sent")) {
        return json({
          items: [item({ id: "n-sent", status: "sent", sent_at: "2026-11-19T12:01:00Z", last_error_code: null })],
          next_cursor: null,
        });
      }
      return json({
        items: [item()],
        next_cursor: "c2",
      });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<DeliveryPanel />);

    expect(await screen.findByText(/estado terminal: gmail no confirma entrega única/i)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "cargar más" }));
    expect(await screen.findByText(/aceptada por gmail/i)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "sent" }));
    await waitFor(() =>
      expect(
        fetchMock.mock.calls.some(([url]) => String(url).includes("status=sent")),
      ).toBe(true),
    );
  });

  it("requires explicit duplicate-risk acknowledgement before POST /notifications/{id}/resend", async () => {
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url === "/notifications/n-1/resend" && init?.method === "POST") {
        return json(
          {
            ...item({ id: "n-seq1", status: "pending", occurrence_seq: 1, attempts: 0 }),
            warning: {
              code: "duplicate_risk_acknowledged",
              message: "Se creó una ocurrencia nueva y auditada; Gmail podría entregar un duplicado.",
            },
          },
          202,
        );
      }
      return json({ items: [item()], next_cursor: null });
    });
    vi.stubGlobal("fetch", fetchMock);

    render(<DeliveryPanel />);

    const resendBtn = await screen.findByRole("button", { name: "reenviar manualmente" });
    fireEvent.click(resendBtn);

    const confirmBtn = screen.getByRole("button", { name: "crear nuevo envío" }) as HTMLButtonElement;
    expect(confirmBtn.disabled).toBe(true);

    const checkbox = screen.getByRole("checkbox");
    fireEvent.click(checkbox);
    expect(confirmBtn.disabled).toBe(false);

    fireEvent.click(confirmBtn);

    expect(
      await screen.findByText(/Se creó una ocurrencia nueva y auditada/i),
    ).toBeTruthy();

    const resendCall = fetchMock.mock.calls.find(
      ([url, init]) => String(url) === "/notifications/n-1/resend" && init?.method === "POST",
    );
    expect(JSON.parse(String(resendCall?.[1]?.body))).toEqual({
      acknowledge_duplicate_risk: true,
    });
  });
});
