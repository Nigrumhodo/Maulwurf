import type { ReactNode } from "react";
import { RetroVisual } from "@/components/ui/RetroVisual";
import {
  estadoBibliotecaVacia,
  estadoBibliotecaVaciaOscuro,
  estadoBusquedaRag,
  estadoBusquedaRagAnim,
  estadoError404,
  estadoError404Anim,
  estadoError404Claro,
  estadoLeyendoCitas,
  estadoLeyendoCitasAnim,
  estadoTareasCafe,
  estadoTareasCafeAnim,
  estadoTareasCafeOscuro,
} from "@/components/Visual";

export type EmptyStateIllustration = "library" | "tasks" | "error" | "reading";

// D1.5 — Empty state: caja punteada con ilustración 16-bit opcional, título, descripción y acción.
// Se usa donde no hay datos que mostrar (biblioteca vacía, bandeja sin tareas, errores de carga…).
export interface EmptyStateProps {
  title: string;
  description?: string;
  action?: ReactNode;
  illustration?: EmptyStateIllustration;
  className?: string;
}

function Illustration({ kind }: { kind: EmptyStateIllustration }) {
  if (kind === "library") {
    return (
      <div className="border-2 border-pure-black bg-dark p-2 shadow-block-sm">
        <RetroVisual
          src={estadoBibliotecaVaciaOscuro}
          lightSrc={estadoBibliotecaVacia}
          className="h-36 w-36 object-contain"
        />
      </div>
    );
  }
  if (kind === "tasks") {
    return (
      <div className="border-2 border-pure-black bg-dark p-2 shadow-block-sm">
        <RetroVisual
          src={estadoTareasCafeOscuro}
          lightSrc={estadoTareasCafeAnim}
          lightStaticSrc={estadoTareasCafe}
          className="h-36 w-36 object-contain"
        />
      </div>
    );
  }
  if (kind === "error") {
    return (
      <div className="border-2 border-pure-black bg-dark p-2 shadow-block-sm">
        <RetroVisual
          src={estadoError404Anim}
          staticSrc={estadoError404}
          lightSrc={estadoError404Claro}
          className="h-36 w-36 object-contain"
        />
      </div>
    );
  }
  return (
    <div className="border-2 border-pure-black bg-dark p-2 shadow-block-sm">
      <RetroVisual
        src={estadoBusquedaRagAnim}
        staticSrc={estadoBusquedaRag}
        lightSrc={estadoLeyendoCitasAnim}
        lightStaticSrc={estadoLeyendoCitas}
        className="h-36 w-36 object-contain"
      />
    </div>
  );
}

export function EmptyState({
  title,
  description,
  action,
  illustration,
  className = "",
}: EmptyStateProps) {
  return (
    <div
      className={`capitalize font-pixel flex flex-col items-center justify-center gap-3 border-2 border-dashed border-lavender/50 px-6 py-8 text-center ${className}`}
    >
      {illustration !== undefined ? (
        <Illustration kind={illustration} />
      ) : (
        <span className="text-4xl leading-none text-mint" aria-hidden="true">
          ░
        </span>
      )}
      <p className="text-lavender">{title}</p>
      {description != null && <p className="text-lavender/60">{description}</p>}
      {action != null && <div className="mt-1">{action}</div>}
    </div>
  );
}
