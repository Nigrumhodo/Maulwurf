// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { WeekStrip } from "@/components/dashboard/WeekStrip";

afterEach(() => {
  cleanup();
});

describe("WeekStrip", () => {
  it("renders the current week and marks today", () => {
    render(<WeekStrip />);

    expect(screen.getByRole("region", { name: "días de la semana actual" })).toBeTruthy();
    expect(screen.getAllByRole("listitem")).toHaveLength(7);
    expect(document.querySelector('[aria-current="date"]')).not.toBeNull();
  });
});
