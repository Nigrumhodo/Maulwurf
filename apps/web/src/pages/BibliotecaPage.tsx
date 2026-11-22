import { useCallback, useEffect, useRef, useState } from "react";
import { Link } from "react-router-dom";
import { AudioCard } from "@/components/library/AudioCard";
import { AudioUploadPanel } from "@/components/upload/AudioUploadPanel";
import { Button } from "@/components/ui/Button";
import { buttonClasses } from "@/components/ui/buttonStyles";
import { Card } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { ApiHttpError } from "@/lib/api";
import { fetchCapabilities } from "@/lib/capabilities";
import type { IngestionCapabilities } from "@/lib/capabilities";
import {
  audioLabel,
  deleteAudio,
  failedStages,
  fetchAudioDetail,
  fetchAudios,
  groupBySubject,
  LIBRARY_LIMIT,
  libraryActionError,
  reuploadAudio,
  retryAudio,
} from "@/lib/library";
import type { LibraryAudio } from "@/lib/library";
import {
  cancelAttempt,
  mapUploadError,
  pickUploadFile,
  uploadContent,
  variantConflict,
} from "@/lib/upload";
import type { AudioCreated, UploadProgress } from "@/lib/upload";

// D2.4 — Biblioteca real (E-S2-DA-06): listado por materia con agregado y estados por
// etapa, aviso permanente de «audio no conservado» y acciones diferenciadas. «Volver a
// subir» solo existe sin transcripción; «reintentar» solo sobre etapas fallidas/cancelled
// de una clase con texto; «eliminar» tombstonea la clase confirmando en ConfirmDialog.
interface ReuploadState {
  audioId: string;
  reservation: AudioCreated;
}

interface Notice {
  tone: "info" | "error";
  message: string;
}

export function BibliotecaPage() {
  const [items, setItems] = useState<LibraryAudio[]>([]);
  const [capabilities, setCapabilities] = useState<IngestionCapabilities | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [notice, setNotice] = useState<Notice | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [deleteTarget, setDeleteTarget] = useState<LibraryAudio | null>(null);

  const [reupload, setReupload] = useState<ReuploadState | null>(null);
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [fileError, setFileError] = useState<string | null>(null);
  const [progress, setProgress] = useState<UploadProgress | null>(null);
  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setLoadError(null);
    try {
      const [nextItems, nextCapabilities] = await Promise.all([
        fetchAudios(),
        // Sin capacidades se puede listar; la validación de archivo queda en el servidor.
        fetchCapabilities().catch(() => null),
      ]);
      setItems(nextItems);
      setCapabilities(nextCapabilities);
    } catch (error) {
      setLoadError(
        error instanceof ApiHttpError
          ? error.message
          : "no se pudo cargar la biblioteca; revisa tu conexión.",
      );
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  useEffect(() => {
    return () => {
      abortRef.current?.abort();
      abortRef.current = null;
    };
  }, []);

  const releaseFile = useCallback(() => {
    setSelectedFile(null);
    setFileError(null);
    setProgress(null);
  }, []);

  function reportActionError(error: unknown) {
    if (error instanceof ApiHttpError) {
      const mapped = libraryActionError(error);
      setNotice({ tone: "error", message: mapped.message });
      if (mapped.refresh === true) void load();
      return;
    }
    setNotice({ tone: "error", message: "la acción falló; revisa tu conexión." });
  }

  async function startReupload(audioId: string) {
    setNotice(null);
    setBusyId(audioId);
    try {
      const reservation = await reuploadAudio(audioId);
      releaseFile();
      setUploadError(null);
      setExpandedId(null);
      setReupload({ audioId, reservation });
    } catch (error) {
      reportActionError(error);
    } finally {
      setBusyId(null);
    }
  }

  async function cancelReupload() {
    abortRef.current?.abort();
    abortRef.current = null;
    const current = reupload;
    releaseFile();
    setUploadError(null);
    setReupload(null);
    if (current !== null) {
      try {
        await cancelAttempt(current.reservation.audio_id, current.reservation.attempt_id);
      } catch {
        // El servidor también cancela al detectar la desconexión.
      }
    }
  }

  async function handleRetry(audio: LibraryAudio) {
    setNotice(null);
    setBusyId(audio.id);
    try {
      // El listado puede estar obsoleto: la versión vigente y las etapas se leen del detalle.
      const detail = await fetchAudioDetail(audio.id);
      if (detail.transcript === null) {
        setNotice({
          tone: "error",
          message: "la clase ya no tiene una transcripción utilizable; vuelve a subir el audio.",
        });
        await load();
        return;
      }
      const stages = failedStages(detail);
      if (stages.length === 0) {
        setNotice({
          tone: "info",
          message: "la clase ya no tiene etapas fallidas; se actualizó la biblioteca.",
        });
        await load();
        return;
      }
      await retryAudio(audio.id, stages, detail.transcript.version);
      setExpandedId(audio.id);
      setNotice({
        tone: "info",
        message: "etapas encoladas · el progreso real aparecerá en esta clase.",
      });
      await load();
    } catch (error) {
      reportActionError(error);
    } finally {
      setBusyId(null);
    }
  }

  async function handleConfirmDelete() {
    const target = deleteTarget;
    if (target === null) return;
    setDeleteTarget(null);
    setNotice(null);
    setBusyId(target.id);
    try {
      const deleted = await deleteAudio(target.id);
      if (reupload?.audioId === target.id) {
        abortRef.current?.abort();
        abortRef.current = null;
        releaseFile();
        setUploadError(null);
        setReupload(null);
      }
      if (expandedId === target.id) setExpandedId(null);
      setNotice({
        tone: "info",
        message: deleted.notice || "clase eliminada de tu biblioteca.",
      });
      await load();
    } catch (error) {
      reportActionError(error);
    } finally {
      setBusyId(null);
    }
  }

  function handleFileChange(file: File | null) {
    const picked = pickUploadFile(file, capabilities);
    setSelectedFile(picked.file);
    setFileError(picked.error);
    setUploadError(null);
  }

  async function handleReuploadUpload() {
    if (reupload === null || selectedFile === null) return;
    const controller = new AbortController();
    abortRef.current = controller;
    setUploading(true);
    setUploadError(null);
    setProgress({ sentBytes: 0, totalBytes: selectedFile.size });
    try {
      const result = await uploadContent({
        uploadUrl: reupload.reservation.upload_url,
        file: selectedFile,
        signal: controller.signal,
        onProgress: setProgress,
      });
      releaseFile();
      setReupload(null);
      setExpandedId(reupload.audioId);
      await load();
      setNotice({
        tone: "info",
        message:
          result.outcome === "accepted"
            ? "audio recibido · la transcripción continuará en esta clase."
            : "el audio ya estaba en la biblioteca; se actualizó la lista.",
      });
    } catch (error) {
      if (controller.signal.aborted) return;
      if (error instanceof ApiHttpError) {
        const conflict = variantConflict(error);
        if (conflict !== null) {
          // La reserva absorbida ya no sirve; la variante solo se confirma desde el formulario.
          releaseFile();
          setReupload(null);
          setNotice({
            tone: "error",
            message:
              "este contenido coincide con otra clase en otro contexto; confírmalo desde «subir clase».",
          });
          await load();
          return;
        }
        const view = mapUploadError(error);
        setUploadError(view.message);
        if (view.resetReservation === true) {
          releaseFile();
          setReupload(null);
          await load();
        }
        return;
      }
      setUploadError("no se pudo enviar el audio; revisa tu conexión.");
    } finally {
      setUploading(false);
      abortRef.current = null;
    }
  }

  const groups = groupBySubject(items);
  const reuploadTarget =
    reupload === null ? null : (items.find((item) => item.id === reupload.audioId) ?? null);

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <h1 className="capitalize text-2xl text-mint">biblioteca</h1>
        <div className="flex flex-wrap items-center gap-2">
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={loading}
            onClick={() => void load()}
          >
            actualizar
          </Button>
          <Link to="/gestion/subir" className={buttonClasses("primary", "sm")}>
            subir clase
          </Link>
        </div>
      </div>

      {notice !== null && (
        <p
          role={notice.tone === "error" ? "alert" : "status"}
          className={`lowercase border-2 px-3 py-2 ${
            notice.tone === "error" ? "border-mint text-mint" : "border-lavender/40 text-lavender"
          }`}
        >
          &gt; {notice.message}
        </p>
      )}

      {loadError !== null && items.length > 0 && (
        <div className="flex flex-wrap items-center gap-2">
          <p role="alert" className="lowercase text-mint">
            &gt; {loadError}
          </p>
          <Button type="button" size="sm" variant="secondary" onClick={() => void load()}>
            reintentar
          </Button>
        </div>
      )}

      {reupload !== null && (
        <Card title="volver a subir" variant="accent">
          <p className="lowercase mb-3 text-lavender/80">
            &gt; clase {reuploadTarget !== null ? audioLabel(reuploadTarget) : reupload.audioId} ·
            reserva hasta {new Date(reupload.reservation.upload_expires_at).toLocaleString()}
          </p>
          <AudioUploadPanel
            capabilities={capabilities}
            selectedFile={selectedFile}
            fileError={fileError}
            progress={progress}
            uploading={uploading}
            formError={uploadError}
            onFileChange={handleFileChange}
            onUpload={() => void handleReuploadUpload()}
            onCancel={() => void cancelReupload()}
          />
        </Card>
      )}

      <Card title="tus clases">
        <p className="lowercase text-sm text-lavender/60">
          &gt; audio no conservado · el audio se borra tras transcribir; solo se guarda el texto.
        </p>

        {loading && items.length === 0 ? (
          <p role="status" className="lowercase mt-3 text-lavender/60">
            &gt; cargando biblioteca…
          </p>
        ) : loadError !== null && items.length === 0 ? (
          <div role="alert" className="mt-3">
            <EmptyState
              title="no se pudo cargar la biblioteca"
              description={loadError}
              action={
                <Button type="button" size="sm" variant="secondary" onClick={() => void load()}>
                  reintentar
                </Button>
              }
            />
          </div>
        ) : groups.length === 0 ? (
          <EmptyState
            className="mt-3"
            title="aún no hay clases"
            description="sube la primera para ver aquí su transcripción y el estado de cada etapa."
            action={
              <Link
                to="/gestion/subir"
                className={buttonClasses("primary", "sm")}
              >
                subir clase
              </Link>
            }
          />
        ) : (
          <div className="mt-3 flex flex-col gap-4">
            {groups.map((group) => (
              <section key={group.id} className="flex flex-col gap-2">
                <h2 className="capitalize font-pixel flex items-center gap-2 text-mint">
                  <span
                    aria-hidden="true"
                    className="inline-block h-3 w-3 border-2 border-pure-black"
                    style={{ backgroundColor: group.color }}
                  />
                  {group.name}
                  <span className="text-lavender/60">({group.items.length})</span>
                </h2>
                <ul className="flex flex-col divide-y-2 divide-lavender/20 border-y-2 border-lavender/20">
                  {group.items.map((audio) => (
                    <AudioCard
                      key={audio.id}
                      audio={audio}
                      expanded={expandedId === audio.id}
                      busy={busyId === audio.id || uploading}
                      onToggleProgress={() =>
                        setExpandedId((current) => (current === audio.id ? null : audio.id))
                      }
                      onRetry={() => void handleRetry(audio)}
                      onReupload={() => void startReupload(audio.id)}
                      onDelete={() => setDeleteTarget(audio)}
                    />
                  ))}
                </ul>
              </section>
            ))}
            {items.length >= LIBRARY_LIMIT && (
              <p className="lowercase text-lavender/60">
                &gt; se muestran hasta {LIBRARY_LIMIT} clases · el contrato S2 aún no ofrece
                paginación.
              </p>
            )}
          </div>
        )}
      </Card>

      <ConfirmDialog
        open={deleteTarget !== null}
        title="eliminar clase"
        description={
          deleteTarget !== null ? (
            <span>
              se eliminará «{audioLabel(deleteTarget)}» junto con su transcripción, fragmentos, citas
              y tareas locales. los eventos ya creados en Google Calendar se conservan.
            </span>
          ) : null
        }
        confirmLabel="sí, eliminar"
        destructive
        onConfirm={() => void handleConfirmDelete()}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
