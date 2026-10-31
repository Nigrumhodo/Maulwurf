// @vitest-environment jsdom
//
// D1.6 — Regresión del formulario de materias (U-S1-DA-02).
//
// El listado permite abrir el editor de otra materia sin cerrar el anterior. Los
// `useState` de `SubjectForm` solo se inicializan al montar, así que cambiar de destino
// debe remontar el formulario; si no, el PATCH lleva los datos de la materia anterior.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { MateriasPage } from "@/pages/MateriasPage";
import { ToastProvider } from "@/components/ui/ToastProvider";

const SUBJECTS = {
  items: [
    { id: "s1", name: "calculo i", color: "#6366f1", teacher: "ana", class_count: 2 },
    { id: "s2", name: "fisica ii", color: "#22c55e", teacher: null, class_count: 0 },
  ],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

let patches: Array<{ url: string; body: unknown }> = [];

beforeEach(() => {
  patches = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = (init?.method ?? "GET").toUpperCase();
      if (url === "/subjects" && method === "GET") return json(SUBJECTS);
      if (method === "PATCH") {
        patches.push({ url, body: init?.body ? JSON.parse(String(init.body)) : null });
        return json({ ...SUBJECTS.items[1] });
      }
      return json({});
    }),
  );
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

function renderPage() {
  return render(
    <MemoryRouter>
      <ToastProvider>
        <MateriasPage />
      </ToastProvider>
    </MemoryRouter>,
  );
}

describe("MateriasPage · cambio de materia en edición", () => {
  it("carga los valores de la materia que se abre", async () => {
    renderPage();
    const editButtons = await screen.findAllByRole("button", { name: "editar" });

    editButtons[0].click();
    await waitFor(() => expect(screen.getByLabelText(/nombre/i)).toHaveProperty("value", "calculo i"));

    editButtons[1].click();
    await waitFor(() => expect(screen.getByLabelText(/nombre/i)).toHaveProperty("value", "fisica ii"));
  });

  it("guarda en la materia visible, no en la anterior", async () => {
    renderPage();
    const editButtons = await screen.findAllByRole("button", { name: "editar" });

    editButtons[0].click();
    await waitFor(() => expect(screen.getByLabelText(/nombre/i)).toHaveProperty("value", "calculo i"));
    editButtons[1].click();
    await waitFor(() => expect(screen.getByLabelText(/nombre/i)).toHaveProperty("value", "fisica ii"));

    screen.getByRole("button", { name: "guardar" }).click();

    await waitFor(() => expect(patches).toHaveLength(1));
    expect(patches[0].url).toBe("/subjects/s2");
    expect(patches[0].body).toMatchObject({ name: "fisica ii", color: "#22c55e" });
  });
});
