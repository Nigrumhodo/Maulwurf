import { Outlet } from "react-router-dom";
import { Header } from "./Header";

// D1.2 — Layout compartido de las rutas autenticadas: header global + área de
// contenido. El guard de sesión vive fuera (RequireAuth) y envuelve este layout.
export function AppLayout() {
  return (
    <div className="relative z-10 min-h-screen">
      <Header />
      <main className="mx-auto max-w-7xl px-4 py-8">
        <Outlet />
      </main>
    </div>
  );
}
