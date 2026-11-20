import { afterEach, describe, expect, it, vi } from "vitest";
import { ApiHttpError } from "@/lib/api";
import {
  buildPreferencesPatch,
  fetchNotificationPreferences,
  isPreferencesDirty,
  normalizeThresholds,
  notificationTestErrorMessage,
  preferencesErrorMessage,
  sendTestNotification,
  updateNotificationPreferences,
  validatePreferencesDraft,
  validateThresholds,
  weekdayLabel,
} from "@/lib/preferences";
import type {
  NotificationPreferences,
  NotificationPreferencesDraft,
} from "@/lib/preferences";

function preferences(
  overrides: Partial<NotificationPreferences> = {},
): NotificationPreferences {
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

function draft(
  overrides: Partial<NotificationPreferencesDraft> = {},
): NotificationPreferencesDraft {
  const base = preferences();
  return {
    gmail_opt_in: base.gmail_opt_in,
    digest_daily_enabled: base.digest_daily_enabled,
    digest_daily_time: base.digest_daily_time,
    digest_weekly_enabled: base.digest_weekly_enabled,
    digest_weekday: base.digest_weekday,
    reminder_offsets_hours: base.reminder_offsets_hours,
    reminder_allday_offsets_days: base.reminder_allday_offsets_days,
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
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("weekdayLabel", () => {
  it("uses the frozen 0 = Sunday vocabulary", () => {
    expect(weekdayLabel(0)).toBe("domingo");
    expect(weekdayLabel(1)).toBe("lunes");
    expect(weekdayLabel(6)).toBe("sábado");
  });
});

describe("thresholds", () => {
  it("normalizes descending and unique", () => {
    expect(normalizeThresholds([2, 48, 24, 2])).toEqual([48, 24, 2]);
  });

  it("rejects out-of-range and non-integer values", () => {
    expect(validateThresholds([0], "hours")).toContain("entre 1 y 8784");
    expect(validateThresholds([1.5], "days")).toContain("entero");
    expect(validateThresholds([367], "days")).toContain("entre 1 y 366");
  });

  it("caps the list at ten thresholds", () => {
    expect(validateThresholds([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11], "days")).toContain(
      "máximo 10",
    );
  });

  it("allows an empty list", () => {
    expect(validateThresholds([], "hours")).toBeNull();
  });
});

describe("validatePreferencesDraft", () => {
  it("accepts the stored defaults", () => {
    expect(validatePreferencesDraft(draft())).toBeNull();
  });

  it("rejects an invalid digest time", () => {
    expect(validatePreferencesDraft(draft({ digest_daily_time: "24:00" }))).toContain(
      "hora válida",
    );
    expect(validatePreferencesDraft(draft({ digest_daily_time: "" }))).toContain(
      "hora válida",
    );
  });

  it("rejects a weekday outside 0..6", () => {
    expect(validatePreferencesDraft(draft({ digest_weekday: 7 }))).toContain(
      "día válido",
    );
  });
});

describe("buildPreferencesPatch", () => {
  it("sends only changed fields and normalizes thresholds", () => {
    const current = draft();
    const next = draft({
      gmail_opt_in: true,
      digest_daily_time: "08:30",
      reminder_offsets_hours: [2, 48],
    });

    expect(buildPreferencesPatch(current, next)).toEqual({
      gmail_opt_in: true,
      digest_daily_time: "08:30",
      reminder_offsets_hours: [48, 2],
    });
  });

  it("returns an empty patch when nothing changed", () => {
    expect(buildPreferencesPatch(draft(), draft())).toEqual({});
    expect(isPreferencesDirty(draft(), draft())).toBe(false);
    expect(isPreferencesDirty(draft(), draft({ gmail_opt_in: true }))).toBe(true);
  });
});

describe("notification endpoints", () => {
  it("reads the complete preferences from /me/notifications", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(preferences()));
    vi.stubGlobal("fetch", fetchMock);

    await expect(fetchNotificationPreferences()).resolves.toEqual(preferences());
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/me/notifications");
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).method).toBeUndefined();
  });

  it("patches only the given fields", async () => {
    const fetchMock = vi.fn().mockResolvedValue(json(preferences({ gmail_opt_in: true })));
    vi.stubGlobal("fetch", fetchMock);

    await updateNotificationPreferences({ gmail_opt_in: true });

    const init = fetchMock.mock.calls[0]?.[1] as RequestInit;
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/me/notifications");
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({ gmail_opt_in: true });
  });

  it("posts the audited test occurrence", async () => {
    const result = {
      id: "notification-1",
      type: "test",
      status: "pending",
      scheduled_at: "2026-11-16T12:00:00Z",
      warning: {
        code: "delivery_uncertain",
        message: "Gmail no garantiza exactamente un envío.",
      },
    };
    const fetchMock = vi.fn().mockResolvedValue(json(result, 202));
    vi.stubGlobal("fetch", fetchMock);

    await expect(sendTestNotification()).resolves.toEqual(result);
    expect(fetchMock.mock.calls[0]?.[0]).toBe("/notifications/test");
    expect((fetchMock.mock.calls[0]?.[1] as RequestInit).method).toBe("POST");
  });

  it("rejects malformed preference and test responses", async () => {
    vi.stubGlobal("fetch", vi.fn(async () => json({ unexpected: true })));
    await expect(fetchNotificationPreferences()).rejects.toThrow("contrato");
    await expect(updateNotificationPreferences({ gmail_opt_in: true })).rejects.toThrow("contrato");
    await expect(sendTestNotification()).rejects.toThrow("contrato");
  });
});

describe("error copy", () => {
  it("explains the closed test reasons", () => {
    expect(
      notificationTestErrorMessage(
        new ApiHttpError(409, {
          code: "notifications_test_unavailable",
          details: { reason: "gmail_not_opted_in" },
        }),
      ),
    ).toContain("activa el opt-in");
    expect(
      notificationTestErrorMessage(
        new ApiHttpError(409, {
          code: "notifications_test_unavailable",
          details: { reason: "recipient_unverified" },
        }),
      ),
    ).toContain("no está verificado");
  });

  it("does not echo an unknown reason", () => {
    const message = notificationTestErrorMessage(
      new ApiHttpError(409, {
        code: "notifications_test_unavailable",
        details: { reason: "<script>" },
      }),
    );
    expect(message).toContain("no está disponible");
    expect(message).not.toContain("<script>");
  });

  it("maps CSRF and network failures without leaking details", () => {
    expect(preferencesErrorMessage(new ApiHttpError(403, null))).toContain(
      "sesión no es válida",
    );
    expect(preferencesErrorMessage(new Error("boom"))).toContain("revisa la conexión");
  });
});
