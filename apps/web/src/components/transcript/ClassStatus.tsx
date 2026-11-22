import { Button } from "@/components/ui/Button";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { failedStages, isCleanupPending } from "@/lib/library";
import type { LibraryAudioDetail } from "@/lib/library";

// D3.5 - visible stage state and the only text-processing actions allowed here: queue
// analysis over persisted text or retry a failed index. Neither action uploads audio.

export interface ClassStatusProps {
  detail: LibraryAudioDetail;
  busy: boolean;
  onBackfill: () => void;
  onRetryIndex: () => void;
}

function analysisActionLabel(state: string): string {
  if (state === "not_requested") return "analizar esta clase";
  if (state === "failed" || state === "cancelled") return "reintentar análisis";
  return "re-analizar con la versión actual";
}

export function ClassStatus({
  detail,
  busy,
  onBackfill,
  onRetryIndex,
}: ClassStatusProps) {
  const analyzeState = detail.stages.analyze ?? "not_requested";
  const indexState = detail.stages.index ?? "not_requested";
  const cleanupPending = isCleanupPending(detail);
  const retryIndex = failedStages(detail).includes("index");

  return (
    <section aria-label="estado de la clase" className="lowercase flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 text-base">
        <span>clase</span>
        <StatusBadge status={detail.aggregate_status} />
        <span>intento</span>
        {detail.attempt !== null ? (
          <StatusBadge status={detail.attempt.status} />
        ) : (
          <span className="text-lavender/60">sin intento</span>
        )}
        <span>índice</span>
        <StatusBadge status={indexState} />
        <span>análisis</span>
        <StatusBadge status={analyzeState} />
      </div>

      {cleanupPending && (
        <p role="status" className="border-2 border-mint px-2 py-1 text-mint">
          &gt; limpieza pendiente: el audio aún no se ha verificado como borrado.
        </p>
      )}

      {detail.analysis_backfill_available && (
        <div className="flex flex-col gap-2 border-2 border-mint/60 px-3 py-2">
          <p className="text-base text-lavender/80">
            &gt; {analysisActionLabel(analyzeState)} usando solo el texto persistido; no se
            vuelve a subir ni a transcribir el audio.
          </p>
          <Button type="button" size="sm" disabled={busy} onClick={onBackfill}>
            {analysisActionLabel(analyzeState)}
          </Button>
        </div>
      )}

      {retryIndex && (
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={onRetryIndex}>
            reintentar índice
          </Button>
          <span className="text-sm text-lavender/60">solo texto persistido</span>
        </div>
      )}
    </section>
  );
}
