import type { ReactNode } from "react";

// A section inside a view's single main window: heading plus content, no box of its own.
export function DashboardSection({
  id,
  title,
  meta,
  children,
}: {
  id: string;
  title: string;
  meta?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section aria-labelledby={id} className="flex flex-col gap-4 py-6 first:pt-0 last:pb-0">
      <div className="flex flex-wrap items-baseline justify-between gap-2">
        <h2 id={id} className="lowercase text-xl text-mint">
          {title}
        </h2>
        {meta !== undefined && <span className="lowercase text-base text-lavender/60">{meta}</span>}
      </div>
      {children}
    </section>
  );
}
