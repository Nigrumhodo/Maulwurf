import { useEffect, useState } from "react";
import { Link, useParams, useSearchParams } from "react-router-dom";
import { TranscriptReader } from "@/components/TranscriptReader";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ApiHttpError } from "@/lib/api";
import { fetchAudioDetail } from "@/lib/library";
import type { LibraryAudioDetail } from "@/lib/library";
import { fetchTranscriptReader } from "@/lib/reader";
import type { ReaderTranscript } from "@/lib/reader";

// D2.5 — Página `/biblioteca/:audioId?segment=…`. Las citas backend ya usan esa ruta,
// así que es el destino estable del lector; el segmento es solo un id, nunca un token.
export function TranscriptReaderPage() {
  const { audioId = "" } = useParams();
  const [searchParams] = useSearchParams();
  const [detail, setDetail] = useState<LibraryAudioDetail | null>(null);
  const [transcript, setTranscript] = useState<ReaderTranscript | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
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
          cause instanceof ApiHttpError
            ? cause.status === 404
              ? "la clase o su transcripción no existen."
              : cause.message
            : "no se pudo cargar la transcripción; revisa tu conexión.",
        );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [audioId]);

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

  return (
    <TranscriptReader
      detail={detail}
      transcript={transcript}
      activeSegmentId={activeSegmentId}
    />
  );
}
