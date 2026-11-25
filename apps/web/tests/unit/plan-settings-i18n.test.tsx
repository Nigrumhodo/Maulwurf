// @vitest-environment jsdom
//
// W4 — planning, dashboard and settings views follow the UI language; Spanish stays the
// default and Settings links to the public legal pages.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { GoogleReconnect } from "@/components/calendar/GoogleReconnect";
import { AccessTiles } from "@/components/dashboard/AccessTiles";
import { statusLine } from "@/components/dashboard/summary";
import { SubjectForm } from "@/components/subjects/SubjectForm";
import { TaskCard } from "@/components/tasks/TaskCard";
import { applyLocale } from "@/i18n";
import type { AudioListItemOut } from "@/lib/audios";
import { calendarErrorCopy, syncStatusDescription } from "@/lib/integrations";
import { notificationStatusExplanation } from "@/lib/notifications";
import { validateThresholds, weekdayLabels } from "@/lib/preferences";
import { qualitySummary, taskStatusFilters } from "@/lib/tasks";
import type { Task } from "@/lib/tasks";
import { SettingsPage } from "@/pages/SettingsPage";

vi.mock("@/components/auth/AuthProvider", () => ({
  useAuth: () => ({
    user: { email: "ana@example.test", timezone: "America/Bogota" },
    loading: false,
    guest: false,
    enterAsGuest: () => {},
    logout: async () => {},
    retrySession: async () => {},
  }),
}));

function json(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function installFetch() {
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/integrations/status") {
        return json({
          google: {
            status: "disconnected",
            scopes: [],
            calendar_id: null,
            calendar_write_ready: false,
            token_expires_at: null,
            last_refresh_at: null,
          },
        });
      }
      if (url === "/me/notifications") {
        return json({
          gmail_opt_in: false,
          digest_daily_enabled: true,
          digest_daily_time: "07:00",
          digest_weekly_enabled: true,
          digest_weekday: 0,
          reminder_offsets_hours: [48],
          reminder_allday_offsets_days: [1],
          updated_at: null,
        });
      }
      if (url.startsWith("/notifications?")) return json({ items: [], next_cursor: null });
      return json({});
    }),
  );
}

function task(overrides: Partial<Task> = {}): Task {
  return {
    id: "t-1",
    audio_id: null,
    subject: null,
    source: "manual",
    tipo: "tarea",
    titulo: "Entregar taller",
    detalle: null,
    due_date: null,
    due_at: null,
    timezone: "America/Bogota",
    all_day: false,
    date_status: "missing",
    date_text: null,
    date_reason: null,
    confidence_score: null,
    status: "needs_review",
    calendar_sync_status: "not_requested",
    calendar_id: null,
    calendar_event_id: null,
    version: 1,
    due_version: 1,
    event_block: {
      start_at: null,
      end_at: null,
      start_date: null,
      end_date_exclusive: null,
      duration_default_editable: true,
    },
    sources: [],
    evidence: [],
    pending_revision: null,
    ...overrides,
  };
}

function audio(status: string): AudioListItemOut {
  return { aggregate_status: status } as AudioListItemOut;
}

afterEach(async () => {
  cleanup();
  vi.unstubAllGlobals();
  await applyLocale("es");
});

describe("dashboard and subjects follow the UI language", () => {
  it("translates the dashboard shortcuts and the status line", async () => {
    await applyLocale("en");
    render(
      <MemoryRouter>
        <AccessTiles />
      </MemoryRouter>,
    );
    const nav = screen.getByRole("navigation", { name: "main shortcuts" });
    expect(within(nav).getByText("upload class")).toBeTruthy();
    expect(statusLine([audio("ready"), audio("ready")], [])).toBe("2 classes ready");

    await applyLocale("fr");
    expect(statusLine([audio("ready")], [])).toBe("1 cours prêt");
    expect(statusLine([], [])).toBe("pas encore de cours ni de tâches");
  });

  it("translates the subject form", async () => {
    await applyLocale("fr");
    render(<SubjectForm submitLabel="ok" onSubmit={async () => {}} />);
    expect(screen.getByLabelText(/professeur \(facultatif\)/)).toBeTruthy();
  });
});

describe("tasks and agenda follow the UI language", () => {
  it("translates the task card and the status filters", async () => {
    await applyLocale("en");
    render(
      <MemoryRouter>
        <TaskCard task={task()} quality={null} onReview={() => {}} onEdit={() => {}} />
      </MemoryRouter>,
    );
    expect(screen.getByText("date not resolved")).toBeTruthy();
    expect(screen.getByRole("button", { name: "review" })).toBeTruthy();
    expect(taskStatusFilters().map((filter) => filter.label)).toContain("to confirm");
    // Contract literals stay verbatim.
    expect(screen.getByText("needs_review")).toBeTruthy();
  });

  it("resolves lib maps at call time", async () => {
    expect(syncStatusDescription("synced")).toBe("evento escrito en el calendario Maulwurf.");
    await applyLocale("fr");
    expect(syncStatusDescription("synced")).toBe("événement écrit dans le calendrier Maulwurf.");
    expect(calendarErrorCopy("nope")).toBe("Impossible de connecter Google Calendar.");
    expect(
      qualitySummary({ code: "low_confidence", label: "x", count: 2, total: 5 }),
    ).toBe("x (2/5)");
    await applyLocale("en");
    expect(calendarErrorCopy("access_denied")).toBe("You did not authorize access to Calendar.");
  });

  it("translates the Google reconnect panel", async () => {
    await applyLocale("en");
    render(
      <GoogleReconnect
        google={{
          status: "disconnected",
          scopes: [],
          calendar_id: null,
          calendar_write_ready: false,
          token_expires_at: null,
          last_refresh_at: null,
        }}
      />,
    );
    expect(screen.getByText("no write ready")).toBeTruthy();
    expect(screen.getByRole("link", { name: "connect google calendar" })).toBeTruthy();
  });
});

describe("settings follow the UI language", () => {
  it("translates the notification and preference messages", async () => {
    await applyLocale("en");
    expect(weekdayLabels()[1]).toBe("monday");
    expect(validateThresholds([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11], "days")).toBe(
      "at most 10 days thresholds.",
    );
    expect(notificationStatusExplanation("failed", "x_err")).toBe(
      "could not send (x_err); the system does not retry automatically.",
    );
  });

  it("renders the settings page in French", async () => {
    installFetch();
    await applyLocale("fr");
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    );
    expect(screen.getByRole("heading", { level: 1, name: "réglages" })).toBeTruthy();
    expect(screen.getByRole("heading", { level: 2, name: "confidentialité et légal" })).toBeTruthy();
    expect(screen.getByRole("navigation", { name: "sections des réglages" })).toBeTruthy();
  });

  it("links the legal section to the public pages and the account section", async () => {
    installFetch();
    render(
      <MemoryRouter>
        <SettingsPage />
      </MemoryRouter>,
    );
    const section = document.getElementById("privacidad");
    expect(section).not.toBeNull();
    const scope = within(section as HTMLElement);
    expect(scope.getByRole("link", { name: "privacidad" }).getAttribute("href")).toBe(
      "/privacidad",
    );
    expect(scope.getByRole("link", { name: "términos" }).getAttribute("href")).toBe("/terminos");
    expect(scope.getByRole("link", { name: "cuenta" }).getAttribute("href")).toBe("#cuenta");
    expect(scope.getByText(/30 días/)).toBeTruthy();
    expect(screen.getByRole("link", { name: "privacidad y legal" }).getAttribute("href")).toBe(
      "#privacidad",
    );
  });
});
