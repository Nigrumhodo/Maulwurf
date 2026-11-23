import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { StatusBadge } from "@/components/ui/StatusBadge";
import { ApiHttpError } from "@/lib/api";
import {
  NOTIFICATION_STATUSES,
  canResendNotification,
  fetchNotifications,
  notificationStatusExplanation,
  notificationTypeLabel,
  resendNotification,
} from "@/lib/notifications";
import type { NotificationItem, NotificationStatus } from "@/lib/notifications";

// D4.2 — Delivery history panel (`GET /notifications`) + duplicate-acknowledged resend.
// Never mutates `delivery_unknown` in place: manual resend creates a new occurrence row.

const PAGE_LIMIT = 20;

function formatStamp(iso: string | null): string {
  if (iso === null) return "—";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  try {
    return new Intl.DateTimeFormat("es", {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(date);
  } catch {
    return iso;
  }
}

export function DeliveryPanel() {
  const [filter, setFilter] = useState<NotificationStatus | "all">("all");
  const [items, setItems] = useState<NotificationItem[]>([]);
  const [nextCursor, setNextCursor] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingMore, setLoadingMore] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [pendingTarget, setPendingTarget] = useState<NotificationItem | null>(null);
  const [duplicateAck, setDuplicateAck] = useState(false);
  const [resendingId, setResendingId] = useState<string | null>(null);
  const loadGenRef = useRef(0);
  const mountedRef = useRef(true);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      loadGenRef.current += 1;
    };
  }, []);

  const loadInitial = useCallback(async (selected: NotificationStatus | "all") => {
    const generation = ++loadGenRef.current;
    setLoading(true);
    setError(null);
    try {
      const page = await fetchNotifications({
        statuses: selected === "all" ? undefined : [selected],
        limit: PAGE_LIMIT,
      });
      if (!mountedRef.current || generation !== loadGenRef.current) return;
      setItems(page.items);
      setNextCursor(page.next_cursor);
    } catch (err) {
      if (!mountedRef.current || generation !== loadGenRef.current) return;
      setError(
        err instanceof ApiHttpError
          ? err.message
          : "no se pudo cargar el historial de notificaciones.",
      );
    } finally {
      if (mountedRef.current && generation === loadGenRef.current) {
        setLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    void loadInitial(filter);
  }, [filter, loadInitial]);

  async function handleLoadMore(): Promise<void> {
    if (nextCursor === null || loadingMore) return;
    const generation = loadGenRef.current;
    setLoadingMore(true);
    setError(null);
    try {
      const page = await fetchNotifications({
        statuses: filter === "all" ? undefined : [filter],
        limit: PAGE_LIMIT,
        cursor: nextCursor,
      });
      if (!mountedRef.current || generation !== loadGenRef.current) return;
      setItems((current) => [...current, ...page.items]);
      setNextCursor(page.next_cursor);
    } catch {
      if (!mountedRef.current || generation !== loadGenRef.current) return;
      setError("no se pudieron cargar más entregas.");
    } finally {
      if (mountedRef.current && generation === loadGenRef.current) {
        setLoadingMore(false);
      }
    }
  }

  async function handleConfirmResend(): Promise<void> {
    if (pendingTarget === null || !duplicateAck) return;
    const targetId = pendingTarget.id;
    setResendingId(targetId);
    setError(null);
    setNotice(null);
    try {
      const created = await resendNotification(targetId);
      setPendingTarget(null);
      setDuplicateAck(false);
      setNotice(created.warning.message);
      await loadInitial(filter);
    } catch (err) {
      setError(
        err instanceof ApiHttpError
          ? err.message
          : "no se pudo registrar el reenvío manual.",
      );
      setPendingTarget(null);
      setDuplicateAck(false);
    } finally {
      setResendingId(null);
    }
  }

  return (
    <div>
      <div className="lowercase flex flex-col gap-4">
        <p className="text-lavender/80">
          gmail no ofrece garantía de entrega única: si una respuesta se pierde o expira el lease,
          la fila queda en <strong>delivery_unknown</strong> (estado terminal, sin reintento
          automático).
        </p>

        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="filtrar por estado">
            <Button
              type="button"
              size="sm"
              variant={filter === "all" ? "primary" : "secondary"}
              onClick={() => setFilter("all")}
            >
              todos
            </Button>
            {NOTIFICATION_STATUSES.map((status) => (
              <Button
                key={status}
                type="button"
                size="sm"
                variant={filter === status ? "primary" : "secondary"}
                onClick={() => setFilter(status)}
              >
                {status}
              </Button>
            ))}
          </div>

          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={loading}
            onClick={() => void loadInitial(filter)}
          >
            actualizar
          </Button>
        </div>

        {notice !== null && (
          <p role="status" className="border-2 border-mint bg-surface px-3 py-2 text-mint">
            &gt; {notice}
          </p>
        )}

        {error !== null && (
          <p role="alert" className="border-2 border-mint bg-mint/10 px-3 py-2 text-mint">
            &gt; {error}
          </p>
        )}

        {loading ? (
          <p className="text-lavender/60">&gt; cargando historial de notificaciones…</p>
        ) : items.length === 0 ? (
          <EmptyState
            title="sin notificaciones registradas"
            description="cuando se programen recordatorios, resúmenes o pruebas verás aquí cada intento auditado."
          />
        ) : (
          <ul className="flex flex-col divide-y-2 divide-lavender/20 border-y-2 border-lavender/20">
            {items.map((item) => (
              <li key={item.id} className="flex flex-col gap-2 py-3">
                <div className="flex flex-wrap items-center justify-between gap-2">
                  <div className="flex flex-wrap items-center gap-2">
                    <span className="font-bold text-mint">{notificationTypeLabel(item.type)}</span>
                    <StatusBadge status={item.status} />
                    {item.occurrence_seq > 0 && (
                      <span className="border border-lavender/40 px-1.5 text-xs text-lavender/80">
                        sec #{item.occurrence_seq}
                      </span>
                    )}
                  </div>
                  <span className="text-base text-lavender/60">
                    intentos: {item.attempts} · programado: {formatStamp(item.scheduled_at)}
                  </span>
                </div>

                <p className="text-sm text-lavender/80">
                  {notificationStatusExplanation(item.status, item.last_error_code)}
                </p>

                <div className="flex flex-wrap items-center justify-between gap-2 text-base text-lavender/60">
                  <span>
                    {item.sent_at !== null
                      ? `enviado: ${formatStamp(item.sent_at)}`
                      : `ocurrencia: ${item.occurrence ?? "—"}`}
                  </span>
                  {canResendNotification(item.status) && (
                    <Button
                      type="button"
                      size="sm"
                      variant="secondary"
                      disabled={resendingId === item.id}
                      onClick={() => {
                        setDuplicateAck(false);
                        setPendingTarget(item);
                      }}
                    >
                      {resendingId === item.id ? "registrando…" : "reenviar manualmente"}
                    </Button>
                  )}
                </div>
              </li>
            ))}
          </ul>
        )}

        {nextCursor !== null && (
          <div>
            <Button
              type="button"
              size="sm"
              variant="secondary"
              disabled={loadingMore}
              onClick={() => void handleLoadMore()}
            >
              {loadingMore ? "cargando…" : "cargar más"}
            </Button>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={pendingTarget !== null}
        title="confirmar reenvío con riesgo de duplicado"
        confirmLabel="crear nuevo envío"
        cancelLabel="cancelar"
        confirmDisabled={!duplicateAck || resendingId !== null}
        description={
          pendingTarget !== null ? (
            <div className="flex flex-col gap-3">
              <p>
                se creará una <strong>nueva ocurrencia</strong> pendiente sin modificar el registro
                original ({pendingTarget.status}). si gmail procesó la entrega anterior, podrías
                recibir un correo duplicado.
              </p>
              <label className="flex cursor-pointer items-start gap-2 border-2 border-lavender/40 p-2 text-lavender">
                <input
                  type="checkbox"
                  checked={duplicateAck}
                  onChange={(event) => setDuplicateAck(event.target.checked)}
                />
                <span>entiendo el riesgo de envío duplicado y deseo registrar un nuevo intento</span>
              </label>
            </div>
          ) : undefined
        }
        onConfirm={() => void handleConfirmResend()}
        onCancel={() => {
          setPendingTarget(null);
          setDuplicateAck(false);
        }}
      />
    </div>
  );
}
