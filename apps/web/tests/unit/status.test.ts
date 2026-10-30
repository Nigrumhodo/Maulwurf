// D1.5 — C-S1-DA-03: badges de estado y empty states muestran los valores del
// contrato de estados. Aquí se verifica el mapa de tonos que alimenta a
// `StatusBadge`: todo valor tipado del contrato (§1.4 / §2.10) tiene tono
// definido y el valor se muestra TAL CUAL (identidad, sin "traducción").
import { describe, expect, it } from "vitest";
import { STATUS_TONES, statusTone } from "@/lib/status";

const REQUIRED_VALUES = [
  // Intento de ingesta (M2)
  "awaiting_upload",
  "receiving",
  "transcribing",
  "transcript_committed_cleanup_pending",
  "succeeded",
  "requires_reupload",
  "rejected",
  "cancelled",
  // Etapa de texto (processing_runs)
  "not_requested",
  "pending",
  "running",
  "failed",
  // Agregado derivado + revisión (M9)
  "processing",
  "ready",
  "partial",
  "needs_review",
];

describe("statusTone (C-S1-DA-03)", () => {
  it("define tono para todos los valores del contrato, incluido cleanup pendiente", () => {
    for (const value of REQUIRED_VALUES) {
      expect(STATUS_TONES[value], `falta tono para ${value}`).toBeDefined();
    }
  });

  it("mapea el estado interno de cleanup pendiente como warning", () => {
    expect(statusTone("transcript_committed_cleanup_pending")).toBe("warning");
  });

  it("mapea los agregados según el contrato", () => {
    expect(statusTone("not_requested")).toBe("muted");
    expect(statusTone("processing")).toBe("active");
    expect(statusTone("ready")).toBe("success");
    expect(statusTone("partial")).toBe("warning");
    expect(statusTone("failed")).toBe("error");
    expect(statusTone("needs_review")).toBe("warning");
  });

  it("conserva el valor tal cual para estados fuera del contrato (fallback muted)", () => {
    expect(statusTone("valor-desconocido")).toBe("muted");
  });

  it("cubre los estados de tarea, calendar sync y notificaciones", () => {
    for (const value of [
      "confirmed",
      "dismissed",
      "completed",
      "synced",
      "blocked",
      "sending",
      "sent",
      "delivery_unknown",
      "streaming",
    ]) {
      expect(STATUS_TONES[value], `falta tono para ${value}`).toBeDefined();
    }
  });
});
