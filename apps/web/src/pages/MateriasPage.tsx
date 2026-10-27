import { useCallback, useEffect, useState } from "react";
import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { ConfirmDialog } from "@/components/ui/ConfirmDialog";
import { EmptyState } from "@/components/ui/EmptyState";
import { useToast } from "@/components/ui/ToastProvider";
import { SubjectForm } from "@/components/subjects/SubjectForm";
import { apiFetch, ApiHttpError } from "@/lib/api";
import type { Subject, SubjectDraft } from "@/lib/subjects";

// D1.6 — CRUD de materias contra la API real (A1.6 / M2a §3.4): lista del tenant,
// alta, edición y borrado. Borrar una materia con clases activas muestra el
// diálogo del contrato (409 `subject_has_active_audios`); el borrado forzado sigue
// diferido a S2.
type FormState = { mode: "create" } | { mode: "edit"; subject: Subject } | null;

export function MateriasPage() {
  const { toast } = useToast();
  const [subjects, setSubjects] = useState<Subject[]>([]);
  const [loading, setLoading] = useState(true);
  const [form, setForm] = useState<FormState>(null);
  const [busy, setBusy] = useState(false);
  const [activeAudios, setActiveAudios] = useState<{ name: string; classCount: number } | null>(
    null,
  );

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const res = await apiFetch("/subjects");
      const data = (await res.json()) as { items: Subject[] };
      setSubjects(data.items);
    } catch (error) {
      toast(error instanceof Error ? error.message : "no se pudieron cargar las materias", "error");
    } finally {
      setLoading(false);
    }
  }, [toast]);

  useEffect(() => {
    void load();
  }, [load]);

  function bodyOf(draft: SubjectDraft) {
    return JSON.stringify({
      name: draft.name,
      color: draft.color,
      teacher: draft.teacher.trim() === "" ? null : draft.teacher,
    });
  }

  async function handleCreate(draft: SubjectDraft) {
    setBusy(true);
    try {
      await apiFetch("/subjects", {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: bodyOf(draft),
      });
      await load();
      setForm(null);
      toast("materia creada", "success");
    } finally {
      setBusy(false);
    }
  }

  async function handleUpdate(draft: SubjectDraft) {
    if (form?.mode !== "edit") return;
    setBusy(true);
    try {
      await apiFetch(`/subjects/${form.subject.id}`, {
        method: "PATCH",
        headers: { "content-type": "application/json" },
        body: bodyOf(draft),
      });
      await load();
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
      await load();
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

  const editing = form?.mode === "edit" ? form.subject : null;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex items-center justify-between">
        <h1 className="lowercase text-2xl text-mint">materias</h1>
        {form === null && (
          <Button onClick={() => setForm({ mode: "create" })}>nueva materia</Button>
        )}
      </div>

      {form !== null && (
        <Card title={editing != null ? `editar · ${editing.name}` : "nueva materia"} variant="accent">
          <SubjectForm
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
        </Card>
      )}

      <Card title="tus materias">
        {loading ? (
          <p className="lowercase text-lavender/60">&gt; cargando…</p>
        ) : subjects.length === 0 ? (
          <EmptyState
            title="aún no tienes materias"
            description="crea la primera para empezar a subir clases."
            action={
              form === null ? (
                <Button onClick={() => setForm({ mode: "create" })}>crear materia</Button>
              ) : undefined
            }
          />
        ) : (
          <ul className="lowercase flex flex-col gap-2">
            {subjects.map((subject) => (
              <li
                key={subject.id}
                className="flex items-center gap-3 border-2 border-pure-black px-3 py-2"
              >
                <span
                  aria-hidden="true"
                  className="inline-block h-4 w-4 shrink-0"
                  style={{ backgroundColor: subject.color }}
                />
                <span className="flex-1 text-lavender">{subject.name}</span>
                {subject.teacher != null && subject.teacher !== "" && (
                  <span className="text-lavender/60">{subject.teacher}</span>
                )}
                <span className="text-lavender/60">
                  {subject.class_count} {subject.class_count === 1 ? "clase" : "clases"}
                </span>
                <Button
                  size="sm"
                  variant="secondary"
                  onClick={() => setForm({ mode: "edit", subject })}
                >
                  editar
                </Button>
                <Button size="sm" variant="danger" disabled={busy} onClick={() => void handleDelete(subject)}>
                  eliminar
                </Button>
              </li>
            ))}
          </ul>
        )}
      </Card>

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
