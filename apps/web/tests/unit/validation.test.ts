// D1.5 — U-S1-DA-02: validación de campos requeridos y errores de API
// renderizados (base del formulario de materias/upload). Sin DOM: se prueban los
// helpers puros que alimentan los inputs y el renderizado de errores.
import { describe, expect, it } from "vitest";
import { isLanguageAllowed } from "@/lib/languages";
import {
  fieldMessage,
  isRequired,
  parseErrorEnvelope,
} from "@/lib/validation";

describe("isRequired (U-S1-DA-02)", () => {
  it("rechaza valores vacíos, en blanco o no-string", () => {
    expect(isRequired("")).toBe(false);
    expect(isRequired("   ")).toBe(false);
    expect(isRequired(undefined)).toBe(false);
    expect(isRequired(null)).toBe(false);
    expect(isRequired(123)).toBe(false);
  });

  it("acepta strings con contenido", () => {
    expect(isRequired("cálculo i")).toBe(true);
  });
});

describe("isLanguageAllowed (idioma sin preselección)", () => {
  it("solo acepta la allowlist es/en/fr", () => {
    expect(isLanguageAllowed("es")).toBe(true);
    expect(isLanguageAllowed("en")).toBe(true);
    expect(isLanguageAllowed("fr")).toBe(true);
  });

  it("rechaza idiomas fuera de la allowlist y valores vacíos", () => {
    expect(isLanguageAllowed("de")).toBe(false);
    expect(isLanguageAllowed("xx")).toBe(false);
    expect(isLanguageAllowed("")).toBe(false);
  });
});

describe("parseErrorEnvelope (errores de API renderizados)", () => {
  it("extrae code/message/details del envelope tipado", () => {
    const parsed = parseErrorEnvelope({
      error: {
        code: "validation_failed",
        message: "campos inválidos",
        details: { name: "materia requerida" },
      },
    });
    expect(parsed).toEqual({
      code: "validation_failed",
      message: "campos inválidos",
      details: { name: "materia requerida" },
    });
  });

  it("devuelve null cuando no hay envelope de error", () => {
    expect(parseErrorEnvelope(null)).toBeNull();
    expect(parseErrorEnvelope({})).toBeNull();
    expect(parseErrorEnvelope({ error: "boom" })).toBeNull();
    expect(parseErrorEnvelope({ error: { code: 42 } })).toEqual({
      code: undefined,
      message: undefined,
      details: undefined,
    });
  });
});

describe("fieldMessage (mapeo de detalle por campo)", () => {
  it("extrae el mensaje string de un campo", () => {
    expect(fieldMessage({ name: "materia requerida" }, "name")).toBe("materia requerida");
  });

  it("toma el primer elemento cuando el detalle es un array", () => {
    expect(fieldMessage({ name: ["materia requerida", "mínimo 3"] }, "name")).toBe(
      "materia requerida",
    );
  });

  it("devuelve undefined si no hay detalle para el campo", () => {
    expect(fieldMessage({}, "name")).toBeUndefined();
    expect(fieldMessage(["name"], "name")).toBeUndefined();
    expect(fieldMessage(undefined, "name")).toBeUndefined();
  });
});
