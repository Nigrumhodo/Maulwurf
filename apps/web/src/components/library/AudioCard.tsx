import { Link } from "react-router-dom";
import { IngestProgress } from "@/components/IngestProgress";
import { Button } from "@/components/ui/Button";
import { buttonClasses } from "@/components/ui/buttonStyles";
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
export interface AudioCardProps {
  audio: LibraryAudio;
  expanded: boolean;
  busy: boolean;
  onToggleProgress: () => void;
  onRetry: (stages: RetryStage[]) => void;
  onReupload: () => void;
  onDelete: () => void;
}

const SETTLED_STATUSES = new Set(["done", "verified", "succeeded"]);

export function AudioCard({
  audio,
  expanded,
  busy,
  onToggleProgress,
  onRetry,
  onReupload,
  onDelete,
}: AudioCardProps) {
  const cleanupPending = isCleanupPending(audio);
  const retryStages = failedStages(audio);
  const partial = audio.aggregate_status === "partial";
  const processing = audio.aggregate_status === "processing";
  const failed = audio.aggregate_status === "failed";
  // A succeeded ingestion has committed usable text even while a text stage is still owed;
  // keep the reader reachable without treating the aggregate as final.
  const hasReader =
    partial || audio.aggregate_status === "ready" || audio.attempt?.status === "succeeded";
  const retryLabel =
    retryStages.length === 1
      ? `reintentar ${stageLabel(retryStages[0])}`
      : "reintentar etapas fallidas";

  const pendingStages = [
    { label: "audio", status: audio.attempt?.status ?? null, missing: "sin intento" },
    { label: "limpieza", status: audio.attempt?.cleanup_status ?? null, missing: "sin intento" },
    { label: "index", status: audio.stages.index ?? null, missing: "sin estado" },
    { label: "análisis", status: audio.stages.analyze ?? null, missing: "sin estado" },
  ].filter((stage) => stage.status === null || !SETTLED_STATUSES.has(stage.status));
  const title = audioLabel(audio);

  return (
    <li className="lowercase flex flex-col gap-2 py-3">
      <div className="flex flex-wrap items-start gap-2">
        <div className="min-w-0 flex-1">
          <p className="truncate text-lavender" title={title}>
            {title}
          </p>
          <p className="text-lavender/60">
            <time dateTime={audio.class_date}>{audio.class_date}</time> · {audio.language_code} ·{" "}
            {audio.class_timezone}
          </p>
        </div>
        <StatusBadge status={audio.aggregate_status} />
      </div>

      {pendingStages.length === 0 ? (
        <p className="text-sm text-lavender/60">todo ok</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2 text-lavender/60">
          {pendingStages.map((stage) => (
            <span key={stage.label} className="flex items-center gap-1">
              <span>{stage.label}:</span>
              {stage.status !== null ? (
                <StatusBadge status={stage.status} />
              ) : (
                <span className="text-lavender/60">{stage.missing}</span>
              )}
            </span>
          ))}
        </div>
      )}

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

        {hasReader && (
          <Link
            to={`/gestion/biblioteca/${encodeURIComponent(audio.id)}`}
            className={buttonClasses("secondary", "sm")}
          >
            abrir transcripción
          </Link>
        )}

        {hasReader && (
          <Link
            to={`/gestion/chat?mode=class&audio_id=${encodeURIComponent(audio.id)}`}
            className={buttonClasses("ghost", "sm")}
          >
            chat
          </Link>
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

        <Button type="button" size="sm" variant="danger" disabled={busy} onClick={onDelete}>
          eliminar
        </Button>
      </div>

      {expanded && <IngestProgress audioId={audio.id} />}
    </li>
  );
}
