import { IngestProgress } from "@/components/IngestProgress";
import { Button } from "@/components/ui/Button";
import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  audioLabel,
  failedStages,
  isCleanupPending,
  stageLabel,
} from "@/lib/library";
import type { LibraryAudio, RetryStage } from "@/lib/library";

// D2.4 — Fila de una clase. El estado agregado y las etapas se muestran tal cual llegan;
// «volver a subir» (sin texto) nunca se mezcla con «reintentar análisis/índice» (con texto).
// El lector sigue deshabilitado hasta que exista el contrato estructurado de D2.5.
export interface AudioCardProps {
  audio: LibraryAudio;
  expanded: boolean;
  busy: boolean;
  onToggleProgress: () => void;
  onRetry: (stages: RetryStage[]) => void;
  onReupload: () => void;
}

export function AudioCard({
  audio,
  expanded,
  busy,
  onToggleProgress,
  onRetry,
  onReupload,
}: AudioCardProps) {
  const cleanupPending = isCleanupPending(audio);
  const retryStages = failedStages(audio);
  const partial = audio.aggregate_status === "partial";
  const processing = audio.aggregate_status === "processing";
  const failed = audio.aggregate_status === "failed";
  const retryLabel =
    retryStages.length === 1
      ? `reintentar ${stageLabel(retryStages[0])}`
      : "reintentar etapas fallidas";

  return (
    <li className="lowercase flex flex-col gap-2 border-2 border-pure-black px-3 py-2">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-lavender">{audioLabel(audio)}</p>
          <p className="text-lavender/50">
            <time dateTime={audio.class_date}>{audio.class_date}</time> · {audio.language_code} ·{" "}
            {audio.class_timezone}
          </p>
        </div>
        <StatusBadge status={audio.aggregate_status} />
      </div>

      <div className="flex flex-wrap items-center gap-2 text-lavender/60">
        <span>audio:</span>
        {audio.attempt !== null ? (
          <StatusBadge status={audio.attempt.status} />
        ) : (
          <span className="text-lavender/50">sin intento</span>
        )}
        <span>index:</span>
        {audio.stages.index !== undefined ? (
          <StatusBadge status={audio.stages.index} />
        ) : (
          <span className="text-lavender/50">sin estado</span>
        )}
        <span>análisis:</span>
        {audio.stages.analyze !== undefined ? (
          <StatusBadge status={audio.stages.analyze} />
        ) : (
          <span className="text-lavender/50">sin estado</span>
        )}
      </div>

      {cleanupPending && (
        <p role="status" className="border-2 border-mint px-2 py-1 text-mint">
          &gt; limpieza pendiente · no es un éxito; la indexación sigue bloqueada hasta verificar
          el borrado del audio.
        </p>
      )}

      {partial && (
        <p role="status" className="text-mint">
          &gt; parcial ·{" "}
          {retryStages.length > 0
            ? `falló ${retryStages.map(stageLabel).join(" y ")}`
            : "no se identificó una etapa reintentable"}
        </p>
      )}

      {failed && (
        <p className="text-lavender/60">
          &gt; sin transcripción utilizable · vuelve a subir el audio original.
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {(processing || cleanupPending) && (
          <Button type="button" size="sm" variant="secondary" onClick={onToggleProgress}>
            {expanded ? "ocultar progreso" : "ver progreso"}
          </Button>
        )}

        {(partial || audio.aggregate_status === "ready") && (
          <Button
            type="button"
            size="sm"
            variant="secondary"
            disabled
            title="el lector llega con D2.5"
          >
            abrir transcripción
          </Button>
        )}

        {partial && retryStages.length > 0 && (
          <Button
            type="button"
            size="sm"
            disabled={busy}
            onClick={() => onRetry(retryStages)}
          >
            {retryLabel}
          </Button>
        )}

        {failed && (
          <Button type="button" size="sm" disabled={busy} onClick={onReupload}>
            volver a subir
          </Button>
        )}

        {(partial || audio.aggregate_status === "ready") && (
          <span className="text-lavender/50">&gt; lector disponible en D2.5</span>
        )}
      </div>

      {expanded && <IngestProgress audioId={audio.id} />}
    </li>
  );
}
