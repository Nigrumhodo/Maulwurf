// D1.5 — Toast (presentacional). Renderiza un único aviso con tono y botón de
// cierre; el listado/auto-dismiss lo gestiona `ToastProvider`.
export type ToastTone = "info" | "success" | "warning" | "error";

export interface ToastItem {
  id: number;
  message: string;
  tone: ToastTone;
}

const TONE_CLASSES: Record<ToastTone, string> = {
  info: "border-lavender bg-surface text-lavender",
  success: "border-mint bg-mint text-on-accent",
  warning: "border-mint bg-surface text-mint",
  error: "border-mint bg-mint/20 text-mint",
};

const TONE_LABEL: Record<ToastTone, string> = {
  info: "info",
  success: "ok",
  warning: "aviso",
  error: "error",
};

export interface ToastProps {
  item: ToastItem;
  onDismiss: (id: number) => void;
}

export function Toast({ item, onDismiss }: ToastProps) {
  return (
    <div
      role="status"
      className={`lowercase font-pixel flex items-center gap-3 border-2 px-3 py-2 shadow-block ${TONE_CLASSES[item.tone]}`}
    >
      <span className="text-mint" aria-hidden="true">
        &gt;
      </span>
      <span className="flex-1">{item.message}</span>
      <span className="opacity-60" aria-hidden="true">
        [{TONE_LABEL[item.tone]}]
      </span>
      <button
        type="button"
        onClick={() => onDismiss(item.id)}
        aria-label="cerrar aviso"
        className="border-2 border-pure-black px-1 leading-none hover:bg-mint hover:text-on-accent"
      >
        x
      </button>
    </div>
  );
}
