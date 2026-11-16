// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { TapeDeck } from "@/components/dashboard/TapeDeck";

afterEach(() => {
  cleanup();
});

describe("TapeDeck", () => {
  it("abre el formulario de subida y no ofrece un selector de archivo", () => {
    render(
      <MemoryRouter>
        <TapeDeck />
      </MemoryRouter>,
    );

    const link = screen.getByRole("link", { name: /formulario de subida/i });
    expect(link.getAttribute("href")).toBe("/gestion/subir");
    expect(screen.queryByRole("button", { name: /seleccionar archivo/i })).toBeNull();
    expect(screen.queryByText("01. ia")).toBeNull();
  });
});
