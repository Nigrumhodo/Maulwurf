import { useEffect, useState } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";
import { ClassSummary } from "@/components/transcript/ClassSummary";
import { Button } from "@/components/ui/Button";
import { buttonClasses } from "@/components/ui/buttonStyles";
import { audioLabel } from "@/lib/library";
import type { LibraryAudioDetail } from "@/lib/library";
import { downloadSrt, segmentLabel } from "@/lib/reader";
import type { ReaderTranscript } from "@/lib/reader";

// D2.5 — Lector enlazable (C-S2-DA-04): segmentos con `?segment=`, foco/highlight,
// tiempos solo cuando el contrato los declara fiables, exportaciones condicionadas y
// resumen vacío cuando el análisis no se solicitó. Nunca renderiza HTML del transcript.
export interface TranscriptReaderProps {
  detail: LibraryAudioDetail;
  transcript: ReaderTranscript;
  activeSegmentId: string | null;
  /** Stage badges and actions rendered on the line under the title. */
  status?: ReactNode;
  /** Extra blocks for the right column (detected items). */
  aside?: ReactNode;
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
  status,
  aside,
}: TranscriptReaderProps) {
  const { t } = useTranslation("study");
  const location = useLocation();
  const [srtBusy, setSrtBusy] = useState(false);
  const [srtUnavailable, setSrtUnavailable] = useState(false);
  const [srtError, setSrtError] = useState<string | null>(null);
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
      setSrtError(t("transcriptReader.srtFailed"));
    } finally {
      setSrtBusy(false);
    }
  }

  return (
    <section className="lowercase font-pixel min-w-0 max-w-full border-2 border-pure-black bg-surface shadow-block">
      <header className="flex flex-col gap-2 border-b-2 border-lavender/30 p-4 sm:p-6">
        <Link
          to="/gestion/biblioteca"
          className="self-start lowercase font-pixel text-base text-mint hover:underline"
        >
          {t("transcriptReader.back")}
        </Link>
        <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-2">
          <div className="flex items-center gap-3">
            <span
              aria-hidden="true"
              className="inline-block h-3 w-3 shrink-0 border-2 border-pure-black"
              style={{ backgroundColor: detail.subject.color }}
            />
            <h1 className="capitalize text-2xl text-mint">{audioLabel(detail)}</h1>
          </div>
          <div className="lowercase flex flex-wrap items-center gap-2">
            <Link
              to={`/gestion/chat?mode=class&audio_id=${encodeURIComponent(detail.id)}`}
              className={buttonClasses("secondary", "sm")}
            >
              {t("transcriptReader.chatClass")}
            </Link>
            {detail.exports.txt ? (
              <a
                href={`/audios/${encodeURIComponent(detail.id)}/transcript.txt`}
                download="transcript.txt"
                className={buttonClasses("secondary", "sm")}
              >
                {t("transcriptReader.downloadTxt")}
              </a>
            ) : (
              <Button type="button" size="sm" variant="secondary" disabled>
                {t("transcriptReader.downloadTxt")}
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              variant="secondary"
              disabled={srtDisabled || srtBusy}
              onClick={() => void handleSrt()}
            >
              {srtBusy ? t("transcriptReader.preparingSrt") : t("transcriptReader.downloadSrt")}
            </Button>
          </div>
        </div>
        <p className="lowercase text-base text-lavender/80">
          {detail.subject.name} · <time dateTime={detail.class_date}>{detail.class_date}</time> ·{" "}
          {detail.language_code} · {detail.class_timezone}
        </p>
        {status}
        <p className="lowercase text-base text-lavender/60">
          {t("transcriptReader.audioNotKept")}
        </p>
        {!detail.exports.srt && (
          <p className="lowercase text-base text-lavender/60">
            {t("transcriptReader.srtNoTimes")}
          </p>
        )}
        {srtUnavailable && (
          <p role="status" className="lowercase text-base text-mint">
            {t("transcriptReader.srtVersion")}
          </p>
        )}
        {srtError !== null && (
          <p role="alert" className="lowercase text-base text-mint">
            &gt; {srtError}
          </p>
        )}
      </header>

      {!activeExists && (
        <p
          role="alert"
          className="lowercase border-b-2 border-lavender/30 px-4 py-3 text-mint sm:px-6"
        >
          {t("transcriptReader.segmentMissing")}
        </p>
      )}

      <div className="grid lg:grid-cols-[minmax(0,1fr)_22rem]">
        <section aria-label={t("transcriptReader.transcript")} className="min-w-0 p-4 sm:p-6">
          <h2 className="capitalize mb-3 text-xl text-mint">
            {t("transcriptReader.segments", { count: transcript.segments.length })}
          </h2>
          {transcript.segments.length === 0 ? (
            <p className="lowercase text-lavender/60">
              {t("transcriptReader.noSegments")}
            </p>
          ) : (
            <ol className="divide-y-2 divide-lavender/20">
              {transcript.segments.map((segment) => {
                const active = segment.id === activeSegmentId;
                return (
                  <li
                    key={segment.id}
                    id={`segment-${segment.id}`}
                    tabIndex={-1}
                    aria-current={active ? "location" : undefined}
                    className={`flex flex-col gap-1 py-3 outline-none first:pt-0 last:pb-0 focus-visible:outline-2 focus-visible:outline-mint ${
                      active ? "bg-mint/10 px-3" : ""
                    }`}
                  >
                    <div className="lowercase flex flex-wrap items-center gap-2 text-sm text-lavender/60">
                      <Link
                        to={{ pathname: location.pathname, search: `?segment=${segment.id}` }}
                        className="text-sm text-lavender/60 outline-offset-2 hover:text-mint hover:underline focus-visible:outline-2 focus-visible:outline-mint"
                        aria-label={t("transcriptReader.linkSegment", { ordinal: segment.ordinal })}
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
                      {active && <span className="text-mint">{t("transcriptReader.linked")}</span>}
                    </div>
                    <p className="max-w-prose normal-case text-lg leading-relaxed text-lavender">
                      {segment.text}
                    </p>
                  </li>
                );
              })}
            </ol>
          )}
        </section>

        <aside
          aria-label={t("transcriptReader.details")}
          className="flex min-w-0 flex-col divide-y-2 divide-lavender/20 border-t-2 border-lavender/30 p-4 sm:p-6 lg:border-l-2 lg:border-t-0"
        >
          <ClassSummary detail={detail} transcript={transcript} />
          {aside}
        </aside>
      </div>
    </section>
  );
}
