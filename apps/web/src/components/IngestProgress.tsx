import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { RetroVisual } from "@/components/ui/RetroVisual";
import { StatusBadge } from "@/components/ui/StatusBadge";
import {
  estadoError404,
  estadoError404Anim,
  estadoError404Claro,
  estadoPrivacidadRam,
  estadoPrivacidadRamAnim,
  estadoPrivacidadRamClaro,
  estadoTranscribiendo,
  estadoTranscribiendoAnim,
  estadoTranscribiendoClaro,
  loaderCassetteAmbar,
  loaderCassetteAmbarStatic,
  loaderCassetteLavanda,
  loaderCassetteLavandaStatic,
} from "@/components/Visual";
import {
  INITIAL_PROGRESS,
  fragmentLabel,
  isCleanupPending,
  progressAggregate,
  reduceProgress,
} from "@/lib/progress";
import type { ProgressState } from "@/lib/progress";
import { createProgressSource } from "@/lib/sse";
import type { ProgressSource, ProgressTransport } from "@/lib/sse";
import { formatBytes } from "@/lib/upload";

// D2.3 — Progreso real de la ingesta (U-S2-DA-03). Muestra el estado que publica A2.9:
// intento, etapas, bytes recibidos y fragmentos completados/total. No calcula porcentajes
// ni ETA; `transcript_committed_cleanup_pending` se ve como limpieza pendiente, nunca como
// éxito. Cerrar la vista no cancela la ingesta.
export interface IngestProgressProps {
  audioId: string;
}

export function IngestProgress({ audioId }: IngestProgressProps) {
  const { t } = useTranslation("study");
  const [state, setState] = useState<ProgressState>(INITIAL_PROGRESS);
  const [transport, setTransport] = useState<ProgressTransport>("sse");
  const [transportError, setTransportError] = useState<string | null>(null);
  const [retryKey, setRetryKey] = useState(0);
  const sourceRef = useRef<ProgressSource | null>(null);

  useEffect(() => {
    setState(INITIAL_PROGRESS);
    setTransport("sse");
    setTransportError(null);

    const source = createProgressSource(audioId, {
      onEvent: (event) => setState((current) => reduceProgress(current, event)),
      onTransport: setTransport,
      onTransportError: setTransportError,
    });
    sourceRef.current = source;

    return () => {
      source.close();
      if (sourceRef.current === source) sourceRef.current = null;
    };
  }, [audioId, retryKey]);

  const snapshot = state.snapshot;
  const aggregate = progressAggregate(state);
  // El frame `progress` no repite el estado del intento. Cuando llega `done`, el agregado
  // es la única verdad final: no se sigue mostrando un `cleanup_pending` que quedó viejo.
  const cleanupPending = state.terminal === null && isCleanupPending(snapshot);
  const terminalText =
    aggregate === "ready"
      ? t("ingestProgress.terminalReady")
      : aggregate === "partial"
        ? t("ingestProgress.terminalPartial")
        : null;
  const received = snapshot?.attempt.received_bytes ?? 0;
  const fragments =
    snapshot === null
      ? null
      : fragmentLabel(snapshot.attempt.fragments_done, snapshot.attempt.fragments_total);
  const hasAttemptData =
    snapshot !== null &&
    (snapshot.attempt.status !== null ||
      received > 0 ||
      snapshot.attempt.fragments_total !== null);

  if (state.terminal === "error" && state.error !== null) {
    return (
      <div className="lowercase flex flex-col gap-3 border-2 border-mint px-3 py-2 sm:flex-row sm:items-center">
        <div className="w-fit shrink-0 border-2 border-pure-black bg-dark p-1">
          <RetroVisual
            src={estadoError404Anim}
            staticSrc={estadoError404}
            lightSrc={estadoError404Claro}
            className="h-16 w-16 object-contain"
          />
        </div>
        <div className="flex flex-col gap-2">
          <div className="flex flex-wrap items-center gap-2">
            <span className="font-pixel text-mint">{t("ingestProgress.stopped")}</span>
            <span className="text-mint">{state.error.code}</span>
            {aggregate !== null && <StatusBadge status={aggregate} />}
          </div>
          {state.error.detail !== null && (
            <p className="text-lavender/80">
              {t("ingestProgress.detail", { detail: state.error.detail })}
            </p>
          )}
          <p className="text-lavender/60">
            {t("ingestProgress.notReady")}
          </p>
          <div>
            <Button type="button" size="sm" variant="secondary" onClick={() => setRetryKey((k) => k + 1)}>
              {t("ingestProgress.retryProgress")}
            </Button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="lowercase flex flex-col gap-2 border-t-2 border-lavender/30 pt-3">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-pixel text-mint">{t("ingestProgress.title")}</span>
        {aggregate !== null && <StatusBadge status={aggregate} />}
        <span className="text-lavender/60">
          {transport === "sse" ? t("ingestProgress.realtime") : t("ingestProgress.auto")}
        </span>
      </div>

      {transportError !== null && (
        <p role="alert" className="text-mint">
          &gt; {transportError}
        </p>
      )}

      {snapshot === null ? (
        <p className="text-lavender/60">
          {t("ingestProgress.waiting")}
        </p>
      ) : (
        <>
          {state.terminal === null && !cleanupPending && (
            <div className="flex items-center gap-3 border-2 border-lavender/30 bg-dark p-2">
              <div className="shrink-0">
                <RetroVisual
                  src={estadoTranscribiendoAnim}
                  staticSrc={estadoTranscribiendo}
                  lightSrc={estadoTranscribiendoClaro}
                  className="h-14 w-14 object-contain"
                />
              </div>
              <RetroVisual
                src={loaderCassetteLavanda}
                staticSrc={loaderCassetteLavandaStatic}
                lightSrc={loaderCassetteAmbar}
                lightStaticSrc={loaderCassetteAmbarStatic}
                className="h-10 w-auto object-contain"
              />
            </div>
          )}
          <div className="flex flex-wrap items-center gap-2 text-lavender/80">
            {hasAttemptData ? (
              <>
                <span>{t("ingestProgress.attempt")}</span>
                {state.terminal === null ? (
                  snapshot.attempt.status !== null ? (
                    <StatusBadge status={snapshot.attempt.status} />
                  ) : (
                    <span className="text-lavender/60">{t("ingestProgress.noState")}</span>
                  )
                ) : (
                  <span className="text-lavender/60">{t("ingestProgress.closed")}</span>
                )}
                <span>·</span>
                <span>{t("ingestProgress.received", { bytes: formatBytes(received) })}</span>
                {fragments !== null && (
                  <>
                    <span>·</span>
                    <span>{t("ingestProgress.fragments", { value: fragments })}</span>
                  </>
                )}
              </>
            ) : (
              <span className="text-lavender/60">{t("ingestProgress.noAttemptData")}</span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2 text-lavender/80">
            <span>{t("ingestProgress.index")}</span>
            <StatusBadge status={snapshot.stages.index} />
            <span>{t("ingestProgress.analysis")}</span>
            <StatusBadge status={snapshot.stages.analyze} />
          </div>

          {cleanupPending && (
            <div className="flex items-center gap-3 border-2 border-mint px-2 py-1.5">
              <div className="shrink-0 border border-pure-black bg-dark p-0.5">
                <RetroVisual
                  src={estadoPrivacidadRamAnim}
                  staticSrc={estadoPrivacidadRam}
                  lightSrc={estadoPrivacidadRamClaro}
                  className="h-12 w-12 object-contain"
                />
              </div>
              <p role="status" className="text-mint">
                {t("ingestProgress.cleanupPending")}
              </p>
            </div>
          )}
        </>
      )}

      {state.terminal === "done" && terminalText !== null && (
        <p role="status" className="text-lavender">
          &gt; {terminalText}
        </p>
      )}

      {state.terminal === null && (
        <div className="flex flex-wrap items-center gap-2">
          {transport === "polling" && (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => setRetryKey((k) => k + 1)}
            >
              {t("ingestProgress.retryEvents")}
            </Button>
          )}
          <span className="text-lavender/60">{t("ingestProgress.closeNote")}</span>
        </div>
      )}
    </div>
  );
}
