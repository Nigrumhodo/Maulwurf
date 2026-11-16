import { useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { ApiHttpError } from "@/lib/api";
import {
  calendarTargetLabel,
  fetchIntegrationsStatus,
} from "@/lib/integrations";
import type { GoogleStatus } from "@/lib/integrations";
import {
  completeTask,
  confirmTask,
  dismissTask,
  effectiveTask,
  eventModeLabel,
  fetchTask,
  formatEventBlock,
  formatTaskDue,
} from "@/lib/tasks";
import type { Task } from "@/lib/tasks";

// D3.3 �?" the human-in-the-loop confirmation. The dialog shows the effective task (active
// fields plus any pending revision) and the Calendar target before writing anything. It
// never calls Google: confirming only enqueues the outbox through the API.

export interface TaskReviewDialogProps {
  task: Task;
  onUpdated: (task: Task) => void;
  onClose: () => void;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiHttpError) {
    const fields = error.details.fields;
    if (typeof fields === "object" && fields !== null && !Array.isArray(fields)) {
      const first = Object.values(fields)[0];
      if (typeof first === "string" && first !== "") return first;
    }
    return error.message;
  }
  return "no se pudo completar la operación; revisa tu conexión.";
}

function noticeFor(task: Task): string {
  if (task.status === "dismissed") return "Tarea descartada.";
  if (task.status === "completed") return "Tarea completada.";
  if (task.calendar_sync_status === "blocked") {
    return "Confirmada; la sincronización queda bloqueada hasta conectar Calendar.";
  }
  if (task.calendar_sync_status === "pending") {
    return "Confirmada; el envío a Calendar queda pendiente.";
  }
  if (task.calendar_sync_status === "synced") return "Confirmada y sincronizada.";
  return "Tarea confirmada.";
}

export function TaskReviewDialog({ task, onUpdated, onClose }: TaskReviewDialogProps) {
  const [currentTask, setCurrentTask] = useState(task);
  const [google, setGoogle] = useState<GoogleStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    Promise.all([fetchTask(task.id), fetchIntegrationsStatus()])
      .then(([fresh, status]) => {
        if (cancelled) return;
        setCurrentTask(fresh);
        setGoogle(status.google);
      })
      .catch((cause: unknown) => {
        if (!cancelled) {
          setError(
            `${errorMessage(cause)} No se pudo comprobar el calendario; cierra y vuelve a intentar.`,
          );
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [task.id]);

  const effective = effectiveTask(currentTask);
  const due = formatTaskDue(effective);
  const block = formatEventBlock(effective);
  const hasPendingRevision = currentTask.pending_revision !== null;
  const closed = currentTask.status === "dismissed" || currentTask.status === "completed";
  const canConfirm =
    !closed &&
    google !== null &&
    (currentTask.status === "pending" ||
      currentTask.status === "needs_review" ||
      (currentTask.status === "confirmed" && hasPendingRevision));
  const canDismiss = !closed;
  const canComplete = currentTask.status === "confirmed";

  async function refreshAfterConflict(): Promise<void> {
    try {
      const fresh = await fetchTask(currentTask.id);
      setCurrentTask(fresh);
      onUpdated(fresh);
      const status = await fetchIntegrationsStatus();
      setGoogle(status.google);
    } catch {
      // Keep the last known preview; the error already tells the user to retry.
    }
  }

  async function handleConfirm(): Promise<void> {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await confirmTask(
        currentTask.id,
        currentTask.version,
        currentTask.pending_revision?.id ?? null,
      );
      setCurrentTask(result.task);
      onUpdated(result.task);
      setNotice(noticeFor(result.task));
    } catch (cause) {
      if (cause instanceof ApiHttpError && cause.status === 409) {
        setError("La tarea cambió; se actualizó la vista. Revisa el preview antes de confirmar.");
        await refreshAfterConflict();
      } else {
        setError(errorMessage(cause));
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleClose(action: "dismiss" | "complete"): Promise<void> {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result =
        action === "dismiss"
          ? await dismissTask(currentTask.id, currentTask.version)
          : await completeTask(currentTask.id, currentTask.version);
      setCurrentTask(result.task);
      onUpdated(result.task);
      setNotice(
        result.notice === "calendar_event_kept"
          ? `${noticeFor(result.task)} El evento de Calendar se conserva.`
          : noticeFor(result.task),
      );
    } catch (cause) {
      if (cause instanceof ApiHttpError && cause.status === 409) {
        setError("La tarea cambió; se actualizó la vista. Revisa antes de continuar.");
        await refreshAfterConflict();
      } else {
        setError(errorMessage(cause));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card
      title="revisar tarea"
      variant="accent"
      role="dialog"
      aria-modal="true"
      aria-label="revisar tarea"
    >
      <div className="flex flex-col gap-4">
        {loading ? (
          <p className="capitalize text-lavender/60">&gt; comprobando la tarea y el calendario…</p>
        ) : null}

        <div className="flex flex-wrap items-center gap-2">
          <StatusBadge status={currentTask.status} />
          <StatusBadge status={currentTask.calendar_sync_status} />
          {hasPendingRevision && (
            <span className="capitalize border-2 border-mint px-2 py-0.5 text-mint">
              se confirmará el cambio pendiente
            </span>
          )}
        </div>

        <section aria-label="qué se escribirá" className="flex flex-col gap-2">
          <h2 className="capitalize text-mint">qué se escribirá</h2>
          <dl className="grid gap-2 sm:grid-cols-2">
            <div>
              <dt className="capitalize text-xs text-lavender/60">título</dt>
              <dd className="normal-case text-lavender">{effective.titulo}</dd>
            </div>
            <div>
              <dt className="capitalize text-xs text-lavender/60">tipo</dt>
              <dd className="normal-case text-lavender">{effective.tipo}</dd>
            </div>
            <div>
              <dt className="capitalize text-xs text-lavender/60">vencimiento</dt>
              <dd className="normal-case text-lavender">
                {due ?? (effective.date_status === "resolved" ? "fecha inválida" : "sin fecha resuelta")}
              </dd>
            </div>
            <div>
              <dt className="capitalize text-xs text-lavender/60">zona horaria</dt>
              <dd className="normal-case text-lavender">{effective.timezone}</dd>
            </div>
            <div>
              <dt className="capitalize text-xs text-lavender/60">agenda</dt>
              <dd className="normal-case text-lavender">
                {eventModeLabel(effective)}
                {block !== null ? ` · ${block}` : ""}
              </dd>
            </div>
            <div>
              <dt className="capitalize text-xs text-lavender/60">calendario destino</dt>
              <dd className="normal-case text-lavender">
                {google === null ? "no se pudo comprobar el calendario" : calendarTargetLabel(google)}
              </dd>
            </div>
          </dl>
          {effective.detalle !== null && effective.detalle.trim() !== "" && (
            <p className="normal-case text-lavender/80">{effective.detalle}</p>
          )}
          {hasPendingRevision && (
            <p className="capitalize text-xs text-lavender/60">
              el preview usa los campos de la revisión pendiente, no solo los activos.
            </p>
          )}
        </section>

        {notice !== null && (
          <p role="status" className="capitalize border-2 border-mint px-3 py-2 text-mint">
            {notice}
          </p>
        )}
        {error !== null && (
          <p role="alert" className="border-2 border-mint px-3 py-2 text-mint">
            {error}
          </p>
        )}

        <div className="flex flex-wrap gap-2">
          {!closed && (
            <Button
              type="button"
              size="sm"
              variant="primary"
              disabled={busy || loading || !canConfirm}
              onClick={() => void handleConfirm()}
            >
              confirmar
            </Button>
          )}
          {canDismiss && (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              disabled={busy || loading}
              onClick={() => void handleClose("dismiss")}
            >
              descartar
            </Button>
          )}
          {canComplete && (
            <Button
              type="button"
              size="sm"
              variant="secondary"
              disabled={busy || loading}
              onClick={() => void handleClose("complete")}
            >
              completar
            </Button>
          )}
          <Button type="button" size="sm" variant="ghost" disabled={busy} onClick={onClose}>
            cerrar
          </Button>
        </div>
      </div>
    </Card>
  );
}

