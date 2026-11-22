import { CalendarView } from "@/components/calendar/CalendarView";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

// D3.6 - Agenda: Google events and Maulwurf tasks in one duplicate-free calendar. The
// calendar is read-only; no event is created, moved or deleted from this page.

export function AgendaPage() {
  useDocumentTitle("agenda");
  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-1">
        <h1 className="capitalize text-2xl text-mint">agenda</h1>
        <p className="max-w-prose lowercase text-base text-lavender/80">
          &gt; eventos de google y tareas maulwurf; cada tarea enlazada se dibuja una sola vez.
        </p>
      </div>

      <CalendarView />
    </div>
  );
}
