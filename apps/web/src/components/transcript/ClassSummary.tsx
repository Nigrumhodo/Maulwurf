import { useMemo, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
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
  const [showAllTopics, setShowAllTopics] = useState(false);
  const summary = summaryState(detail, transcript);
  const warnings = qualitySignals(detail);
  const uniqueTopics = useMemo(() => {
    const seen = new Set<string>();
    const out: string[] = [];
    for (const raw of transcript.topics) {
      const cleaned = raw.trim();
      if (cleaned === "") continue;
      const key = cleaned.toLocaleLowerCase("es");
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

  return (
    <Card title="resumen y calidad">
      <div className="lowercase flex flex-col gap-3">
        {summary.text !== null ? (
          <p className="normal-case leading-relaxed text-lavender">{summary.text}</p>
        ) : (
          <p className="text-lavender/60">
            &gt; {summary.note ?? "resumen no disponible"}
          </p>
        )}

        {transcript.analysis_version !== null && (
          <p className="text-xs text-lavender/60">
            análisis v{transcript.analysis_version}
          </p>
        )}

        {uniqueTopics.length > 0 && (
          <div className="flex flex-col items-start gap-2">
            <ul aria-label="temas de la clase" className="flex flex-wrap gap-2">
              {visibleTopics.map((topic) => (
                <li
                  key={topic}
                  className="normal-case border-2 border-lavender/40 px-2 py-0.5 text-lavender/70"
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
                  ? "mostrar menos temas"
                  : `ver ${hiddenTopicsCount} temas más`}
              </Button>
            )}
          </div>
        )}

        {warnings.length > 0 ? (
          <p role="status" className="normal-case border-2 border-mint px-2 py-1 text-mint">
            calidad ASR baja:{" "}
            {warnings.map((signal) => qualitySummary(signal)).join(" · ")}
          </p>
        ) : (
          <p className="text-lavender/50">
            sin señales de calidad ASR registradas.
          </p>
        )}
      </div>
    </Card>
  );
}
