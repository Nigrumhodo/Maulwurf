import { describe, expect, it } from "vitest";
import { buildScheduleFields } from "@/lib/tasks";
import type { ScheduleFormValues } from "@/lib/tasks";

const BASE: ScheduleFormValues = {
  dueMode: "timed",
  dueDate: "2026-11-10",
  dueTime: "09:00",
  timezone: "America/Bogota",
  eventMode: "none",
  eventStartDate: "",
  eventStartTime: "09:00",
  eventEndDate: "",
  durationMinutes: 30,
};

describe("buildScheduleFields", () => {
  it("asks for a date instead of reporting a nonexistent time", () => {
    expect(buildScheduleFields({ ...BASE, dueDate: "" })).toBe("elige la fecha de vencimiento.");
    expect(buildScheduleFields({ ...BASE, eventMode: "timed", eventStartDate: "" })).toBe(
      "elige la fecha de inicio de agenda.",
    );
  });

  it("builds a timed due and timed event block", () => {
    const result = buildScheduleFields({
      ...BASE,
      eventMode: "timed",
      eventStartDate: "2026-11-10",
    });
    expect(result).toMatchObject({
      date_status: "resolved",
      due_date: null,
      due_at: "2026-11-10T14:00:00.000Z",
      all_day: false,
      event_block: { start_at: "2026-11-10T14:00:00.000Z", end_at: "2026-11-10T14:30:00.000Z" },
    });
  });

  it("builds an all-day schedule and validates the range", () => {
    const ok = buildScheduleFields({
      ...BASE,
      dueMode: "all_day",
      eventMode: "all_day",
      eventStartDate: "2026-11-10",
      eventEndDate: "2026-11-11",
    });
    expect(ok).toMatchObject({ due_date: "2026-11-10", due_at: null, all_day: true });
    expect(
      buildScheduleFields({
        ...BASE,
        dueMode: "all_day",
        eventMode: "all_day",
        eventStartDate: "2026-11-10",
        eventEndDate: "2026-11-10",
      }),
    ).toBe("el fin de agenda debe ser posterior al inicio.");
  });

  it("maps a missing due to date_status missing", () => {
    expect(buildScheduleFields({ ...BASE, dueMode: "missing" })).toMatchObject({
      date_status: "missing",
      due_date: null,
      due_at: null,
      event_block: null,
    });
  });
});
