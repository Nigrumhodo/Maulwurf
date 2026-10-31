// D1.6 — U-S1-DA-02: validación del formulario de materias y mapeo de errores de
// la API (envelope) a campos. Sin DOM: se prueban los helpers puros que alimentan
// `SubjectForm` y `MateriasPage`.
import { describe, expect, it } from "vitest";
import { ApiHttpError } from "@/lib/api";
import { subjectFormError, validateSubject } from "@/lib/subjects";

describe("validateSubject (U-S1-DA-02)", () => {
  it("requiere nombre (vacío o en blanco)", () => {
    expect(validateSubject({ name: "", color: "#6366f1", teacher: "" }).name).toBeDefined();
    expect(validateSubject({ name: "   ", color: "#6366f1", teacher: "" }).name).toBeDefined();
  });

  it("acepta una materia válida", () => {
    expect(validateSubject({ name: "cálculo", color: "#6366f1", teacher: "" })).toEqual({});
  });

  it("valida el color como #rrggbb", () => {
    expect(validateSubject({ name: "x", color: "azul", teacher: "" }).color).toBeDefined();
    expect(validateSubject({ name: "x", color: "#fff", teacher: "" }).color).toBeDefined();
    expect(validateSubject({ name: "x", color: "", teacher: "" }).color).toBeUndefined();
  });
});

describe("subjectFormError (errores de API renderizados)", () => {
  it("mapea subject_name_taken al campo nombre", () => {
    const error = new ApiHttpError(409, {
      code: "subject_name_taken",
      message: "Ya tienes una materia con ese nombre.",
    });
    expect(subjectFormError(error)).toEqual({
      field: "name",
      message: "Ya tienes una materia con ese nombre.",
    });
  });

  it("mapea validation_failed a su campo", () => {
    const error = new ApiHttpError(422, {
      code: "validation_failed",
      message: "Hay campos inválidos.",
      details: { fields: { name: "ensure this value has at least 1 characters" } },
    });
    expect(subjectFormError(error).field).toBe("name");
    expect(subjectFormError(error).message).toContain("at least 1 characters");
  });

  it("cae al mensaje general para errores sin campo conocido", () => {
    const error = new ApiHttpError(500, { code: "internal", message: "boom" });
    expect(subjectFormError(error)).toEqual({ field: "form", message: "boom" });
  });
});
