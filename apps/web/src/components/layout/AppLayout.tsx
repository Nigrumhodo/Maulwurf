import { useEffect } from "react";
import { Outlet } from "react-router-dom";
import { Header } from "./Header";
import { ToastProvider, useToast } from "@/components/ui/ToastProvider";

// The API client reports 403s as events so it stays framework-agnostic; this bridge
// turns them into toasts inside the provider that owns the viewport.
function ApiErrorBridge() {
  const { toast } = useToast();

  useEffect(() => {
    const onForbidden = (event: Event) => {
      const message = (event as CustomEvent<string>).detail;
      toast(message !== "" ? message : "no tienes permisos para esta acción", "error");
    };
    window.addEventListener("mw:forbidden", onForbidden);
    return () => window.removeEventListener("mw:forbidden", onForbidden);
  }, [toast]);

  return null;
}

// D1.2 — Layout compartido de las rutas autenticadas: header global + área de
// contenido. El guard de sesión vive fuera (RequireAuth) y envuelve este layout.
// D1.5 monta el ToastProvider para que `useToast()` esté disponible en la app.
export function AppLayout() {
  return (
    <ToastProvider>
      <ApiErrorBridge />
      <div className="relative z-10 min-h-screen">
        <Header />
        <main className="mx-auto max-w-7xl px-4 py-8">
          <Outlet />
        </main>
      </div>
    </ToastProvider>
  );
}
