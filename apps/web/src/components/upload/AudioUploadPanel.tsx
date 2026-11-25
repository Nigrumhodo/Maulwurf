import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { RetroVisual } from "@/components/ui/RetroVisual";
import {
  loaderCassetteAmbar,
  loaderCassetteAmbarStatic,
  loaderCassetteLavanda,
  loaderCassetteLavandaStatic,
} from "@/components/Visual";
import type { IngestionCapabilities } from "@/lib/capabilities";
import { acceptedFileInputAccept, formatBytes } from "@/lib/upload";
import type { UploadProgress } from "@/lib/upload";

// D2.4 — Panel de envío reutilizable por D2.1/D2.2 y por «volver a subir» de la biblioteca.
// Es presentacional: el padre conserva la reserva, la orquestación, la cancelación y el
// manejo de variantes. Aquí solo se elige el archivo y se pinta el progreso real de bytes.
export interface AudioUploadPanelProps {
  capabilities: IngestionCapabilities | null;
  selectedFile: File | null;
  fileError: string | null;
  progress: UploadProgress | null;
  uploading: boolean;
  formError: string | null;
  onFileChange: (file: File | null) => void;
  onUpload: () => void;
  onCancel: () => void;
  uploadLabel?: string;
  cancelLabel?: string;
}

export function AudioUploadPanel({
  capabilities,
  selectedFile,
  fileError,
  progress,
  uploading,
  formError,
  onFileChange,
  onUpload,
  onCancel,
  uploadLabel,
  cancelLabel,
}: AudioUploadPanelProps) {
  const { t } = useTranslation("study");
  const percent =
    progress !== null && progress.totalBytes > 0
      ? Math.min(100, Math.round((progress.sentBytes / progress.totalBytes) * 100))
      : 0;

  return (
    <div className="lowercase flex flex-col gap-4">
      <label className="capitalize font-pixel flex flex-col gap-1">
        <span className="text-mint">{t("audioUpload.fileLabel")}</span>
        <input
          type="file"
          accept={acceptedFileInputAccept(capabilities)}
          onChange={(event) => onFileChange(event.target.files?.[0] ?? null)}
          disabled={uploading}
          className="bg-surface text-lavender border-2 border-lavender px-3 py-2 file:mr-3 file:border-2 file:border-mint file:bg-transparent file:px-2 file:py-1 file:font-pixel file:text-mint"
        />
      </label>

      {selectedFile !== null && (
        <p className="text-lavender/60">
          &gt; {selectedFile.name} · {formatBytes(selectedFile.size)} ·{" "}
          {selectedFile.type || t("audioUpload.unknownType")}
        </p>
      )}

      {fileError !== null && (
        <p role="alert" className="text-mint">
          &gt; {fileError}
        </p>
      )}

      {uploading && progress !== null && (
        <div className="flex items-center gap-3 border-2 border-lavender/30 bg-dark p-3">
          <div className="shrink-0">
            <span className="img-dark-only">
              <RetroVisual
                src={loaderCassetteLavanda}
                staticSrc={loaderCassetteLavandaStatic}
                className="h-12 w-auto object-contain"
              />
            </span>
            <span className="img-light-only">
              <RetroVisual
                src={loaderCassetteAmbar}
                staticSrc={loaderCassetteAmbarStatic}
                className="h-12 w-auto object-contain"
              />
            </span>
          </div>
          <div className="flex min-w-0 flex-1 flex-col gap-1">
            <p className="text-lavender/80">
              {t("audioUpload.sending", {
                sent: formatBytes(progress.sentBytes),
                total: formatBytes(progress.totalBytes),
              })}
            </p>
            <div
              role="progressbar"
              aria-label={t("audioUpload.progressAria")}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={percent}
              className="h-3 border-2 border-mint bg-surface"
            >
              <div className="h-full bg-mint" style={{ width: `${percent}%` }} />
            </div>
            <p className="text-lavender/60">{t("audioUpload.realProgress")}</p>
          </div>
        </div>
      )}

      {formError !== null && (
        <p role="alert" className="text-mint">
          &gt; {formError}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {uploading ? (
          <Button type="button" variant="danger" onClick={onCancel}>
            {t("audioUpload.cancelUpload")}
          </Button>
        ) : (
          <>
            <Button type="button" disabled={selectedFile === null} onClick={onUpload}>
              {uploadLabel ?? t("audioUpload.send")}
            </Button>
            <Button type="button" variant="secondary" onClick={onCancel}>
              {cancelLabel ?? t("audioUpload.discard")}
            </Button>
          </>
        )}
      </div>

      <p className="text-lavender/60">
        {t("audioUpload.notKept")}
      </p>
    </div>
  );
}
