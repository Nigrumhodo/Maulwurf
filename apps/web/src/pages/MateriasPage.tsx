import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "@/components/ui/Button";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/ToastProvider";
import { SubjectForm } from "@/components/subjects/SubjectForm";
import { useSubjectStats } from "@/components/subjects/useSubjectStats";
import { apiFetch, ApiHttpError } from "@/lib/api";
import { createSubject, fetchSubjects, updateSubject } from "@/lib/subjects";
import type { Subject, SubjectDraft } from "@/lib/subjects";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

// D1.6 — CRUD de materias contra la API real (A1.6 / M2a §3.4): lista del tenant,
// alta, edición y borrado. Borrar una materia con clases activas muestra el
// diálogo del contrato (409 `subject_has_active_audios`); el borrado forzado sigue
// diferido a S2.
type FormState = { mode: "create" } | { mode: "edit"; subject: Subject } | null;

export function MateriasPage() {
  useDocumentTitle("materias");
  const { toast } = useToast();
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [loading, setLoading] = useState(true);
  const [loadFailed, setLoadFailed] = useState(false);
  const [form, setForm] = useState<FormState>(null);
  const [busy, setBusy] = useState(false);
  const [activeAudios, setActiveAudios] = useState<{ name: string; classCount: number } | null>(
    null,
  );

  const { stats, loading: statsLoading, reload: reloadStats } = useSubjectStats(subjects);
  const statsById = new Map(stats.subjects.map((item) => [item.id, item]));

  const loadGeneration = useRef(0);

  const load = useCallback(async () => {
    const generation = ++loadGeneration.current;
    setLoading(true);
    setLoadFailed(false);
    try {
      const next = await fetchSubjects();
      if (generation !== loadGeneration.current) return;
      setSubjects(next);
    } catch (error) {
      if (generation !== loadGeneration.current) return;
      setLoadFailed(true);
      toast(error instanceof Error ? error.message : "no se pudieron cargar las materias", "error");
    } finally {
      if (generation === loadGeneration.current) setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void load();
    return () => {
      loadGeneration.current += 1;
    };
  }, [load]);

  async function handleCreate(draft: SubjectDraft) {
    setBusy(true);
    try {
      const created = await createSubject(draft);
      setSubjects((current) => [...current.filter((subject) => subject.id !== created.id), created]);
      setForm(null);
      toast("materia creada", "success");
    } finally {
      setBusy(false);
    }
  }

  async function handleUpdate(draft: SubjectDraft) {
    if (form?.mode !== "edit") return;
    const subjectId = form.subject.id;
    setBusy(true);
    try {
      const updated = await updateSubject(subjectId, draft);
      setSubjects((current) =>
        current.map((subject) => (subject.id === updated.id ? updated : subject)),
      );
      setForm(null);
      toast("materia actualizada", "success");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(subject: Subject) {
    setBusy(true);
    try {
      await apiFetch(`/subjects/${subject.id}`, { method: "DELETE" });
      setSubjects((current) => current.filter((item) => item.id !== subject.id));
      toast("materia eliminada", "success");
    } catch (error) {
      if (error instanceof ApiHttpError && error.code === "subject_has_active_audios") {
        const classCount =
          typeof error.details.class_count === "number" ? error.details.class_count : 0;
        setActiveAudios({ name: subject.name, classCount });
      } else {
        toast(error instanceof Error ? error.message : "no se pudo eliminar la materia", "error");
      }
    } finally {
      setBusy(false);
    }
  }

  const showEmptyState = !loading && !loadFailed && subjects.length === 0;
  const editing = form?.mode === "edit" ? form.subject : null;

  const gridCols = "lg:grid lg:grid-cols-[minmax(0,2fr)_5rem_minmax(0,3fr)_auto] lg:items-center";

  return (
    <div className="flex flex-col gap-6">
      <section className="lowercase flex flex-col border-2 border-pure-black bg-surface shadow-block">
        <header className="flex flex-wrap items-start justify-between gap-3 p-4">
          <div className="flex flex-col gap-1">
            <h1 className="capitalize text-2xl text-mint">materias</h1>
            <p className="text-base text-lavender/80 max-w-prose">
              &gt; tus materias, con el estado de sus clases y tareas.
            </p>
          </div>
          {form === null && !showEmptyState && (
            <Button onClick={() => setForm({ mode: "create" })}>nueva materia</Button>
          )}
        </header>

        {!loading && !loadFailed && subjects.length > 0 && (
          <p className="border-t-2 border-lavender/20 px-4 py-2 text-base text-lavender/80">
            {subjects.length} {subjects.length === 1 ? "materia" : "materias"} ·{" "}
            {stats.readyClassCount}/{stats.classCount} clases listas · {stats.transcribedMinutes} min
            · {stats.confirmedTaskCount} confirmadas / {stats.pendingTaskCount} pendientes
          </p>
        )}

        {form !== null && (
          <div className="flex flex-col gap-3 border-t-2 border-lavender/20 p-4">
            <h2 className="capitalize text-xl text-mint">
              {editing != null ? `editar · ${editing.name}` : "nueva materia"}
            </h2>
            <div className="max-w-md">
              <SubjectForm
                // Remount when the edited subject changes: useState initializers only run
                // on mount, so without this key editing B reused A's values and PATCHed B
                // with them.
                key={editing?.id ?? "create"}
                initial={
                  editing != null
                    ? { name: editing.name, color: editing.color, teacher: editing.teacher ?? "" }
                    : undefined
                }
                submitLabel={editing != null ? "guardar" : "crear"}
                busy={busy}
                onSubmit={editing != null ? handleUpdate : handleCreate}
                onCancel={() => setForm(null)}
              />
            </div>
          </div>
        )}

        <div className="border-t-2 border-lavender/20">
          {loading ? (
            <p className="p-4 text-lavender/60">&gt; cargando…</p>
          ) : loadFailed ? (
            <div className="p-4">
              <EmptyState
                title="no se pudieron cargar las materias"
                description="revisa tu conexión e inténtalo de nuevo."
                action={
                  <Button variant="secondary" onClick={() => void load()}>
                    reintentar
                  </Button>
                }
              />
            </div>
          ) : subjects.length === 0 ? (
            <div className="p-4">
              <EmptyState
                title="aún no tienes materias"
                description="crea la primera para empezar a subir clases."
                action={
                  form === null ? (
                    <Button variant="secondary" onClick={() => setForm({ mode: "create" })}>
                      crear materia
                    </Button>
                  ) : undefined
                }
              />
            </div>
          ) : (
            <>
              <div
                aria-hidden="true"
                className={`hidden gap-4 border-b-2 border-lavender/20 px-4 py-2 text-sm text-lavender/60 ${gridCols}`}
              >
                <span>materia</span>
                <span>clases</span>
                <span>listas · minutos · tareas</span>
                <span className="text-right">acciones</span>
              </div>
              <ul className="flex flex-col divide-y-2 divide-lavender/20">
                {subjects.map((subject) => {
                  const item = statsById.get(subject.id);
                  return (
                    <li
                      key={subject.id}
                      className={`flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-3 ${gridCols}`}
                    >
                      <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                        <span
                          aria-hidden="true"
                          className="inline-block h-4 w-4 shrink-0"
                          style={{ backgroundColor: subject.color }}
                        />
                        <span className="min-w-0 wrap-anywhere text-lg text-lavender">
                          {subject.name}
                        </span>
                        {subject.teacher != null && subject.teacher !== "" && (
                          <span className="min-w-0 wrap-anywhere text-lavender/80">
                            {subject.teacher}
                          </span>
                        )}
                      </span>
                      <span className="text-lavender/80">
                        {subject.class_count} {subject.class_count === 1 ? "clase" : "clases"}
                      </span>
                      <span className="basis-full text-base text-lavender/80 lg:basis-auto">
                        {item === undefined
                          ? statsLoading
                            ? "…"
                            : "sin datos"
                          : `${item.readyClasses}/${item.totalClasses} listas · ${item.transcribedMinutes} min · ${item.confirmedTasks} confirmadas / ${item.pendingTasks} pendientes`}
                      </span>
                      <span className="ml-auto flex items-center gap-2">
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => setForm({ mode: "edit", subject })}
                        >
                          editar
                        </Button>
                        <span className="ml-4 border-l-2 border-lavender/30 pl-4">
                          <Button
                            size="sm"
                            variant="danger"
                            disabled={busy}
                            onClick={() => void handleDelete(subject)}
                          >
                            eliminar
                          </Button>
                        </span>
                      </span>
                    </li>
                  );
                })}
              </ul>
            </>
          )}
        </div>

        <footer className="flex flex-wrap items-center justify-between gap-2 border-t-2 border-lavender/20 px-4 py-2">
          <span className="text-sm text-lavender/80">
            solo metadatos y texto · audio original no conservado (g1)
          </span>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={statsLoading}
            onClick={() => void reloadStats()}
          >
            {statsLoading ? "actualizando…" : "actualizar"}
          </Button>
        </footer>
      </section>

      <ConfirmDialog
        open={activeAudios !== null}
        title="materia con clases activas"
        description={
          activeAudios != null
            ? `«${activeAudios.name}» tiene ${activeAudios.classCount} ${
                activeAudios.classCount === 1 ? "clase activa" : "clases activas"
              }. elimina o mueve esas clases primero; el borrado forzado estará disponible más adelante.`
            : ""
        }
        confirmLabel="entendido"
        cancelLabel="cerrar"
        onConfirm={() => setActiveAudios(null)}
        onCancel={() => setActiveAudios(null)}
      />
    </div>
  );
}
