import type { ChatToolCall } from "@/lib/chat";

// D3.8 - a chat answer must say where its calendar/task data came from. The badge is
// driven by the structured tool event, never by the model's prose, so a hallucinated
// "según tu agenda" cannot add provenance. A proposal is labelled as an inbox item.

const LABELS: Record<string, string> = {
  get_calendar_events: "según tu agenda",
  get_pending_tasks: "según tus tareas",
  propose_task: "propuesta para la bandeja",
};

export interface ToolBadgeProps {
  tool: ChatToolCall;
}

export function ToolBadge({ tool }: ToolBadgeProps) {
  const label = LABELS[tool.name] ?? tool.name;
  const stale = tool.name === "get_calendar_events" && tool.payload.stale === true;
  const incomplete =
    tool.name === "get_calendar_events" && tool.payload.coverage_ok === false;
  const suffix = stale ? " · datos antiguos" : incomplete ? " · cobertura incompleta" : "";

  return (
    <span
      className={
        tool.ok
          ? "lowercase inline-flex items-center border-2 border-lavender/40 px-2 py-0.5 text-xs text-lavender/70"
          : "lowercase inline-flex items-center border-2 border-mint px-2 py-0.5 text-xs text-mint"
      }
    >
      {tool.ok ? `${label}${suffix}` : `${label} · no disponible`}
    </span>
  );
}
