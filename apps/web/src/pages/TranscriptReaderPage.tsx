import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { ClassItems } from "@/components/transcript/ClassItems";
import { ClassStatus } from "@/components/transcript/ClassStatus";
import { TranscriptReader } from "@/components/TranscriptReader";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
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

// D3.5 - the class page is the reader plus persisted metadata: stages, summary/topics,
// ASR warnings, detected items and the explicit analysis backfill/reanalysis action. It
// never exposes audio, never reuploads and never retranscribes.

function messageFor(error: unknown, fallback: string): string {
  return error instanceof ApiHttpError ? error.message : fallback;
}

export function TranscriptReaderPage() {
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
            ? "la clase o su transcripción no existen."
            : messageFor(cause, "no se pudo cargar la transcripción; revisa tu conexión."),
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
          messageFor(cause, "no se pudieron cargar los elementos detectados."),
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
          ? "el análisis actual ya está completo."
          : "análisis encolado; el estado se actualizará en esta página.",
      );
      setReloadKey((value) => value + 1);
    } catch (cause) {
      setNotice(messageFor(cause, "no se pudo encolar el análisis."));
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
      setNotice("índice encolado; el estado se actualizará en esta página.");
      setReloadKey((value) => value + 1);
    } catch (cause) {
      setNotice(messageFor(cause, "no se pudo reintentar el índice."));
    } finally {
      setActionBusy(false);
    }
  }

  if (loading) {
    return <p className="lowercase text-lavender/60">&gt; cargando transcripción…</p>;
  }

  if (error !== null || detail === null || transcript === null) {
    return (
      <div className="flex flex-col items-start gap-3">
        <Card title="transcripción">
          <p role="alert" className="lowercase text-mint">
            &gt; {error ?? "la transcripción no está disponible."}
          </p>
        </Card>
        <Link
          to="/gestion/biblioteca"
          className="lowercase font-pixel border-2 border-mint px-3 py-2 text-mint hover:bg-mint hover:text-on-accent"
        >
          volver a la biblioteca
        </Link>
        <Button type="button" variant="secondary" onClick={() => window.location.reload()}>
          reintentar
        </Button>
      </div>
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
        <p role="status" className="lowercase border-2 border-mint px-3 py-2 text-mint">
          &gt; {notice}
        </p>
      )}

      <ClassStatus
        detail={detail}
        busy={actionBusy}
        onBackfill={() => void handleBackfill()}
        onRetryIndex={() => void handleRetryIndex()}
      />

      <TranscriptReader
        detail={detail}
        transcript={transcript}
        activeSegmentId={activeSegmentId}
      />

      <ClassItems
        tasks={tasks}
        quality={classQuality}
        loading={tasksLoading}
        error={tasksError}
        onRefresh={() => setReloadKey((value) => value + 1)}
      />
    </div>
  );
}
