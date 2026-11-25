import { useCallback, useEffect, useRef, useState } from "react";
import { Trans, useTranslation } from "react-i18next";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { StatusBadge } from "@/components/ui/StatusBadge";
import i18n, { intlLocale } from "@/i18n";
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
    return new Intl.DateTimeFormat(intlLocale(), {
      dateStyle: "medium",
      timeStyle: "short",
    }).format(date);
  } catch {
    return iso;
  }
}

export function DeliveryPanel() {
  const { t } = useTranslation("settings");
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
          : i18n.t("settings:deliveries.loadFailed"),
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
      setError(t("deliveries.loadMoreFailed"));
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
          : t("deliveries.resendFailed"),
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
          <Trans
            t={t}
            i18nKey="deliveries.explainer"
            components={{ strong: <strong /> }}
          />
        </p>

        <div className="flex flex-wrap items-center justify-between gap-2">
          <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label={t("deliveries.filterLabel")}>
            <Button
              type="button"
              size="sm"
              variant={filter === "all" ? "primary" : "secondary"}
              onClick={() => setFilter("all")}
            >
              {t("deliveries.all")}
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
            {t("deliveries.refresh")}
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
          <p className="text-lavender/60">&gt; {t("deliveries.loading")}</p>
        ) : items.length === 0 ? (
          <EmptyState
            title={t("deliveries.emptyTitle")}
            description={t("deliveries.emptyHint")}
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
                        {t("deliveries.seq", { seq: item.occurrence_seq })}
                      </span>
                    )}
                  </div>
                  <span className="text-base text-lavender/60">
                    {t("deliveries.attempts", {
                      attempts: item.attempts,
                      scheduled: formatStamp(item.scheduled_at),
                    })}
                  </span>
                </div>

                <p className="text-sm text-lavender/80">
                  {notificationStatusExplanation(item.status, item.last_error_code)}
                </p>

                <div className="flex flex-wrap items-center justify-between gap-2 text-base text-lavender/60">
                  <span>
                    {item.sent_at !== null
                      ? t("deliveries.sentAt", { at: formatStamp(item.sent_at) })
                      : t("deliveries.occurrence", { occurrence: item.occurrence ?? "—" })}
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
                      {resendingId === item.id ? t("deliveries.recording") : t("deliveries.resend")}
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
              {loadingMore ? t("deliveries.loadingMore") : t("deliveries.loadMore")}
            </Button>
          </div>
        )}
      </div>

      <ConfirmDialog
        open={pendingTarget !== null}
        title={t("deliveries.dialog.title")}
        confirmLabel={t("deliveries.dialog.confirm")}
        cancelLabel={t("deliveries.dialog.cancel")}
        confirmDisabled={!duplicateAck || resendingId !== null}
        description={
          pendingTarget !== null ? (
            <div className="flex flex-col gap-3">
              <p>
                <Trans
                  t={t}
                  i18nKey="deliveries.dialog.body"
                  values={{ status: pendingTarget.status }}
                  components={{ strong: <strong /> }}
                />
              </p>
              <label className="flex cursor-pointer items-start gap-2 border-2 border-lavender/40 p-2 text-lavender">
                <input
                  type="checkbox"
                  checked={duplicateAck}
                  onChange={(event) => setDuplicateAck(event.target.checked)}
                />
                <span>{t("deliveries.dialog.ack")}</span>
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
