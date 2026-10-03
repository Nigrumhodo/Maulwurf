import { Outlet } from "react-router-dom";
import { Navbar } from "./Navbar";

// D1.2 — Layout compartido de las rutas autenticadas: barra de navegación + área
// de contenido. El guard de sesión vive fuera (RequireAuth) y envuelve este layout.
export function AppLayout() {
  return (
    <div className="min-h-screen">
      <Navbar />
      <main className="mx-auto max-w-5xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}
