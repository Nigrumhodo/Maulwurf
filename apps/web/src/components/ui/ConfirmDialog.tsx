import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";

// D1.6 — Diálogo de confirmación/movimiento (contrato A1.6): borrar una materia
// con clases activas muestra un diálogo en lugar de fallar en silencio. Genérico
// para reutilizarse en otros flujos de confirmación.
export interface ConfirmDialogProps {
  open: boolean;
  title: string;
  description?: ReactNode;
  confirmLabel?: string;
  cancelLabel?: string;
  destructive?: boolean;
  confirmDisabled?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel,
  cancelLabel,
  destructive = false,
  confirmDisabled = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
  const { t } = useTranslation("common");
  if (!open) return null;

  return (
    <div
      className="fixed inset-0 z-[80] flex items-center justify-center bg-black/50 p-4"
      role="dialog"
      aria-modal="true"
      aria-label={title}
    >
      <div className="lowercase font-pixel w-full max-w-md border-2 border-pure-black bg-surface shadow-block">
        <header className="flex items-center justify-between border-b-2 border-pure-black bg-mint px-3 py-2 text-on-accent">
          <span className="capitalize font-bold">› {title}</span>
          <button
            type="button"
            onClick={onCancel}
            aria-label={t("dialog.close")}
            className="inline-flex min-h-8 min-w-8 items-center justify-center border-2 border-on-accent/40 px-1 leading-none hover:bg-on-accent hover:text-mint"
          >
            x
          </button>
        </header>
        <div className="p-4 text-lavender">{description}</div>
        <footer className="flex items-center justify-end gap-2 border-t-2 border-pure-black px-3 py-2">
          <Button type="button" variant="secondary" onClick={onCancel}>
            {cancelLabel ?? t("dialog.cancel")}
          </Button>
          <Button
            type="button"
            variant={destructive ? "danger" : "primary"}
            disabled={confirmDisabled}
            onClick={onConfirm}
          >
            {confirmLabel ?? t("dialog.confirm")}
          </Button>
        </footer>
      </div>
    </div>
  );
}
