// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { MarkdownBody } from "@/components/chat/MarkdownBody";

afterEach(() => {
  cleanup();
});

describe("MarkdownBody", () => {
  it("renders the Markdown subset used by assistant answers", () => {
    render(
      <MarkdownBody
        content={[
          "**concepto** y *detalle*",
          "",
          "- primero",
          "- segundo",
          "",
          "```js",
          "const x = 1;",
          "```",
          "",
          "| a | b |",
          "| - | - |",
          "| 1 | 2 |",
        ].join("\n")}
      />,
    );

    expect(screen.getByText("concepto").tagName).toBe("STRONG");
    expect(screen.getByText("detalle").tagName).toBe("EM");
    expect(screen.getAllByRole("listitem")).toHaveLength(2);
    expect(screen.getByText("const x = 1;")).toBeTruthy();
    expect(screen.getByRole("table")).toBeTruthy();
    expect(screen.getByText("a")).toBeTruthy();
    expect(screen.getByText("1")).toBeTruthy();
  });

  it("keeps raw HTML inert and never renders model links or images", () => {
    const { container } = render(
      <MarkdownBody
        content={
          '<img src=x onerror="alert(1)"> <script>alert(1)</script> [malo](javascript:alert(1)) [externo](https://example.com) ![alt](https://example.com/x.png)'
        }
      />,
    );

    expect(container.querySelector("img")).toBeNull();
    expect(container.querySelector("script")).toBeNull();
    expect(screen.queryByRole("link")).toBeNull();
    expect(screen.getByText(/malo/)).toBeTruthy();
    expect(screen.getByText(/externo/)).toBeTruthy();
  });
});
