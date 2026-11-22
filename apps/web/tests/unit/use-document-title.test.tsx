// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, renderHook } from "@testing-library/react";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

afterEach(() => {
  cleanup();
  document.title = "";
});

describe("useDocumentTitle (A9)", () => {
  it("sets the page title on mount and restores the previous title on unmount", () => {
    document.title = "maulwurf";
    const { unmount } = renderHook(() => useDocumentTitle("tareas"));

    expect(document.title).toBe("tareas · maulwurf");

    unmount();
    expect(document.title).toBe("maulwurf");
  });
});
