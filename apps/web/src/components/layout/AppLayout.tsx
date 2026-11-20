import { useState } from "react";
import type { ReactNode } from "react";
import { Outlet } from "react-router-dom";
import { CommandPalette } from "@/components/search/CommandPalette";
import { Header } from "./Header";
import { ToastProvider } from "@/components/ui/ToastProvider";

// D1.2 — Layout compartido de las rutas autenticadas: header global + área de
// contenido. El guard de sesión vive fuera (RequireAuth) y envuelve este layout.
// D1.5 monta el ToastProvider para que `useToast()` esté disponible en la app.
// D4.3 monta la paleta Ctrl+K accesible desde cualquier pantalla autenticada.
export function AppLayout({ children }: { children?: ReactNode }) {
  const [paletteOpen, setPaletteOpen] = useState(false);

  return (
    <ToastProvider>
      <div className="relative z-10 min-h-screen">
        <Header onOpenSearch={() => setPaletteOpen(true)} />
        <main className="mx-auto max-w-7xl px-4 py-8">
          {children ?? <Outlet />}
        </main>
        <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      </div>
    </ToastProvider>
  );
}
