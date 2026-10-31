// @vitest-environment jsdom
//
// D1.4/D1.5 — Regresión del interceptor 401 en el navegador (U-S1-DA-01).
//
// El modo invitado solo desbloquea la navegación: un 401 no puede expulsar al visitante
// a `/login` con una recarga completa. Se monta el `AuthProvider` real sobre
// `MemoryRouter` para verificar la navegación que el usuario observaría.
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Navigate, Route, Routes, useLocation } from "react-router-dom";
import { AuthProvider, RequireAuth } from "@/components/auth/AuthProvider";
import { apiFetch } from "@/lib/api";

const UNAUTHORIZED = () =>
  new Response(JSON.stringify({ error: { code: "auth_required", message: "Inicia sesión." } }), {
    status: 401,
    headers: { "content-type": "application/json" },
  });

const ME = {
  id: "u-1",
  email: "estudiante@lab.test",
  display_name: null,
  timezone: "America/Bogota",
  csrf_token: "tok-a",
};

function okResponse(body: unknown): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "content-type": "application/json" },
  });
}

function LocationProbe() {
  const location = useLocation();
  return <span data-testid="pathname">{location.pathname}</span>;
}

/** Page that calls the API once on mount and swallows the rejection. */
function ApiCaller() {
  return (
    <button
      type="button"
      onClick={() => {
        void apiFetch("/subjects").catch(() => undefined);
      }}
    >
      llamar
    </button>
  );
}

function renderApp(initialPath: string) {
  return render(
    <MemoryRouter initialEntries={[initialPath]}>
      <AuthProvider>
        <LocationProbe />
        <Routes>
          <Route path="/login" element={<p>login</p>} />
          <Route element={<RequireAuth />}>
            <Route path="/gestion/materias" element={<ApiCaller />} />
          </Route>
          <Route path="*" element={<Navigate to="/" replace />} />
        </Routes>
      </AuthProvider>
    </MemoryRouter>,
  );
}

async function clickCaller() {
  const button = await screen.findByRole("button", { name: "llamar" });
  button.click();
  // Let the rejected promise settle through the interceptor.
  await waitFor(() => expect(screen.getByRole("button", { name: "llamar" })).toBeDefined());
  await new Promise((resolve) => setTimeout(resolve, 25));
}

beforeEach(() => {
  window.sessionStorage.clear();
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.sessionStorage.clear();
});

describe("interceptor 401 en el navegador", () => {
  it("mantiene al invitado en su ruta en vez de mandarlo a /login", async () => {
    window.sessionStorage.setItem("maulwurf-guest", "1");
    vi.stubGlobal("fetch", vi.fn(UNAUTHORIZED));

    renderApp("/gestion/materias");
    await clickCaller();

    expect(screen.getByTestId("pathname").textContent).toBe("/gestion/materias");
    expect(screen.queryByText("login")).toBeNull();
    expect(window.sessionStorage.getItem("maulwurf-guest")).toBe("1");
  });

  it("con sesión caducada sí redirige a /login", async () => {
    const fetchMock = vi.fn().mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url === "/me") return Promise.resolve(okResponse(ME));
      return Promise.resolve(UNAUTHORIZED());
    });
    vi.stubGlobal("fetch", fetchMock);

    renderApp("/gestion/materias");
    const button = await screen.findByRole("button", { name: "llamar" });
    button.click();

    await waitFor(() => expect(screen.getByTestId("pathname").textContent).toBe("/login"));
  });
});
