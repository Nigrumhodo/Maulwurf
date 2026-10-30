import { useState } from "react";
import type { FormEvent } from "react";
import { Button } from "@/components/ui/Button";
import { Input } from "@/components/ui/Input";
import { ApiHttpError } from "@/lib/api";
import {
  DEFAULT_SUBJECT_COLOR,
  subjectFormError,
  validateSubject,
} from "@/lib/subjects";
import type { SubjectDraft, SubjectField } from "@/lib/subjects";

// D1.6 — Formulario de materia (alta/edición) contra la API A1.6. Valida en
// cliente los campos requeridos (nombre) y el color (#rrggbb) y renderiza los
// errores de API (409 `subject_name_taken`, 422 `validation_failed`) en el campo
// correspondiente. `onSubmit` lanza `ApiHttpError`; aquí se traduce a UI.
export interface SubjectFormProps {
  /** valores iniciales para editar; si falta, alta con color por defecto */
  initial?: SubjectDraft;
  submitLabel: string;
  busy?: boolean;
  onSubmit: (draft: SubjectDraft) => Promise<void>;
  onCancel?: () => void;
}

export function SubjectForm({
  initial,
  submitLabel,
  busy = false,
  onSubmit,
  onCancel,
}: SubjectFormProps) {
  const [name, setName] = useState(initial?.name ?? "");
  const [color, setColor] = useState(initial?.color ?? DEFAULT_SUBJECT_COLOR);
  const [teacher, setTeacher] = useState(initial?.teacher ?? "");
  const [errors, setErrors] = useState<Partial<Record<SubjectField, string>>>({});
  const [formError, setFormError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    const draft: SubjectDraft = { name, color, teacher };
    const nextErrors = validateSubject(draft);
    if (Object.keys(nextErrors).length > 0) {
      setErrors(nextErrors);
      setFormError(null);
      return;
    }
    setErrors({});
    setFormError(null);
    try {
      await onSubmit(draft);
    } catch (error) {
      if (error instanceof ApiHttpError) {
        const mapped = subjectFormError(error);
        if (mapped.field === "form") {
          setFormError(mapped.message);
        } else {
          setErrors({ [mapped.field]: mapped.message });
        }
      } else {
        setFormError("no se pudo guardar la materia");
      }
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex flex-col gap-3">
      <Input
        label="nombre"
        value={name}
        onChange={(event) => setName(event.target.value)}
        placeholder="cálculo i"
      />
      {errors.name != null && <p className="lowercase text-mint">&gt; {errors.name}</p>}

      <Input
        label="color"
        value={color}
        onChange={(event) => setColor(event.target.value)}
        placeholder="#6366f1"
      />
      {errors.color != null && <p className="lowercase text-mint">&gt; {errors.color}</p>}

      <Input
        label="profesor (opcional)"
        value={teacher}
        onChange={(event) => setTeacher(event.target.value)}
        placeholder=""
      />

      {formError != null && <p className="lowercase text-mint">&gt; {formError}</p>}

      <div className="mt-2 flex items-center gap-2">
        <Button type="submit" disabled={busy}>
          {busy ? "…" : submitLabel}
        </Button>
        {onCancel != null && (
          <Button type="button" variant="secondary" onClick={onCancel}>
            cancelar
          </Button>
        )}
      </div>
    </form>
  );
}
