// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { NotificationPreferences } from "@/components/settings/NotificationPreferences";
import type { NotificationPreferences as Preferences } from "@/lib/preferences";

vi.mock("@/components/auth/AuthProvider", () => ({
  useAuth: () => ({
    user: { email: "ana@example.test", timezone: "America/Bogota" },
    loading: false,
    guest: false,
    enterAsGuest: () => {},
    logout: async () => {},
  }),
}));

function preferences(overrides: Partial<Preferences> = {}): Preferences {
  return {
    gmail_opt_in: false,
    digest_daily_enabled: true,
    digest_daily_time: "07:00",
    digest_weekly_enabled: true,
    digest_weekday: 0,
    reminder_offsets_hours: [48, 24, 2],
    reminder_allday_offsets_days: [3, 1],
    updated_at: "2026-11-16T12:00:00Z",
    ...overrides,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

interface FetchOptions {
  get?: () => Response;
  patch?: (body: Record<string, unknown>) => Response;
  test?: () => Response;
}

function installFetch(
  initial: Preferences = preferences(),
  options: FetchOptions = {},
): ReturnType<typeof vi.fn> {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    if (url === "/me/notifications" && (init?.method ?? "GET") === "GET") {
      return options.get?.() ?? json(initial);
    }
    if (url === "/me/notifications" && init?.method === "PATCH") {
      const body = JSON.parse(String(init.body)) as Record<string, unknown>;
      return options.patch?.(body) ?? json({ ...initial, ...body });
    }
    if (url === "/notifications/test") {
      return options.test?.() ?? json({}, 202);
    }
    return json({});
  });
  vi.stubGlobal("fetch", fetchMock);
  return fetchMock;
}

async function renderLoaded() {
  render(<NotificationPreferences />);
  await screen.findByText("activar el opt-in de Gmail");
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("NotificationPreferences", () => {
  it("renders the stored preferences, including the 0 = Sunday weekday", async () => {
    installFetch();
    await renderLoaded();

    expect((screen.getByLabelText(/umbral 1 \(horas\)/) as HTMLInputElement).value).toBe(
      "48",
    );
    expect(
      (screen.getByLabelText(/umbral 1 \(días\)/) as HTMLInputElement).value,
    ).toBe("3");
    expect((screen.getByLabelText(/hora local/) as HTMLInputElement).value).toBe("07:00");
    expect(
      (screen.getByLabelText("día del resumen semanal") as HTMLSelectElement).value,
    ).toBe("0");
    expect(
      (screen.getByLabelText(/activar el opt-in de Gmail/i) as HTMLInputElement).checked,
    ).toBe(false);
    expect(screen.getByText(/ana@example.test/)).toBeTruthy();
  });

  it("saves only the changed fields as a partial PATCH", async () => {
    const fetchMock = installFetch();
    await renderLoaded();

    fireEvent.click(screen.getByLabelText(/activar el opt-in de Gmail/i));
    const timeInput = screen.getByLabelText(/hora local/) as HTMLInputElement;
    fireEvent.change(timeInput, { target: { value: "08:30" } });
    expect(timeInput.value).toBe("08:30");
    const saveButton = screen.getByRole("button", {
      name: "guardar preferencias",
    }) as HTMLButtonElement;
    expect(saveButton.disabled).toBe(false);
    fireEvent.click(saveButton);

    expect(await screen.findByText(/preferencias guardadas\./)).toBeTruthy();
    const patchCall = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "PATCH",
    );
    expect(patchCall?.[0]).toBe("/me/notifications");
    expect(JSON.parse(String((patchCall?.[1] as RequestInit).body))).toEqual({
      gmail_opt_in: true,
      digest_daily_time: "08:30",
    });
  });

  it("surfaces a validation error from the API", async () => {
    installFetch(preferences(), {
      patch: () =>
        json(
          {
            error: {
              code: "validation_failed",
              message: "preferencia inválida",
              details: { fields: { digest_weekday: "elige un día válido" } },
            },
          },
          422,
        ),
    });
    await renderLoaded();

    fireEvent.change(screen.getByLabelText("día del resumen semanal"), {
      target: { value: "3" },
    });
    fireEvent.click(screen.getByRole("button", { name: "guardar preferencias" }));

    expect(await screen.findByText(/elige un día válido/)).toBeTruthy();
  });

  it("rejects an invalid threshold before calling the API", async () => {
    const fetchMock = installFetch();
    await renderLoaded();

    fireEvent.change(screen.getByLabelText(/umbral 1 \(horas\)/), {
      target: { value: "0" },
    });
    fireEvent.click(screen.getByRole("button", { name: "guardar preferencias" }));

    expect(await screen.findByText(/debe ser un entero entre 1 y 8784/)).toBeTruthy();
    expect(
      fetchMock.mock.calls.some(
        ([, init]) => (init as RequestInit | undefined)?.method === "PATCH",
      ),
    ).toBe(false);
  });

  it("retries a failed preferences read", async () => {
    let reads = 0;
    installFetch(preferences(), {
      get: () => {
        reads += 1;
        return reads === 1
          ? json({ error: { code: "internal_error" } }, 500)
          : json(preferences());
      },
    });

    render(<NotificationPreferences />);
    expect(await screen.findByText(/no se pudieron cargar las preferencias/)).toBeTruthy();

    fireEvent.click(screen.getByRole("button", { name: "reintentar" }));

    expect(await screen.findByText("activar el opt-in de Gmail")).toBeTruthy();
  });

  it("disables the test send until the opt-in is saved", async () => {
    installFetch(preferences({ gmail_opt_in: false }));
    await renderLoaded();

    const button = screen.getByRole("button", { name: "enviar prueba" }) as HTMLButtonElement;
    expect(button.disabled).toBe(true);
    expect(screen.getByText(/activa el opt-in y guarda/)).toBeTruthy();
  });

  it("explains a test send blocked by an unverified recipient", async () => {
    installFetch(preferences({ gmail_opt_in: true }), {
      test: () =>
        json(
          {
            error: {
              code: "notifications_test_unavailable",
              message: "El envío de prueba no está disponible en el estado actual.",
              details: { reason: "recipient_unverified" },
            },
          },
          409,
        ),
    });
    await renderLoaded();

    fireEvent.click(screen.getByRole("button", { name: "enviar prueba" }));

    expect(await screen.findByText(/no está verificado/)).toBeTruthy();
  });

  it("shows the delivery warning of a recorded test occurrence", async () => {
    installFetch(preferences({ gmail_opt_in: true }), {
      test: () =>
        json(
          {
            id: "notification-1",
            type: "test",
            status: "pending",
            scheduled_at: "2026-11-16T12:00:00Z",
            warning: {
              code: "delivery_uncertain",
              message: "Gmail no garantiza exactamente un envío.",
            },
          },
          202,
        ),
    });
    await renderLoaded();

    fireEvent.click(screen.getByRole("button", { name: "enviar prueba" }));

    expect(
      await screen.findByText(/prueba registrada; Gmail no garantiza exactamente un envío/),
    ).toBeTruthy();
  });
});
