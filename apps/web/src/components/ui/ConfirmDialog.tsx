import type { ReactNode } from "react";
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
  onConfirm: () => void;
  onCancel: () => void;
}

export function ConfirmDialog({
  open,
  title,
  description,
  confirmLabel = "confirmar",
  cancelLabel = "cancelar",
  destructive = false,
  onConfirm,
  onCancel,
}: ConfirmDialogProps) {
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
          <span className="font-bold">› {title}</span>
          <button
            type="button"
            onClick={onCancel}
            aria-label="cerrar"
            className="border-2 border-on-accent/40 px-1 leading-none hover:bg-on-accent hover:text-mint"
          >
            x
          </button>
        </header>
        <div className="p-4 text-lavender">{description}</div>
        <footer className="flex items-center justify-end gap-2 border-t-2 border-pure-black px-3 py-2">
          <Button type="button" variant="secondary" onClick={onCancel}>
            {cancelLabel}
          </Button>
          <Button type="button" variant={destructive ? "danger" : "primary"} onClick={onConfirm}>
            {confirmLabel}
          </Button>
        </footer>
      </div>
    </div>
  );
}
