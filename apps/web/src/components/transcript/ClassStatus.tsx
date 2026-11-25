import { useTranslation } from "react-i18next";
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

export function ClassStatus({
  detail,
  busy,
  onBackfill,
  onRetryIndex,
}: ClassStatusProps) {
  const { t } = useTranslation("study");
  const analyzeState = detail.stages.analyze ?? "not_requested";
  const indexState = detail.stages.index ?? "not_requested";
  const cleanupPending = isCleanupPending(detail);
  const retryIndex = failedStages(detail).includes("index");
  const analysisAction =
    analyzeState === "not_requested"
      ? t("classStatus.analyzeNew")
      : analyzeState === "failed" || analyzeState === "cancelled"
        ? t("classStatus.analyzeRetry")
        : t("classStatus.analyzeAgain");

  return (
    <section aria-label={t("classStatus.aria")} className="lowercase flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-2 text-base">
        <span>{t("classStatus.class")}</span>
        <StatusBadge status={detail.aggregate_status} />
        <span>{t("classStatus.attempt")}</span>
        {detail.attempt !== null ? (
          <StatusBadge status={detail.attempt.status} />
        ) : (
          <span className="text-lavender/60">{t("classStatus.noAttempt")}</span>
        )}
        <span>{t("classStatus.index")}</span>
        <StatusBadge status={indexState} />
        <span>{t("classStatus.analysis")}</span>
        <StatusBadge status={analyzeState} />
      </div>

      {cleanupPending && (
        <p role="status" className="border-2 border-mint px-2 py-1 text-mint">
          {t("classStatus.cleanupPending")}
        </p>
      )}

      {detail.analysis_backfill_available && (
        <div className="flex flex-col gap-2 border-2 border-mint/60 px-3 py-2">
          <p className="text-base text-lavender/80">
            {t("classStatus.backfillNote", { action: analysisAction })}
          </p>
          <Button type="button" size="sm" disabled={busy} onClick={onBackfill}>
            {analysisAction}
          </Button>
        </div>
      )}

      {retryIndex && (
        <div className="flex flex-wrap items-center gap-2">
          <Button type="button" size="sm" variant="secondary" disabled={busy} onClick={onRetryIndex}>
            {t("classStatus.retryIndex")}
          </Button>
          <span className="text-sm text-lavender/60">{t("classStatus.textOnly")}</span>
        </div>
      )}
    </section>
  );
}
