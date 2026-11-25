import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ClassItems } from "@/components/transcript/ClassItems";
import { ClassStatus } from "@/components/transcript/ClassStatus";
import { TranscriptReader } from "@/components/TranscriptReader";
import { Button } from "@/components/ui/Button";
import { ApiHttpError } from "@/lib/api";
import {
  backfillAnalysis,
  fetchAudioDetail,
  retryAudio,
} from "@/lib/library";
import type { LibraryAudioDetail } from "@/lib/library";
import { fetchTranscriptReader } from "@/lib/reader";
import type { ReaderTranscript } from "@/lib/reader";
import { fetchTaskPage, qualitySignals } from "@/lib/tasks";
import type { Task, TaskQualityState } from "@/lib/tasks";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

// D3.5 - the class page is the reader plus persisted metadata: stages, summary/topics,
// ASR warnings, detected items and the explicit analysis backfill/reanalysis action. It
// never exposes audio, never reuploads and never retranscribes.

function messageFor(error: unknown, fallback: string): string {
  return error instanceof ApiHttpError ? error.message : fallback;
}

export function TranscriptReaderPage() {
  const { t } = useTranslation("study");
  useDocumentTitle(t("readerPage.docTitle"));
  const { audioId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const [detail, setDetail] = useState<LibraryAudioDetail | null>(null);
  const [transcript, setTranscript] = useState<ReaderTranscript | null>(null);
  const [tasks, setTasks] = useState<Task[]>([]);
  const [loading, setLoading] = useState(true);
  const [tasksLoading, setTasksLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [tasksError, setTasksError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [actionBusy, setActionBusy] = useState(false);
  const [reloadKey, setReloadKey] = useState(0);
  const activeSegmentId = searchParams.get("segment");

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([fetchAudioDetail(audioId), fetchTranscriptReader(audioId)])
      .then(([nextDetail, nextTranscript]) => {
        if (cancelled) return;
        setDetail(nextDetail);
        setTranscript(nextTranscript);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setError(
          cause instanceof ApiHttpError && cause.status === 404
            ? t("readerPage.notFound")
            : messageFor(cause, t("readerPage.loadFailed")),
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [audioId, reloadKey]);

  useEffect(() => {
    let cancelled = false;
    setTasksLoading(true);
    setTasksError(null);
    fetchTaskPage({ audioId, status: "all", limit: 200 })
      .then((page) => {
        if (cancelled) return;
        setTasks(Array.isArray(page.items) ? page.items : []);
      })
      .catch((cause: unknown) => {
        if (cancelled) return;
        setTasks([]);
        setTasksError(
          messageFor(cause, t("readerPage.itemsFailed")),
        );
      })
      .finally(() => {
        if (!cancelled) setTasksLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [audioId, reloadKey]);

  async function handleBackfill(): Promise<void> {
    setActionBusy(true);
    setNotice(null);
    try {
      const result = await backfillAnalysis(audioId);
      setNotice(
        result.status === "already_analyzed"
          ? t("readerPage.alreadyAnalyzed")
          : t("readerPage.analysisQueued"),
      );
      setReloadKey((value) => value + 1);
    } catch (cause) {
      setNotice(messageFor(cause, t("readerPage.queueFailed")));
    } finally {
      setActionBusy(false);
    }
  }

  async function handleRetryIndex(): Promise<void> {
    if (transcript === null) return;
    setActionBusy(true);
    setNotice(null);
    try {
      await retryAudio(audioId, ["index"], transcript.version);
      setNotice(t("readerPage.indexQueued"));
      setReloadKey((value) => value + 1);
    } catch (cause) {
      setNotice(messageFor(cause, t("readerPage.indexRetryFailed")));
    } finally {
      setActionBusy(false);
    }
  }

  if (loading) {
    return (
      <p className="lowercase font-pixel min-w-0 max-w-full border-2 border-pure-black bg-surface shadow-block p-4 text-base text-lavender/60 sm:p-6">
        {t("readerPage.loading")}
      </p>
    );
  }

  if (error !== null || detail === null || transcript === null) {
    return (
      <section className="lowercase font-pixel min-w-0 max-w-full border-2 border-pure-black bg-surface shadow-block flex flex-col items-start gap-3 p-4 sm:p-6">
        <h1 className="capitalize text-2xl text-mint">{t("readerPage.title")}</h1>
        <p role="alert" className="lowercase text-mint">
          &gt; {error ?? t("readerPage.unavailable")}
        </p>
        <Link
          to="/gestion/biblioteca"
          className="lowercase font-pixel border-2 border-mint px-3 py-2 text-mint hover:bg-mint hover:text-on-accent"
        >
          {t("readerPage.backToLibrary")}
        </Link>
        <Button type="button" variant="secondary" onClick={() => window.location.reload()}>
          {t("readerPage.retry")}
        </Button>
      </section>
    );
  }

  const classQuality: TaskQualityState = {
    signals: qualitySignals(detail),
    unavailable: false,
    timestamp_precision: detail.transcript?.timestamp_precision ?? null,
    duration_seconds: detail.duration_seconds,
  };

  return (
    <div className="flex flex-col gap-6">
      {notice !== null && (
        <p role="status" className="lowercase border-2 border-mint bg-surface px-3 py-2 text-mint">
          &gt; {notice}
        </p>
      )}

      <TranscriptReader
        detail={detail}
        transcript={transcript}
        activeSegmentId={activeSegmentId}
        status={
          <ClassStatus
            detail={detail}
            busy={actionBusy}
            onBackfill={() => void handleBackfill()}
            onRetryIndex={() => void handleRetryIndex()}
          />
        }
        aside={
          <ClassItems
            tasks={tasks}
            quality={classQuality}
            loading={tasksLoading}
            error={tasksError}
            onRefresh={() => setReloadKey((value) => value + 1)}
          />
        }
      />
    </div>
  );
}
