import { calendarCoverageLabel, formatLastSyncedAt } from "@/lib/calendar";

// D3.6 - never present a partial or stale mirror as "the calendar". The freshness line is
// always visible, including the "never synced" state.

export interface CalendarFreshnessProps {
  lastSyncedAt: string | null;
  coverageOk: boolean;
  coverageNotice: string | null;
  stale: boolean;
}

export function CalendarFreshness({
  lastSyncedAt,
  coverageOk,
  coverageNotice,
  stale,
}: CalendarFreshnessProps) {
  const warning = stale || !coverageOk;
  return (
    <p className="lowercase text-sm text-lavender/80">
      <span>
        última sincronización:{" "}
        <span className="text-lavender">{formatLastSyncedAt(lastSyncedAt)}</span>
      </span>
      {" · "}
      <span role={warning ? "status" : undefined} className={warning ? "text-mint" : undefined}>
        {warning ? "cobertura parcial: " : "cobertura: "}
        {calendarCoverageLabel(coverageNotice)}
        {stale ? " · datos desactualizados" : ""}
      </span>
    </p>
  );
}
