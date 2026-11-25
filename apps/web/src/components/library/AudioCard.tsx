import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation("study");
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
      ? t("audioCard.retryOne", { stage: stageLabel(retryStages[0]) })
      : t("audioCard.retryMany");

  const pendingStages = [
    {
      key: "audio",
      label: t("audioCard.stageAudio"),
      status: audio.attempt?.status ?? null,
      missing: t("audioCard.noAttempt"),
    },
    {
      key: "cleanup",
      label: t("audioCard.stageCleanup"),
      status: audio.attempt?.cleanup_status ?? null,
      missing: t("audioCard.noAttempt"),
    },
    {
      key: "index",
      label: t("audioCard.stageIndex"),
      status: audio.stages.index ?? null,
      missing: t("audioCard.noState"),
    },
    {
      key: "analyze",
      label: t("audioCard.stageAnalysis"),
      status: audio.stages.analyze ?? null,
      missing: t("audioCard.noState"),
    },
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
        <p className="text-sm text-lavender/60">{t("audioCard.allOk")}</p>
      ) : (
        <div className="flex flex-wrap items-center gap-2 text-lavender/60">
          {pendingStages.map((stage) => (
            <span key={stage.key} className="flex items-center gap-1">
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
          {t("audioCard.cleanupPending")}
        </p>
      )}

      {partial && (
        <p role="status" className="text-mint">
          {t("audioCard.partial", {
            detail:
              retryStages.length > 0
                ? t("audioCard.failedStages", {
                    stages: retryStages.map(stageLabel).join(t("audioCard.and")),
                  })
                : t("audioCard.noRetryable"),
          })}
        </p>
      )}

      {failed && (
        <p className="text-lavender/60">
          {t("audioCard.failed")}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {(processing || cleanupPending) && (
          <Button type="button" size="sm" variant="secondary" onClick={onToggleProgress}>
            {expanded ? t("audioCard.hideProgress") : t("audioCard.showProgress")}
          </Button>
        )}

        {hasReader && (
          <Link
            to={`/gestion/biblioteca/${encodeURIComponent(audio.id)}`}
            className={buttonClasses("secondary", "sm")}
          >
            {t("audioCard.openTranscript")}
          </Link>
        )}

        {hasReader && (
          <Link
            to={`/gestion/chat?mode=class&audio_id=${encodeURIComponent(audio.id)}`}
            className={buttonClasses("ghost", "sm")}
          >
            {t("audioCard.chat")}
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
            {t("audioCard.reupload")}
          </Button>
        )}

        <Button type="button" size="sm" variant="danger" disabled={busy} onClick={onDelete}>
          {t("audioCard.delete")}
        </Button>
      </div>

      {expanded && <IngestProgress audioId={audio.id} />}
    </li>
  );
}
