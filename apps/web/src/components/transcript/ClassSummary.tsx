import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { intlLocale } from "@/i18n";
import { qualitySignals, qualitySummary } from "@/lib/tasks";
import { summaryState } from "@/lib/reader";
import type { LibraryAudioDetail } from "@/lib/library";
import type { ReaderTranscript } from "@/lib/reader";

// D3.5 - the class summary is persisted text plus explicit states: a missing or pending
// summary is never replaced with generated copy, and ASR warnings keep their counts.

const INITIAL_TOPICS_SHOWN = 12;

export interface ClassSummaryProps {
  detail: LibraryAudioDetail;
  transcript: ReaderTranscript;
}

export function ClassSummary({ detail, transcript }: ClassSummaryProps) {
  const { t } = useTranslation("study");
  const [showAllTopics, setShowAllTopics] = useState(false);
  const summary = summaryState(detail, transcript);
  const warnings = qualitySignals(detail);
  const uniqueTopics = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const raw of transcript.topics) {
      const cleaned = raw.trim();
      if (cleaned === "") continue;
      const key = cleaned.toLocaleLowerCase(intlLocale());
      if (seen.has(key)) continue;
      seen.add(key);
      out.push(cleaned);
    }
    return out;
  }, [transcript.topics]);
  const visibleTopics = showAllTopics
    ? uniqueTopics
    : uniqueTopics.slice(0, INITIAL_TOPICS_SHOWN);
  const hiddenTopicsCount = uniqueTopics.length - visibleTopics.length;

  const sectionClass = "lowercase flex flex-col gap-2 py-5 first:pt-0 last:pb-0";

  return (
    <>
      <section aria-label={t("classSummary.summary")} className={sectionClass}>
        <h2 className="capitalize text-xl text-mint">{t("classSummary.summary")}</h2>
        {summary.text !== null ? (
          <p className="normal-case text-base leading-relaxed text-lavender">{summary.text}</p>
        ) : (
          <p className="text-base text-lavender/60">
            &gt; {summary.note ?? t("classSummary.unavailable")}
          </p>
        )}
        {transcript.analysis_version !== null && (
          <p className="text-sm text-lavender/60">{t("classSummary.analysisVersion", { version: transcript.analysis_version })}</p>
        )}
      </section>

      {uniqueTopics.length > 0 && (
        <section aria-label={t("classSummary.topics")} className={`${sectionClass} items-start`}>
          <h2 className="capitalize text-xl text-mint">{t("classSummary.topics")}</h2>
          <ul aria-label={t("classSummary.classTopics")} className="flex flex-wrap gap-2">
            {visibleTopics.map((topic) => (
              <li
                key={topic}
                className="normal-case border-2 border-lavender/30 px-2 py-0.5 text-base text-lavender/80"
              >
                {topic}
              </li>
            ))}
          </ul>
          {uniqueTopics.length > INITIAL_TOPICS_SHOWN && (
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setShowAllTopics((prev) => !prev)}
            >
              {showAllTopics
                ? t("classSummary.showLess")
                : t("classSummary.showMore", { count: hiddenTopicsCount })}
            </Button>
          )}
        </section>
      )}

      <section aria-label={t("classSummary.quality")} className={sectionClass}>
        <h2 className="capitalize text-xl text-mint">{t("classSummary.quality")}</h2>
        {warnings.length > 0 ? (
          <p role="status" className="normal-case text-mint">
            {t("classSummary.lowQuality", {
              signals: warnings.map((signal) => qualitySummary(signal)).join(" · "),
            })}
          </p>
        ) : (
          <p className="text-base text-lavender/60">{t("classSummary.noSignals")}</p>
        )}
      </section>
    </>
  );
}
