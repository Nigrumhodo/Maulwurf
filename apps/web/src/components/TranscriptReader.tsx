import { useEffect, useState } from "react";
import { Link, useLocation, useNavigate } from "react-router-dom";
import { Button } from "@/components/ui/Button";
import { buttonClasses } from "@/components/ui/buttonStyles";
import { Card } from "@/components/ui/Card";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { audioLabel } from "@/lib/library";
import type { LibraryAudioDetail } from "@/lib/library";
import {
  downloadSrt,
  segmentLabel,
  summaryState,
} from "@/lib/reader";
import type { ReaderTranscript } from "@/lib/reader";

// D2.5 — Lector enlazable (C-S2-DA-04): segmentos con `?segment=`, foco/highlight,
// tiempos solo cuando el contrato los declara fiables, exportaciones condicionadas y
// resumen vacío cuando el análisis no se solicitó. Nunca renderiza HTML del transcript.
export interface TranscriptReaderProps {
  detail: LibraryAudioDetail;
  transcript: ReaderTranscript;
  activeSegmentId: string | null;
}

function prefersReducedMotion(): boolean {
  return (
    typeof window !== "undefined" &&
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

export function TranscriptReader({
  detail,
  transcript,
  activeSegmentId,
}: TranscriptReaderProps) {
  const location = useLocation();
  const navigate = useNavigate();
  const [srtBusy, setSrtBusy] = useState(false);
  const [srtUnavailable, setSrtUnavailable] = useState(false);
  const [srtError, setSrtError] = useState<string | null>(null);
  const summary = summaryState(detail, transcript);
  const activeExists =
    activeSegmentId === null ||
    transcript.segments.some((segment) => segment.id === activeSegmentId);
  const srtDisabled = !detail.exports.srt || srtUnavailable;

  useEffect(() => {
    if (activeSegmentId === null) return;
    const element = document.getElementById(`segment-${activeSegmentId}`);
    if (element === null) return;
    if (typeof element.scrollIntoView === "function") {
      element.scrollIntoView({
        block: "center",
        behavior: prefersReducedMotion() ? "auto" : "smooth",
      });
    }
    element.focus({ preventScroll: true });
  }, [activeSegmentId, transcript]);

  async function handleSrt() {
    setSrtBusy(true);
    setSrtError(null);
    try {
      const result = await downloadSrt(detail.id);
      if (result === "unavailable") setSrtUnavailable(true);
    } catch {
      setSrtError("no se pudo descargar el SRT; reintenta en un momento.");
    } finally {
      setSrtBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant="secondary"
            aria-label="volver atrás"
            onClick={() => navigate(-1)}
          >
            ← atrás
          </Button>
          <Link
            to="/gestion/biblioteca"
            className="lowercase font-pixel text-mint hover:underline"
          >
            &lt; volver a la biblioteca
          </Link>
        </div>
        <div className="flex flex-wrap items-center gap-3">
          <span
            aria-hidden="true"
            className="inline-block h-3 w-3 border-2 border-pure-black"
            style={{ backgroundColor: detail.subject.color }}
          />
          <h1 className="capitalize text-2xl text-mint">{audioLabel(detail)}</h1>
          <StatusBadge status={detail.aggregate_status} />
        </div>
        <p className="lowercase text-lavender/60">
          {detail.subject.name} · <time dateTime={detail.class_date}>{detail.class_date}</time> ·{" "}
          {detail.language_code} · {detail.class_timezone}
        </p>
      </div>

      <Card title="transcripción" variant="accent">
        <div className="lowercase flex flex-col gap-4">
          <p className="border-2 border-mint px-3 py-2 text-mint">
            &gt; audio no conservado · solo se guarda el texto de la transcripción.
          </p>

          <div className="flex flex-wrap items-center gap-2">
            {detail.exports.txt ? (
              <a
                href={`/audios/${encodeURIComponent(detail.id)}/transcript.txt`}
                download="transcript.txt"
                className={buttonClasses("secondary", "sm")}
              >
                descargar txt
              </a>
            ) : (
              <Button type="button" size="sm" variant="secondary" disabled>
                descargar txt
              </Button>
            )}

            <Button
              type="button"
              size="sm"
              variant="secondary"
              disabled={srtDisabled || srtBusy}
              onClick={() => void handleSrt()}
            >
              {srtBusy ? "preparando srt…" : "descargar srt"}
            </Button>
          </div>

          {!detail.exports.srt && (
            <p className="text-lavender/60">
              &gt; srt no disponible: la transcripción no tiene tiempos fiables dentro de la
              duración.
            </p>
          )}
          {srtUnavailable && (
            <p role="status" className="text-mint">
              &gt; srt no disponible para esta versión; el texto sigue completo.
            </p>
          )}
          {srtError !== null && (
            <p role="alert" className="text-mint">
              &gt; {srtError}
            </p>
          )}
        </div>
      </Card>

      <Card title="resumen">
        <div className="lowercase flex flex-col gap-2">
          {summary.text !== null ? (
            <p className="leading-relaxed text-lavender">{summary.text}</p>
          ) : (
            <p className="text-lavender/60">&gt; {summary.note ?? "resumen no disponible"}</p>
          )}
          {transcript.topics.length > 0 && (
            <ul className="flex flex-wrap gap-2">
              {transcript.topics.map((topic) => (
                <li
                  key={topic}
                  className="border-2 border-lavender/40 px-2 py-0.5 text-lavender/70"
                >
                  {topic}
                </li>
              ))}
            </ul>
          )}
        </div>
      </Card>

      {!activeExists && (
        <p role="alert" className="lowercase border-2 border-mint px-3 py-2 text-mint">
          &gt; segmento no encontrado en esta transcripción; se muestra el texto completo.
        </p>
      )}

      <Card title={`segmentos · ${transcript.segments.length}`}>
        {transcript.segments.length === 0 ? (
          <p className="lowercase text-lavender/60">
            &gt; esta transcripción no tiene segmentos publicados.
          </p>
        ) : (
          <ol className="flex flex-col gap-3">
            {transcript.segments.map((segment) => {
              const active = segment.id === activeSegmentId;
              return (
                <li
                  key={segment.id}
                  id={`segment-${segment.id}`}
                  tabIndex={-1}
                  aria-current={active ? "location" : undefined}
                  className={`lowercase flex flex-col gap-2 border-2 px-3 py-2 outline-none ${
                    active
                      ? "border-mint bg-mint/10"
                      : "border-lavender/25 focus:border-mint"
                  }`}
                >
                  <div className="flex flex-wrap items-center gap-2 text-lavender/60">
                    <Link
                      to={{ pathname: location.pathname, search: `?segment=${segment.id}` }}
                      className="border-2 border-lavender/40 px-2 py-0.5 text-mint hover:border-mint"
                      aria-label={`enlazar segmento ${segment.ordinal}`}
                    >
                      #{segment.ordinal}
                    </Link>
                    <span>
                      {segmentLabel(
                        segment,
                        transcript.timestamp_precision,
                        detail.duration_seconds,
                      )}
                    </span>
                    {active && <span className="text-mint">· enlazado</span>}
                  </div>
                  <p className="leading-relaxed text-lavender">{segment.text}</p>
                </li>
              );
            })}
          </ol>
        )}
      </Card>
    </div>
  );
}
