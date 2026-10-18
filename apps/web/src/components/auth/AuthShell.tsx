import type { ReactNode } from "react";
import { Card } from "@/components/ui/Card";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { FlickerText } from "@/components/motion/FlickerText";
import { loginup, loginuposcuro } from "@/components/Visual";

export interface AuthShellProps {
  /** título de la ventana (barra del terminal) */
  title: string;
  children: ReactNode;
}

/**
 * Envoltura compartida por las pantallas de login y registro: imagen del topo
 * asomando por detrás de la card, tarjeta centrada y marca parpadeando abajo.
 */
export function AuthShell({ title, children }: AuthShellProps) {
  return (
    <div className="relative z-10 flex min-h-screen flex-col items-center justify-center px-4 py-10">
      <div className="crt-overlay" aria-hidden="true" />

      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>

      {/* Topo (maulwurf) detrás de la card. Ajusta tamaño con h-/w-* y solape con -mb-*.
          Una imagen para cada tema: oscura por defecto, clara con la clase .light. */}
      <div className="relative z-0 -mb-16 select-none">
        <img
          src={loginuposcuro}
          alt=""
          className="img-dark-only pointer-events-none h-48 w-48"
          style={{ imageRendering: "pixelated" }}
        />
        <img
          src={loginup}
          alt=""
          className="img-light-only pointer-events-none h-48 w-48"
          style={{ imageRendering: "pixelated" }}
        />
      </div>

      <Card title={title} variant="outline" className="relative z-10 w-full max-w-md glitch-hover">
        <div className="flex flex-col gap-5">{children}</div>
      </Card>

      <p className="lowercase mt-8 text-center">
        <FlickerText mode="blink" className="text-4xl text-mint">
          maulwurf<span className="text-lavender">@lab</span>
        </FlickerText>
      </p>
    </div>
  );
}
