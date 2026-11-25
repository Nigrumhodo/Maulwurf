import { useTranslation } from "react-i18next";
import type { ChatToolCall } from "@/lib/chat";

// D3.8 - a chat answer must say where its calendar/task data came from. The badge is
// driven by the structured tool event, never by the model's prose, so a hallucinated
// "según tu agenda" cannot add provenance. A proposal is labelled as an inbox item.

const LABEL_KEYS = {
  get_calendar_events: "toolBadge.calendar",
  get_pending_tasks: "toolBadge.tasks",
  propose_task: "toolBadge.propose",
} as const;

function isKnownTool(name: string): name is keyof typeof LABEL_KEYS {
  return Object.hasOwn(LABEL_KEYS, name);
}

export interface ToolBadgeProps {
  tool: ChatToolCall;
}

export function ToolBadge({ tool }: ToolBadgeProps) {
  const { t } = useTranslation("study");
  const label = isKnownTool(tool.name) ? t(LABEL_KEYS[tool.name]) : tool.name;
  const stale = tool.name === "get_calendar_events" && tool.payload.stale === true;
  const incomplete =
    tool.name === "get_calendar_events" && tool.payload.coverage_ok === false;
  const suffix = stale
    ? ` · ${t("toolBadge.stale")}`
    : incomplete
      ? ` · ${t("toolBadge.incomplete")}`
      : "";

  return (
    <span
      className={
        tool.ok
          ? "lowercase inline-flex items-center border-2 border-lavender/40 px-2 py-0.5 text-xs text-lavender/80"
          : "lowercase inline-flex items-center border-2 border-mint px-2 py-0.5 text-xs text-mint"
      }
    >
      {tool.ok ? `${label}${suffix}` : `${label} · ${t("toolBadge.unavailable")}`}
    </span>
  );
}
