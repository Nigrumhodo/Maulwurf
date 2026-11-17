// @vitest-environment jsdom
import { afterEach, describe, expect, it } from "vitest";
import { cleanup, render, screen } from "@testing-library/react";
import { ToolBadge } from "@/components/chat/ToolBadge";
import type { ChatToolCall } from "@/lib/chat";

function tool(overrides: Partial<ChatToolCall> = {}): ChatToolCall {
  return { name: "get_calendar_events", ok: true, payload: {}, ...overrides };
}

afterEach(cleanup);

describe("ToolBadge", () => {
  it("labels calendar data as the agenda source", () => {
    render(<ToolBadge tool={tool()} />);
    expect(screen.getByText(/según tu agenda/)).toBeTruthy();
  });

  it("labels pending tasks as the tasks source", () => {
    render(<ToolBadge tool={tool({ name: "get_pending_tasks" })} />);
    expect(screen.getByText(/según tus tareas/)).toBeTruthy();
  });

  it("labels a proposal as an inbox item, never as a Calendar write", () => {
    render(<ToolBadge tool={tool({ name: "propose_task" })} />);
    expect(screen.getByText(/propuesta para la bandeja/)).toBeTruthy();
    expect(screen.queryByText(/calendar/i)).toBeNull();
  });

  it("marks a failed tool as unavailable", () => {
    render(<ToolBadge tool={tool({ ok: false })} />);
    expect(screen.getByText(/no disponible/)).toBeTruthy();
  });

  it("warns about stale or incomplete calendar coverage", () => {
    const { rerender } = render(<ToolBadge tool={tool({ payload: { stale: true } })} />);
    expect(screen.getByText(/datos antiguos/)).toBeTruthy();

    rerender(<ToolBadge tool={tool({ payload: { coverage_ok: false } })} />);
    expect(screen.getByText(/cobertura incompleta/)).toBeTruthy();
  });
});
