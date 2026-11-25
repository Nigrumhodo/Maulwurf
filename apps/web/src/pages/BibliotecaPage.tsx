import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { AudioCard } from "@/components/library/AudioCard";
import { AudioUploadPanel } from "@/components/upload/AudioUploadPanel";
import { Button } from "@/components/ui/Button";
import { buttonClasses } from "@/components/ui/buttonStyles";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { intlLocale } from "@/i18n";
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
import { useDocumentTitle } from "@/lib/useDocumentTitle";

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
  const { t } = useTranslation("study");
  useDocumentTitle(t("libraryPage.docTitle"));
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
          : t("libraryPage.loadFailed"),
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
    setNotice({ tone: "error", message: t("libraryPage.actionFailed") });
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
          message: t("libraryPage.noUsableTranscript"),
        });
        await load();
        return;
      }
      const stages = failedStages(detail);
      if (stages.length === 0) {
        setNotice({
          tone: "info",
          message: t("libraryPage.noFailedStages"),
        });
        await load();
        return;
      }
      await retryAudio(audio.id, stages, detail.transcript.version);
      setExpandedId(audio.id);
      setNotice({
        tone: "info",
        message: t("libraryPage.stagesQueued"),
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
        message: deleted.notice || t("libraryPage.deleted"),
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
            ? t("libraryPage.audioReceived")
            : t("libraryPage.audioAlready"),
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
            message: t("libraryPage.variantConflict"),
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
      setUploadError(t("libraryPage.sendFailed"));
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
      <section className="lowercase font-pixel min-w-0 max-w-full border-2 border-pure-black bg-surface shadow-block">
        <header className="flex flex-wrap items-start justify-between gap-x-4 gap-y-3 p-4 sm:p-6">
          <div className="flex min-w-0 flex-col gap-1">
            <h1 className="capitalize text-2xl text-mint">{t("libraryPage.title")}</h1>
            <p className="text-base text-lavender/80 max-w-prose">
              {t("libraryPage.audioNotKept")}
            </p>
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <Button
              type="button"
              size="sm"
              variant="ghost"
              disabled={loading}
              onClick={() => void load()}
            >
              {t("libraryPage.refresh")}
            </Button>
            <Link to="/gestion/subir" className={buttonClasses("primary", "sm")}>
              {t("libraryPage.upload")}
            </Link>
          </div>
        </header>

        <div className="flex flex-col divide-y-2 divide-lavender/20 border-t-2 border-lavender/30">
          {notice !== null && (
            <p
              role={notice.tone === "error" ? "alert" : "status"}
              className={`px-4 py-3 sm:px-6 ${
                notice.tone === "error" ? "text-mint" : "text-lavender"
              }`}
            >
              &gt; {notice.message}
            </p>
          )}

          {loadError !== null && items.length > 0 && (
            <div className="flex flex-wrap items-center gap-2 px-4 py-3 sm:px-6">
              <p role="alert" className="text-mint">
                &gt; {loadError}
              </p>
              <Button type="button" size="sm" variant="secondary" onClick={() => void load()}>
                {t("libraryPage.retry")}
              </Button>
            </div>
          )}

          {reupload !== null && (
            <section aria-label={t("libraryPage.reupload")} className="flex flex-col gap-3 p-4 sm:p-6">
              <h2 className="capitalize text-xl text-mint">{t("libraryPage.reupload")}</h2>
              <p className="text-base text-lavender/80">
                {t("libraryPage.reuploadInfo", {
                  title: reuploadTarget !== null ? audioLabel(reuploadTarget) : reupload.audioId,
                  until: new Date(reupload.reservation.upload_expires_at).toLocaleString(
                    intlLocale(),
                  ),
                })}
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
            </section>
          )}

          {loading && items.length === 0 ? (
            <p role="status" className="p-4 text-base text-lavender/60 sm:p-6">
              {t("libraryPage.loading")}
            </p>
          ) : loadError !== null && items.length === 0 ? (
            <div role="alert" className="p-4 sm:p-6">
              <EmptyState
                illustration="error"
                title={t("libraryPage.loadFailed")}
                description={loadError}
                action={
                  <Button type="button" size="sm" variant="secondary" onClick={() => void load()}>
                    {t("libraryPage.retry")}
                  </Button>
                }
              />
            </div>
          ) : groups.length === 0 ? (
            <div className="p-4 sm:p-6">
              <EmptyState
                illustration="library"
                title={t("libraryPage.emptyTitle")}
                description={t("libraryPage.emptyDescription")}
                action={
                  <Link to="/gestion/subir" className={buttonClasses("primary", "sm")}>
                    {t("libraryPage.upload")}
                  </Link>
                }
              />
            </div>
          ) : (
            <>
              {groups.map((group) => (
                <section key={group.id} className="flex flex-col gap-2 p-4 sm:p-6">
                  <h2 className="capitalize flex items-center gap-2 text-xl text-mint">
                    <span
                      aria-hidden="true"
                      className="inline-block h-3 w-3 shrink-0 border-2 border-pure-black"
                      style={{ backgroundColor: group.color }}
                    />
                    {group.name}
                    <span className="text-base text-lavender/60">({group.items.length})</span>
                  </h2>
                  <ul className="flex flex-col divide-y-2 divide-lavender/20">
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
                <p className="px-4 py-3 text-base text-lavender/60 sm:px-6">
                  {t("libraryPage.limitNote", { limit: LIBRARY_LIMIT })}
                </p>
              )}
            </>
          )}
        </div>
      </section>

      <ConfirmDialog
        open={deleteTarget !== null}
        title={t("libraryPage.deleteTitle")}
        description={
          deleteTarget !== null ? (
            <span>{t("libraryPage.deleteDescription", { title: audioLabel(deleteTarget) })}</span>
          ) : null
        }
        confirmLabel={t("libraryPage.deleteConfirm")}
        cancelLabel={t("shared.cancel")}
        destructive
        onConfirm={() => void handleConfirmDelete()}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  );
}
