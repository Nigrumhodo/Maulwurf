import { Link, Outlet, useLocation } from "react-router-dom";
import { gestionBreadcrumb } from "./gestionNav";

// D- (Gestión) — The section menu now lives in the app-wide AppSidebar; this layout only adds
// the route-derived breadcrumb above the routed page. The breadcrumb sits on an opaque strip
// because nothing may be read directly over the background layer.
export function GestionLayout() {
  const { pathname } = useLocation();
  const crumbs = gestionBreadcrumb(pathname);

  return (
    <div className="flex min-w-0 flex-col gap-3">
      {crumbs.length > 1 && (
        <nav aria-label="ruta" className="self-start border-2 border-lavender/30 bg-surface px-3 py-1">
          <ol className="lowercase flex flex-wrap items-center gap-1 text-sm text-lavender/60">
            {crumbs.map((crumb, i) => (
              <li key={`${i}-${crumb.label}`} className="flex items-center gap-1">
                {i > 0 && <span aria-hidden="true">›</span>}
                {crumb.to !== undefined ? (
                  <Link to={crumb.to} className="underline-offset-2 hover:text-mint hover:underline">
                    {crumb.label}
                  </Link>
                ) : (
                  <span aria-current="page" className="text-lavender/80">
                    {crumb.label}
                  </span>
                )}
              </li>
            ))}
          </ol>
        </nav>
      )}
      <Outlet />
    </div>
  );
}
