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
    <div className="flex flex-col gap-1 text-xs">
      <p className="lowercase text-lavender/60">
        última sincronización:{" "}
        <span className="text-lavender">{formatLastSyncedAt(lastSyncedAt)}</span>
      </p>
      <p
        role={warning ? "status" : undefined}
        className={warning ? "lowercase text-mint" : "lowercase text-lavender/50"}
      >
        {warning ? "cobertura parcial: " : "cobertura: "}
        {calendarCoverageLabel(coverageNotice)}
        {stale ? " · datos desactualizados" : ""}
      </p>
    </div>
  );
}
