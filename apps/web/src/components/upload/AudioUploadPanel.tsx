import { Button } from "@/components/ui/Button";
import type { IngestionCapabilities } from "@/lib/capabilities";
import { acceptedFileExtensions, formatBytes } from "@/lib/upload";
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
  uploadLabel = "enviar audio",
  cancelLabel = "descartar reserva",
}: AudioUploadPanelProps) {
  const percent =
    progress !== null && progress.totalBytes > 0
      ? Math.min(100, Math.round((progress.sentBytes / progress.totalBytes) * 100))
      : 0;

  return (
    <div className="lowercase flex flex-col gap-4">
      <label className="lowercase font-pixel flex flex-col gap-1">
        <span className="text-mint">&gt; archivo de audio</span>
        <input
          type="file"
          accept={acceptedFileExtensions(capabilities).join(",")}
          onChange={(event) => onFileChange(event.target.files?.[0] ?? null)}
          disabled={uploading}
          className="bg-surface text-lavender border-2 border-lavender px-3 py-2 file:mr-3 file:border-2 file:border-mint file:bg-transparent file:px-2 file:py-1 file:font-pixel file:text-mint"
        />
      </label>

      {selectedFile !== null && (
        <p className="text-lavender/60">
          &gt; {selectedFile.name} · {formatBytes(selectedFile.size)} ·{" "}
          {selectedFile.type || "tipo desconocido"}
        </p>
      )}

      {fileError !== null && (
        <p role="alert" className="text-mint">
          &gt; {fileError}
        </p>
      )}

      {uploading && progress !== null && (
        <div className="flex flex-col gap-1">
          <p className="text-lavender/70">
            &gt; enviando {formatBytes(progress.sentBytes)} de {formatBytes(progress.totalBytes)}
          </p>
          <div
            role="progressbar"
            aria-label="audio enviado"
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={percent}
            className="h-3 border-2 border-mint bg-surface"
          >
            <div className="h-full bg-mint" style={{ width: `${percent}%` }} />
          </div>
          <p className="text-lavender/50">&gt; progreso real de bytes enviados</p>
        </div>
      )}

      {formError !== null && (
        <p role="alert" className="border-2 border-mint px-3 py-2 text-mint">
          &gt; {formError}
        </p>
      )}

      <div className="flex flex-wrap items-center gap-2">
        {uploading ? (
          <Button type="button" variant="danger" onClick={onCancel}>
            cancelar subida
          </Button>
        ) : (
          <>
            <Button type="button" disabled={selectedFile === null} onClick={onUpload}>
              {uploadLabel}
            </Button>
            <Button type="button" variant="secondary" onClick={onCancel}>
              {cancelLabel}
            </Button>
          </>
        )}
      </div>

      <p className="text-lavender/60">
        &gt; audio no conservado · se borra al terminar; solo queda la transcripción.
      </p>
    </div>
  );
}
