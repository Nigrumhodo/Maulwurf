import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { StatusBadge } from "@/components/ui/StatusBadge";
import i18n from "@/i18n";
import { ApiHttpError } from "@/lib/api";
import {
  calendarTargetLabel,
  fetchIntegrationsStatus,
} from "@/lib/integrations";
import type { GoogleStatus } from "@/lib/integrations";
import {
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
  onActionComplete?: (task: Task, notice: string) => void;
  onEdit?: (task: Task) => void;
  onClose: () => void;
}

function errorMessage(error: unknown): string {
  if (error instanceof ApiHttpError) return error.firstFieldMessage()?.message ?? error.message;
  return i18n.t("plan:review.opFailed");
}

function noticeFor(task: Task): string {
  if (task.status === "dismissed") return i18n.t("plan:review.notice.dismissed");
  if (task.status === "completed") return i18n.t("plan:review.notice.completed");
  if (task.calendar_sync_status === "blocked") {
    return i18n.t("plan:review.notice.blocked");
  }
  if (task.calendar_sync_status === "pending") {
    return i18n.t("plan:review.notice.pending");
  }
  if (task.calendar_sync_status === "synced") return i18n.t("plan:review.notice.synced");
  return i18n.t("plan:review.notice.confirmed");
}

export function TaskReviewDialog({
  task,
  onUpdated,
  onActionComplete,
  onEdit,
  onClose,
}: TaskReviewDialogProps) {
  const { t } = useTranslation("plan");
  const [currentTask, setCurrentTask] = useState(task);
  const [google, setGoogle] = useState<GoogleStatus | null>(null);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape" && !busy) {
        onClose();
      }
    }
    window.addEventListener("keydown", handleKeyDown);
    return () => {
      window.removeEventListener("keydown", handleKeyDown);
    };
  }, [busy, onClose]);

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
          setError(`${errorMessage(cause)} ${t("review.checkFailed")}`);
        }
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [task.id, t]);

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
      const message = noticeFor(result.task);
      setCurrentTask(result.task);
      onUpdated(result.task);
      setNotice(message);
      onActionComplete?.(result.task, message);
    } catch (cause) {
      if (cause instanceof ApiHttpError && cause.status === 409) {
        setError(t("review.conflictConfirm"));
        await refreshAfterConflict();
      } else {
        setError(errorMessage(cause));
      }
    } finally {
      setBusy(false);
    }
  }

  async function handleDismiss(): Promise<void> {
    setBusy(true);
    setError(null);
    setNotice(null);
    try {
      const result = await dismissTask(currentTask.id, currentTask.version);
      const message =
        result.notice === "calendar_event_kept"
          ? `${noticeFor(result.task)} ${t("review.eventKept")}`
          : noticeFor(result.task);
      setCurrentTask(result.task);
      onUpdated(result.task);
      setNotice(message);
      onActionComplete?.(result.task, message);
    } catch (cause) {
      if (cause instanceof ApiHttpError && cause.status === 409) {
        setError(t("review.conflictDismiss"));
        await refreshAfterConflict();
      } else {
        setError(errorMessage(cause));
      }
    } finally {
      setBusy(false);
    }
  }

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-dark/80 p-4"
      onClick={(event) => {
        if (event.target === event.currentTarget && !busy) {
          onClose();
        }
      }}
    >
      <div className="w-full max-w-2xl">
      <Card
        title={t("review.title")}
        variant="accent"
        role="dialog"
        aria-modal="true"
        aria-label={t("review.title")}
        onClose={busy ? undefined : onClose}
        className="max-h-[90vh] w-full overflow-y-auto"
      >
        <div className="min-w-0 flex flex-col gap-4">
          {loading ? (
            <p className="capitalize text-lavender/60">&gt; {t("review.checking")}</p>
          ) : null}

          <div className="flex flex-wrap items-center gap-2">
            <StatusBadge status={currentTask.status} />
            <StatusBadge status={currentTask.calendar_sync_status} />
            {hasPendingRevision && (
              <span className="capitalize border-2 border-mint px-2 py-0.5 text-mint">
                {t("review.willConfirm")}
              </span>
            )}
          </div>

          <section aria-label={t("review.whatWillBeWritten")} className="min-w-0 flex flex-col gap-2">
            <h2 className="capitalize text-mint">{t("review.whatWillBeWritten")}</h2>
            <dl className="grid gap-3 sm:grid-cols-2">
              <div className="min-w-0">
                <dt className="capitalize text-sm text-lavender/60">{t("review.fields.title")}</dt>
                <dd className="normal-case wrap-anywhere break-words text-lavender">
                  {effective.titulo}
                </dd>
              </div>
              <div className="min-w-0">
                <dt className="capitalize text-sm text-lavender/60">{t("review.fields.type")}</dt>
                <dd className="normal-case wrap-anywhere break-words text-lavender">
                  {effective.tipo}
                </dd>
              </div>
              <div className="min-w-0">
                <dt className="capitalize text-sm text-lavender/60">{t("review.fields.due")}</dt>
                <dd className="normal-case wrap-anywhere break-words text-lavender">
                  {due ??
                    (effective.date_status === "resolved"
                      ? t("review.invalidDate")
                      : t("review.noResolvedDate"))}
                </dd>
              </div>
              <div className="min-w-0">
                <dt className="capitalize text-sm text-lavender/60">{t("review.fields.timezone")}</dt>
                <dd className="normal-case wrap-anywhere break-words text-lavender">
                  {effective.timezone}
                </dd>
              </div>
              <div className="min-w-0">
                <dt className="capitalize text-sm text-lavender/60">{t("review.fields.agenda")}</dt>
                <dd className="normal-case wrap-anywhere break-words text-lavender">
                  {eventModeLabel(effective)}
                  {block !== null ? ` · ${block}` : ""}
                </dd>
              </div>
              <div className="min-w-0">
                <dt className="capitalize text-sm text-lavender/60">{t("review.fields.target")}</dt>
                <dd
                  className="normal-case wrap-anywhere break-words text-lavender"
                  title={google?.calendar_id ?? undefined}
                >
                  {google === null
                    ? t("review.calendarUnknown")
                    : calendarTargetLabel(google)}
                </dd>
              </div>
            </dl>
            {effective.detalle !== null && effective.detalle.trim() !== "" && (
              <p className="normal-case wrap-anywhere break-words text-lavender/80">
                {effective.detalle}
              </p>
            )}
            {hasPendingRevision && (
              <p className="capitalize text-sm text-lavender/60">
                {t("review.previewHint")}
              </p>
            )}
            {!closed && effective.date_status !== "resolved" && (
              <p className="normal-case text-base text-mint">
                &gt; {t("review.needsDate")}
              </p>
            )}
          </section>

          {notice !== null && (
            <p role="status" className="capitalize border-2 border-mint px-3 py-2 text-mint">
              {notice}
            </p>
          )}
          {error !== null && (
            <p role="alert" className="wrap-anywhere break-words border-2 border-mint px-3 py-2 text-mint">
              {error}
            </p>
          )}

          <div className="flex flex-wrap items-center gap-2">
            {!closed && (
              <Button
                type="button"
                size="sm"
                variant="primary"
                className="min-h-8"
                disabled={busy || loading || !canConfirm}
                onClick={() => void handleConfirm()}
              >
                {t("review.confirm")}
              </Button>
            )}
            {!closed && onEdit !== undefined && (
              <Button
                type="button"
                size="sm"
                variant="secondary"
                className="min-h-8"
                disabled={busy || loading}
                onClick={() => onEdit(currentTask)}
              >
                {t("review.edit")}
              </Button>
            )}
            {canDismiss && (
              <Button
                type="button"
                size="sm"
                variant="danger"
                className="min-h-8 sm:ml-auto"
                disabled={busy || loading}
                onClick={() => void handleDismiss()}
              >
                {t("review.dismiss")}
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              className="min-h-8"
              disabled={busy}
              onClick={onClose}
            >
              {t("review.close")}
            </Button>
          </div>
        </div>
      </Card>
      </div>
    </div>
  );
}

