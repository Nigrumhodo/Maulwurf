import { useState } from "react";
import type { ReactNode } from "react";
import { Outlet } from "react-router-dom";
import { WavesBackground } from "@/components/motion/WavesBackground";
import { CommandPalette } from "@/components/search/CommandPalette";
import { ToastProvider } from "@/components/ui/ToastProvider";
import { AppSidebar } from "./AppSidebar";

// D1.2 — Layout compartido de las rutas autenticadas: header global + área de
// contenido. El guard de sesión vive fuera (RequireAuth) y envuelve este layout.
// D1.5 monta el ToastProvider para que `useToast()` esté disponible en la app.
// D4.3 monta la paleta Ctrl+K accesible desde cualquier pantalla autenticada.
// Wave 3: the single AppSidebar menu replaces the header tabs; the content column is offset
// by the menu width (56px rail on md, 240px column on lg) and each view draws its own window.
export function AppLayout({ children }: { children?: ReactNode }) {
  const [paletteOpen, setPaletteOpen] = useState(false);

  return (
    <ToastProvider>
      <div className="relative z-10 min-h-screen">
        <WavesBackground />
        <AppSidebar onOpenSearch={() => setPaletteOpen(true)} />
        <div className="md:pl-14 lg:pl-60">
          <main className="mx-auto w-full max-w-[75rem] px-4 py-6 lg:px-8">
            {children ?? <Outlet />}
          </main>
        </div>
        <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
      </div>
    </ToastProvider>
  );
}
