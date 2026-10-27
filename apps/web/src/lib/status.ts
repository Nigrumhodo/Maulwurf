// D1.5 — Valores del contrato de estados (ESPECIFICACION §1.4 / DISENO §2.10).
//
// `StatusBadge` renderiza el valor tal cual llega (nunca se "traduce" a texto
// amigable); el tono es solo visual. Este mapa centraliza los tonos de TODOS los
// estados tipados del contrato, incluidos `transcript_committed_cleanup_pending`
// y los agregados `not_requested`, `processing`, `ready`, `partial`, `failed`,
// `needs_review`.
export type StatusTone = "muted" | "info" | "active" | "success" | "warning" | "error";

export const STATUS_TONES: Record<string, StatusTone> = {
  // Intento de ingesta (M2 · ingestion_attempts.status)
  awaiting_upload: "muted",
  receiving: "active",
  transcribing: "active",
  transcript_committed_cleanup_pending: "warning",
  succeeded: "success",
  requires_reupload: "error",
  rejected: "error",
  cancelled: "muted",

  // Etapa de texto (processing_runs.status): index | analyze
  not_requested: "muted",
  pending: "warning",
  running: "active",
  failed: "error",

  // Agregado derivado (M2/M9)
  processing: "active",
  ready: "success",
  partial: "warning",

  // Tarea (M4 · tasks.status) y fecha
  needs_review: "warning",
  confirmed: "info",
  dismissed: "muted",
  completed: "success",

  // calendar_sync_status (M5)
  synced: "success",
  blocked: "error",

  // Notificaciones (M8) y chat (M7)
  sending: "active",
  sent: "success",
  delivery_unknown: "warning",
  streaming: "active",
};

/** Tono de un estado; `muted` para valores fuera del contrato (se muestran igual). */
export function statusTone(status: string): StatusTone {
  return STATUS_TONES[status] ?? "muted";
}
