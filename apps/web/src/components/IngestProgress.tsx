import { useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { StatusBadge } from "@/components/ui/StatusBadge";
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

function terminalMessage(aggregate: string | null): string | null {
  if (aggregate === "ready") return "el servidor marcó la clase como lista.";
  if (aggregate === "partial") {
    return "el servidor marcó la clase como parcial; revisa la etapa fallida.";
  }
  return null;
}

export function IngestProgress({ audioId }: IngestProgressProps) {
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
      <div className="lowercase flex flex-col gap-2 border-2 border-mint px-3 py-2">
        <div className="flex flex-wrap items-center gap-2">
          <span className="font-pixel text-mint">&gt; progreso detenido</span>
          <span className="text-mint">{state.error.code}</span>
          {aggregate !== null && <StatusBadge status={aggregate} />}
        </div>
        {state.error.detail !== null && (
          <p className="text-lavender/70">&gt; detalle: {state.error.detail}</p>
        )}
        <p className="text-lavender/60">
          &gt; la clase no se muestra como lista. Revisa la etapa o vuelve a subir el audio.
        </p>
        <div>
          <Button type="button" size="sm" variant="secondary" onClick={() => setRetryKey((k) => k + 1)}>
            reintentar progreso
          </Button>
        </div>
      </div>
    );
  }

  return (
    <div className="lowercase flex flex-col gap-2 border-2 border-lavender/40 px-3 py-2">
      <div className="flex flex-wrap items-center gap-2">
        <span className="font-pixel text-mint">&gt; progreso de la clase</span>
        {aggregate !== null && <StatusBadge status={aggregate} />}
        <span className="text-lavender/50">
          {transport === "sse" ? "eventos en vivo" : "sondeo (fallback)"}
        </span>
      </div>

      {transportError !== null && (
        <p role="alert" className="text-mint">
          &gt; {transportError}
        </p>
      )}

      {snapshot === null ? (
        <p className="text-lavender/60">
          &gt; esperando el primer estado del servidor… no se inventa ningún avance.
        </p>
      ) : (
        <>
          <div className="flex flex-wrap items-center gap-2 text-lavender/70">
            {hasAttemptData ? (
              <>
                <span>intento:</span>
                {state.terminal === null ? (
                  snapshot.attempt.status !== null ? (
                    <StatusBadge status={snapshot.attempt.status} />
                  ) : (
                    <span className="text-lavender/50">sin estado</span>
                  )
                ) : (
                  <span className="text-lavender/50">cerrado · revisa el agregado</span>
                )}
                <span>·</span>
                <span>recibidos {formatBytes(received)}</span>
                {fragments !== null && (
                  <>
                    <span>·</span>
                    <span>fragmentos {fragments}</span>
                  </>
                )}
              </>
            ) : (
              <span className="text-lavender/50">&gt; aún no hay datos del intento.</span>
            )}
          </div>

          <div className="flex flex-wrap items-center gap-2 text-lavender/70">
            <span>index:</span>
            <StatusBadge status={snapshot.stages.index} />
            <span>análisis:</span>
            <StatusBadge status={snapshot.stages.analyze} />
          </div>

          {cleanupPending && (
            <p role="status" className="border-2 border-mint px-2 py-1 text-mint">
              &gt; limpieza pendiente · no es un éxito y la indexación sigue bloqueada hasta
              verificar el borrado del audio.
            </p>
          )}
        </>
      )}

      {state.terminal === "done" && terminalMessage(aggregate) !== null && (
        <p role="status" className="text-lavender">
          &gt; {terminalMessage(aggregate)}
        </p>
      )}

      {state.terminal === null && (
        <div className="flex flex-wrap items-center gap-2">
          {transport === "sse" ? (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => sourceRef.current?.usePolling()}
            >
              usar sondeo
            </Button>
          ) : (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              onClick={() => setRetryKey((k) => k + 1)}
            >
              reintentar eventos
            </Button>
          )}
          <span className="text-lavender/50">&gt; cerrar la vista no cancela la ingesta.</span>
        </div>
      )}
    </div>
  );
}
