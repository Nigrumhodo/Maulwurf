import { useState } from "react";
import { useAuth } from "@/components/auth/AuthProvider";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
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
  const { logout } = useAuth();
  const [deleteEvents, setDeleteEvents] = useState(false);
  const [dialogOpen, setDialogOpen] = useState(false);
  const [confirmWord, setConfirmWord] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [operation, setOperation] = useState<DeletionOperation | null>(null);
  const [operationId, setOperationId] = useState<string | null>(null);

  async function handleConfirmDeletion(): Promise<void> {
    if (confirmWord.trim().toUpperCase() !== "ELIMINAR") return;
    setBusy(true);
    setError(null);
    let accepted: Awaited<ReturnType<typeof requestAccountDeletion>>;
    try {
      accepted = await requestAccountDeletion(deleteEvents);
    } catch (err) {
      setError(
        err instanceof ApiHttpError
          ? err.message
          : "no se pudo iniciar el borrado de cuenta.",
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
      setError("el borrado se inició, pero no se pudo consultar el estado; actualízalo.");
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
      setError("no se pudo consultar el estado del borrado.");
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
      setError("no se pudo reanudar el borrado de cuenta.");
    } finally {
      setBusy(false);
    }
  }

  return (
    <Card title="zona de peligro · eliminar cuenta" variant="outline" className="mt-6">
      <div className="lowercase flex flex-col gap-3 text-lavender">
        <p className="text-lavender/80">
          elimina de forma irreversible tus materias, clases, transcripciones, embeddings,
          conversaciones, tareas y tokens OAuth. se conserva solo un registro residual de auditoría
          sin perfil ni texto durante 30 días.
        </p>

        {operation === null && operationId === null ? (
          <>
            <label className="flex min-h-10 cursor-pointer items-center gap-2 border-2 border-lavender/30 p-3">
              <input
                type="checkbox"
                checked={deleteEvents}
                disabled={busy}
                onChange={(event) => setDeleteEvents(event.target.checked)}
              />
              <span>
                eliminar también los eventos creados por maulwurf en google calendar (si no lo
                marcas, esos eventos permanecerán en tu calendario)
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
                solicitar eliminación de cuenta
              </Button>
            </div>
          </>
        ) : (
          <div className="flex flex-col gap-3 border-2 border-mint bg-surface p-3">
            <div className="flex flex-wrap items-center justify-between gap-2">
              <span className="font-bold text-mint">operación {operationId}</span>
              {operation !== null && <StatusBadge status={operation.status} />}
            </div>

            {operation !== null && (
              <>
                <p className="text-sm text-lavender/80">
                  eventos de google calendar:{" "}
                  {operation.delete_maulwurf_events ? "borrado autorizado" : "conservados"}.
                </p>
                {operation.error_summary !== null && (
                  <p role="alert" className="text-sm text-mint">
                    &gt; detalle remoto: {operation.error_summary}
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
                comprobar estado
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
                  reanudar borrado
                </Button>
              )}
              <Button type="button" size="sm" variant="danger" onClick={() => void logout()}>
                cerrar sesión
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
        title="eliminar cuenta definitivamente"
        confirmLabel="eliminar cuenta"
        cancelLabel="cancelar"
        confirmDisabled={confirmWord.trim().toUpperCase() !== "ELIMINAR" || busy}
        description={
          <div className="flex flex-col gap-3">
            <p>
              esta acción bloqueará tu sesión de inmediato y purgará todos los textos, citas y
              credenciales.{" "}
              {deleteEvents
                ? "se intentará borrar también los eventos creados por Maulwurf en Google Calendar."
                : "tus eventos en Google Calendar se conservarán."}
            </p>
            <Input
              label="escribe ELIMINAR para confirmar"
              value={confirmWord}
              onChange={(event) => setConfirmWord(event.target.value)}
              placeholder="ELIMINAR"
            />
          </div>
        }
        onConfirm={() => void handleConfirmDeletion()}
        onCancel={() => {
          setDialogOpen(false);
          setConfirmWord("");
        }}
      />
    </Card>
  );
}
