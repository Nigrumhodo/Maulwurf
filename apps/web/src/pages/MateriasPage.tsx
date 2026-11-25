import { useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
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
  const { t } = useTranslation("plan");
  useDocumentTitle(t("subjects.title"));
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
      toast(error instanceof Error ? error.message : t("subjects.loadFailed"), "error");
    } finally {
      if (generation === loadGeneration.current) setLoading(false);
    }
  }, [toast, t]);

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
      toast(t("subjects.created"), "success");
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
      toast(t("subjects.updated"), "success");
    } finally {
      setBusy(false);
    }
  }

  async function handleDelete(subject: Subject) {
    setBusy(true);
    try {
      await apiFetch(`/subjects/${subject.id}`, { method: "DELETE" });
      setSubjects((current) => current.filter((item) => item.id !== subject.id));
      toast(t("subjects.deleted"), "success");
    } catch (error) {
      if (error instanceof ApiHttpError && error.code === "subject_has_active_audios") {
        const classCount =
          typeof error.details.class_count === "number" ? error.details.class_count : 0;
        setActiveAudios({ name: subject.name, classCount });
      } else {
        toast(error instanceof Error ? error.message : t("subjects.deleteFailed"), "error");
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
            <h1 className="capitalize text-2xl text-mint">{t("subjects.title")}</h1>
            <p className="text-base text-lavender/80 max-w-prose">
              &gt; {t("subjects.subtitle")}
            </p>
          </div>
          {form === null && !showEmptyState && (
            <Button onClick={() => setForm({ mode: "create" })}>{t("subjects.new")}</Button>
          )}
        </header>

        {!loading && !loadFailed && subjects.length > 0 && (
          <p className="border-t-2 border-lavender/20 px-4 py-2 text-base text-lavender/80">
            {t("subjects.summary", {
              subjects: t("subjects.subjectCount", { count: subjects.length }),
              ready: stats.readyClassCount,
              total: stats.classCount,
              minutes: stats.transcribedMinutes,
              confirmed: stats.confirmedTaskCount,
              pending: stats.pendingTaskCount,
            })}
          </p>
        )}

        {form !== null && (
          <div className="flex flex-col gap-3 border-t-2 border-lavender/20 p-4">
            <h2 className="capitalize text-xl text-mint">
              {editing != null ? t("subjects.editing", { name: editing.name }) : t("subjects.new")}
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
                submitLabel={editing != null ? t("subjects.save") : t("subjects.create")}
                busy={busy}
                onSubmit={editing != null ? handleUpdate : handleCreate}
                onCancel={() => setForm(null)}
              />
            </div>
          </div>
        )}

        <div className="border-t-2 border-lavender/20">
          {loading ? (
            <p className="p-4 text-lavender/60">&gt; {t("subjects.loading")}</p>
          ) : loadFailed ? (
            <div className="p-4">
              <EmptyState
                illustration="error"
                title={t("subjects.loadFailed")}
                description={t("subjects.loadFailedHint")}
                action={
                  <Button variant="secondary" onClick={() => void load()}>
                    {t("subjects.retry")}
                  </Button>
                }
              />
            </div>
          ) : subjects.length === 0 ? (
            <div className="p-4">
              <EmptyState
                illustration="library"
                title={t("subjects.emptyTitle")}
                description={t("subjects.emptyHint")}
                action={
                  form === null ? (
                    <Button variant="secondary" onClick={() => setForm({ mode: "create" })}>
                      {t("subjects.createSubject")}
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
                <span>{t("subjects.columns.subject")}</span>
                <span>{t("subjects.columns.classes")}</span>
                <span>{t("subjects.columns.stats")}</span>
                <span className="text-right">{t("subjects.columns.actions")}</span>
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
                        {t("subjects.classCount", { count: subject.class_count })}
                      </span>
                      <span className="basis-full text-base text-lavender/80 lg:basis-auto">
                        {item === undefined
                          ? statsLoading
                            ? "…"
                            : t("subjects.noData")
                          : t("subjects.rowStats", {
                              ready: item.readyClasses,
                              total: item.totalClasses,
                              minutes: item.transcribedMinutes,
                              confirmed: item.confirmedTasks,
                              pending: item.pendingTasks,
                            })}
                      </span>
                      <span className="ml-auto flex items-center gap-2">
                        <Button
                          size="sm"
                          variant="secondary"
                          onClick={() => setForm({ mode: "edit", subject })}
                        >
                          {t("subjects.edit")}
                        </Button>
                        <span className="ml-4 border-l-2 border-lavender/30 pl-4">
                          <Button
                            size="sm"
                            variant="danger"
                            disabled={busy}
                            onClick={() => void handleDelete(subject)}
                          >
                            {t("subjects.delete")}
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
            {t("subjects.privacy")}
          </span>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            disabled={statsLoading}
            onClick={() => void reloadStats()}
          >
            {statsLoading ? t("subjects.refreshing") : t("subjects.refresh")}
          </Button>
        </footer>
      </section>

      <ConfirmDialog
        open={activeAudios !== null}
        title={t("subjects.activeDialog.title")}
        description={
          activeAudios != null
            ? t("subjects.activeDialog.description", {
                name: activeAudios.name,
                count: activeAudios.classCount,
              })
            : ""
        }
        confirmLabel={t("subjects.activeDialog.confirm")}
        cancelLabel={t("subjects.activeDialog.cancel")}
        onConfirm={() => setActiveAudios(null)}
        onCancel={() => setActiveAudios(null)}
      />
    </div>
  );
}
