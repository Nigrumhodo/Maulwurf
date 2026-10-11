import type { ReactNode } from "react";
import { Link } from "react-router-dom";
import { Card } from "@/components/ui/Card";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { FlickerText } from "@/components/motion/FlickerText";

export interface AuthShellProps {
  /** título de la ventana (barra del terminal) */
  title: string;
  children: ReactNode;
}

/**
 * Envoltura compartida por las pantallas de login y registro: fondo oscuro,
 * superposición CRT y una tarjeta centrada con la barra de terminal.
 */
export function AuthShell({ title, children }: AuthShellProps) {
  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center px-4 py-10">
      <div className="crt-overlay" aria-hidden="true" />

      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>

      <Link
        to="/"
        className="lowercase mb-6 text-3xl text-mint"
      >
        maulwurf<span className="text-lavender">@lab</span>
      </Link>

      <Card title={title} variant="outline" className="w-full max-w-md">
        <div className="flex flex-col gap-5">{children}</div>
      </Card>

      <p className="lowercase mt-6 text-center text-lavender/70">
        <FlickerText mode="blink" className="text-lavender">
          audio efímero · solo texto durable
        </FlickerText>
      </p>
    </div>
  );
}
