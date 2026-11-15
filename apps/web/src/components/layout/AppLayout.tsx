import { Outlet } from "react-router-dom";
import { Header } from "./Header";
import { ToastProvider } from "@/components/ui/ToastProvider";

// D1.2 — Layout compartido de las rutas autenticadas: header global + área de
// contenido. El guard de sesión vive fuera (RequireAuth) y envuelve este layout.
// D1.5 monta el ToastProvider para que `useToast()` esté disponible en la app.
// A 403 is rendered by the caller that made the request; this layout does not
// add a second toast for the same error.
export function AppLayout() {
  return (
    <ToastProvider>
      <div className="relative z-10 min-h-screen">
        <Header />
        <main className="mx-auto max-w-7xl px-4 py-8">
          <Outlet />
        </main>
      </div>
    </ToastProvider>
  );
}
