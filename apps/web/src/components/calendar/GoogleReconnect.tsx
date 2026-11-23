import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  calendarTargetLabel,
  googleConnectHref,
  googleConnectLabel,
} from "@/lib/integrations";
import type { GoogleStatus } from "@/lib/integrations";

// D3.7 - Google Calendar connection panel. The action is a real anchor: the API answers
// with a 302 to Google and sets the signed OAuth cookie, which a `fetch` POST cannot do.
// It never confirms a task or writes an event; reconnecting only resumes blocked work.

export interface GoogleReconnectProps {
  google: GoogleStatus;
  compact?: boolean;
}

const LINK_CLASSES =
  "inline-flex w-fit items-center gap-1 border-2 border-lavender/40 px-2 py-0.5 text-mint underline decoration-dotted hover:border-mint hover:bg-mint hover:text-on-accent";

export function GoogleReconnect({ google, compact = false }: GoogleReconnectProps) {
  const href = googleConnectHref(google);
  const label = googleConnectLabel(google);

  if (compact) {
    return (
      <div
        role="status"
        className="lowercase flex flex-wrap items-center justify-between gap-2 border-2 border-mint px-3 py-2 text-mint"
      >
        <p>
          &gt; calendar no está listo; las tareas confirmadas quedarán bloqueadas hasta
          conectar o reconectar google calendar.
        </p>
        <a className={LINK_CLASSES} href={href}>
          {label}
        </a>
      </div>
    );
  }

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2">
        <StatusBadge status={google.status} />
        <span className="lowercase text-base text-lavender/80">
          {google.calendar_write_ready ? "listo para escribir" : "sin escritura lista"}
        </span>
      </div>
      <p className="lowercase text-lavender/80">{calendarTargetLabel(google)}</p>
      <a className={LINK_CLASSES} href={href}>
        {label}
      </a>
    </div>
  );
}
