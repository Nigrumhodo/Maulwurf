import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useAuth } from "@/components/auth/AuthProvider";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { Input } from "@/components/ui/Input";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { ApiHttpError } from "@/lib/api";
import {
  fetchDeletionStatus,
  requestAccountDeletion,
  resumeAccountDeletion,
} from "@/lib/auth";
import type { DeletionOperation } from "@/lib/auth";

// D4.2 / A4.4 — Observable and resumable account deletion panel.
// `delete_maulwurf_events` stays `false` unless explicitly opted in. Once deletion starts,
// the initiating session becomes restricted to status checks, resume, and logout.

export function AccountDeletionPanel() {
  const { t } = useTranslation("settings");
  const confirmWordExpected = t("deletion.confirmWord");
  const { logout } = useAuth();
  const [deleteEvents, setDeleteEvents] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmWord, setConfirmWord] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [operation, setOperation] = useState<DeletionOperation | null>(null);
  const [operationId, setOperationId] = useState<string | null>(null);

  async function handleConfirmDeletion(): Promise<void> {
    if (confirmWord.trim().toUpperCase() !== confirmWordExpected) return;
    setBusy(true);
    setError(null);
    let accepted: Awaited<ReturnType<typeof requestAccountDeletion>>;
    try {
      accepted = await requestAccountDeletion(deleteEvents);
    } catch (err) {
      setError(
        err instanceof ApiHttpError
          ? err.message
          : t("deletion.startFailed"),
      );
      setDialogOpen(false);
      setBusy(false);
      return;
    }
    setOperationId(accepted.deletion_operation_id);
    setDialogOpen(false);
    setConfirmWord("");
    try {
      setOperation(await fetchDeletionStatus(accepted.deletion_operation_id));
    } catch {
      setError(t("deletion.statusAfterStartFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function handleRefreshStatus(): Promise<void> {
    if (operationId === null || busy) return;
    setBusy(true);
    setError(null);
    try {
      setOperation(await fetchDeletionStatus(operationId));
    } catch {
      setError(t("deletion.statusFailed"));
    } finally {
      setBusy(false);
    }
  }

  async function handleResume(): Promise<void> {
    if (operationId === null || busy) return;
    setBusy(true);
    setError(null);
    try {
      setOperation(await resumeAccountDeletion(operationId));
    } catch {
      setError(t("deletion.resumeFailed"));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div>
      <div className="lowercase flex flex-col gap-3 text-lavender">
        <p className="text-lavender/80">
          {t("deletion.explainer")}
        </p>

        {operation === null && operationId === null ? (
          <>
            <label className="flex min-h-10 cursor-pointer items-center gap-2">
              <input
                type="checkbox"
                checked={deleteEvents}
                disabled={busy}
                onChange={(event) => setDeleteEvents(event.target.checked)}
              />
              <span>
                {t("deletion.deleteEvents")}
              </span>
            </label>

            <div>
              <Button
                type="button"
                variant="danger"
                disabled={busy}
                onClick={() => {
                  setConfirmWord("");
                  setError(null);
                  setDialogOpen(true);
                }}
              >
                {t("deletion.request")}
              </Button>
            </div>
          </>
        ) : (
          <div className="flex flex-col gap-3 border-2 border-mint bg-surface p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-bold text-mint">{t("deletion.operation", { id: operationId })}</span>
              {operation !== null && <StatusBadge status={operation.status} />}
            </div>

            {operation !== null && (
              <>
                <p className="text-sm text-lavender/80">
                  {t("deletion.calendarEvents")}{" "}
                  {operation.delete_maulwurf_events
                    ? t("deletion.eventsDeleted")
                    : t("deletion.eventsKept")}
                  .
                </p>
                {operation.error_summary !== null && (
                  <p role="alert" className="text-sm text-mint">
                    &gt; {t("deletion.remoteDetail")} {operation.error_summary}
                  </p>
                )}
              </>
            )}

            <div className="flex flex-wrap gap-2">
              <Button
                type="button"
                size="sm"
                variant="secondary"
                disabled={busy}
                onClick={() => void handleRefreshStatus()}
              >
                {t("deletion.checkStatus")}
              </Button>
              {(operation === null ||
                operation.status === "pending" ||
                operation.status === "running") && (
                <Button
                  type="button"
                  size="sm"
                  variant="primary"
                  disabled={busy}
                  onClick={() => void handleResume()}
                >
                  {t("deletion.resume")}
                </Button>
              )}
              <Button type="button" size="sm" variant="danger" onClick={() => void logout()}>
                {t("deletion.logout")}
              </Button>
            </div>
          </div>
        )}

        {error !== null && (
          <p role="alert" className="text-mint">
            &gt; {error}
          </p>
        )}
      </div>

      <ConfirmDialog
        open={dialogOpen}
        destructive
        title={t("deletion.dialogTitle")}
        confirmLabel={t("deletion.dialogConfirm")}
        cancelLabel={t("deletion.dialogCancel")}
        confirmDisabled={confirmWord.trim().toUpperCase() !== confirmWordExpected || busy}
        description={
          <div className="flex flex-col gap-3">
            <p>
              {t("deletion.dialogBody")}{" "}
              {deleteEvents ? t("deletion.dialogEventsDeleted") : t("deletion.dialogEventsKept")}
            </p>
            <Input
              label={t("deletion.typeToConfirm", { word: confirmWordExpected })}
              value={confirmWord}
              onChange={(event) => setConfirmWord(event.target.value)}
              placeholder={confirmWordExpected}
            />
          </div>
        }
        onConfirm={() => void handleConfirmDeletion()}
        onCancel={() => {
          setDialogOpen(false);
          setConfirmWord("");
        }}
      />
    </div>
  );
}
