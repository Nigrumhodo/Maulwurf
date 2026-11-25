import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { segmentLabelFromTimes } from "@/lib/reader";
import { taskEvidenceHref } from "@/lib/tasks";
import type { Task, TaskEvidence, TaskQualityState } from "@/lib/tasks";

// D3.4 - the task-to-reader link. The label is the same honest segment label the reader
// uses: a measured time only when the transcript declares reliable offsets and the end is
// inside the class; otherwise "segmento N". No audio, token or arbitrary URL is involved.

export interface TranscriptLinkProps {
  task: Task;
  evidence: TaskEvidence;
  quality: TaskQualityState | null;
}

export function TranscriptLink({ task, evidence, quality }: TranscriptLinkProps) {
  const { t } = useTranslation("study");
  const href = taskEvidenceHref(task, evidence);
  if (href === null) return null;

  const label = segmentLabelFromTimes(
    evidence.t_start,
    evidence.t_end,
    evidence.ordinal,
    quality?.timestamp_precision ?? "none",
    quality?.duration_seconds ?? null,
  );

  return (
    <Link
      to={href}
      className="inline-flex items-center gap-1 border-2 border-lavender/40 px-2 py-0.5 text-xs text-mint underline decoration-dotted hover:border-mint hover:bg-mint hover:text-on-accent"
      aria-label={t("transcriptLink.aria", { ordinal: evidence.ordinal })}
      title={t("transcriptLink.title")}
    >
      <span aria-hidden="true">{t("transcriptLink.view")}</span>{" "}
      <span className="normal-case">{label}</span>
    </Link>
  );
}
