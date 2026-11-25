import { useTranslation } from "react-i18next";
import { CalendarView } from "@/components/calendar/CalendarView";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

// D3.6 - Agenda: Google events and Maulwurf tasks in one duplicate-free calendar. The
// calendar is read-only; no event is created, moved or deleted from this page.

export function AgendaPage() {
  const { t } = useTranslation("plan");
  useDocumentTitle(t("agenda.title"));
  return (
    <div className="flex flex-col gap-4 border-2 border-pure-black bg-surface p-4 shadow-block sm:p-6">
      <header className="flex flex-col gap-1 border-b-2 border-lavender/30 pb-4">
        <h1 className="capitalize text-2xl text-mint">{t("agenda.title")}</h1>
        <p className="max-w-prose lowercase text-base text-lavender/80">
          &gt; {t("agenda.subtitle")}
        </p>
      </header>

      <CalendarView />
    </div>
  );
}
