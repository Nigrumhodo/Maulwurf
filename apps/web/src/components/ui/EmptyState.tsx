import type { ReactNode } from "react";

// D1.5 — Empty state: caja punteada con título, descripción y acción opcional.
// Se usa donde no hay datos que mostrar (biblioteca vacía, bandeja sin tareas…).
export interface EmptyStateProps {
  title: string;
  description?: string;
  action?: ReactNode;
  className?: string;
}

export function EmptyState({ title, description, action, className = "" }: EmptyStateProps) {
  return (
    <div
      className={`lowercase font-pixel flex flex-col items-center justify-center gap-3 border-2 border-dashed border-lavender/50 px-6 py-10 text-center ${className}`}
    >
      <span className="text-4xl leading-none text-mint" aria-hidden="true">
        ░
      </span>
      <p className="text-lavender">{title}</p>
      {description != null && <p className="text-lavender/60">{description}</p>}
      {action != null && <div className="mt-1">{action}</div>}
    </div>
  );
}
