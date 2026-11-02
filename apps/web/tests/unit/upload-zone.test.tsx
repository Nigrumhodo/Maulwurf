// @vitest-environment jsdom
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it } from "vitest";
import { MemoryRouter } from "react-router-dom";
import { UploadZone } from "@/components/dashboard/UploadZone";

afterEach(() => {
  cleanup();
});

describe("UploadZone", () => {
  it("abre el formulario de subida en vez de un selector de archivo mudo", () => {
    render(
      <MemoryRouter>
        <UploadZone />
      </MemoryRouter>,
    );

    const link = screen.getByRole("link", { name: /continuar/i });
    expect(link.getAttribute("href")).toBe("/gestion/subir");
    expect(screen.queryByRole("button", { name: "seleccionar archivo" })).toBeNull();
  });
});
