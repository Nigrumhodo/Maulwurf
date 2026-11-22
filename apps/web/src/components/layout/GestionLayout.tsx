import { useState } from "react";
import { Link, NavLink, Outlet, useLocation } from "react-router-dom";
import {
  GESTION_ITEMS,
  GESTION_MODULES,
  currentGestionItem,
  gestionBreadcrumb,
} from "./gestionNav";

const toggleClasses =
  "inline-flex min-h-8 min-w-8 shrink-0 items-center justify-center border-2 border-lavender/40 leading-none text-lavender hover:border-mint hover:text-mint";

function navLinkClasses({ isActive }: { isActive: boolean }): string {
  return `capitalize flex items-center gap-2 px-3 py-2 ${
    isActive ? "bg-mint text-on-accent" : "text-lavender hover:text-mint"
  }`;
}

// D- (Gestión) — Academic hub sidebar: one flat nav list grouped by module, a compact
// decorative footer, and a route-derived breadcrumb above the routed page.
// Desktop collapses to a glyph rail; below `md` the nav folds into a single bar that names
// the current section, so the page content starts right under the header.
export function GestionLayout() {
  const [railCollapsed, setRailCollapsed] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);
  const { pathname } = useLocation();
  const current = currentGestionItem(pathname);
  const crumbs = gestionBreadcrumb(pathname);

  const fullNav = (
    <nav
      id="gestion-nav"
      aria-label="gestión"
      className={`flex-col gap-4 p-3 md:pt-4 ${menuOpen ? "flex" : "hidden"} ${
        railCollapsed ? "md:hidden" : "md:flex"
      }`}
    >
      {GESTION_MODULES.map((module) => (
        <div key={module.id} className="flex flex-col gap-1">
          <p id={`gestion-module-${module.id}`} className="lowercase pr-10 text-sm text-lavender/60">
            {module.id}. {module.title}
          </p>
          <ul
            aria-labelledby={`gestion-module-${module.id}`}
            className="flex flex-col divide-y-2 divide-lavender/20"
          >
            {module.items.map((item) => (
              <li key={item.to}>
                <NavLink to={item.to} onClick={() => setMenuOpen(false)} className={navLinkClasses}>
                  <span aria-hidden="true">{item.glyph}</span>
                  <span>{item.label}</span>
                </NavLink>
              </li>
            ))}
          </ul>
        </div>
      ))}
    </nav>
  );

  return (
    <div className="flex flex-col gap-3 md:flex-row md:items-start md:gap-4">
      <aside
        className={`relative w-full shrink-0 border-2 border-pure-black bg-surface shadow-block md:sticky md:top-24 ${
          railCollapsed ? "md:w-14" : "md:w-56 lg:w-64"
        }`}
      >
        <div className="flex items-center justify-between gap-2 px-3 py-1.5 md:hidden">
          <span className="capitalize min-w-0 truncate text-lavender">
            <span className="text-mint" aria-hidden="true">
              ›{" "}
            </span>
            {current?.label ?? "gestión"}
          </span>
          <button
            type="button"
            onClick={() => setMenuOpen((open) => !open)}
            aria-label={menuOpen ? "colapsar menú" : "expandir menú"}
            aria-expanded={menuOpen}
            aria-controls="gestion-nav"
            className={toggleClasses}
          >
            {menuOpen ? "▴" : "▾"}
          </button>
        </div>

        {/* Expanded, the toggle floats in the corner beside the first module heading
            instead of taking a whole empty title row. */}
        <div
          className={`hidden md:flex ${
            railCollapsed
              ? "md:justify-center md:border-b-2 md:border-lavender/20 md:p-1.5"
              : "md:absolute md:right-2 md:top-2"
          }`}
        >
          <button
            type="button"
            onClick={() => setRailCollapsed((c) => !c)}
            aria-label={railCollapsed ? "expandir menú" : "colapsar menú"}
            aria-expanded={!railCollapsed}
            className={toggleClasses}
          >
            {railCollapsed ? "▸" : "◂"}
          </button>
        </div>

        {fullNav}

        {railCollapsed && (
          <nav className="hidden flex-col gap-1 p-2 md:flex" aria-label="gestión">
            {GESTION_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                title={item.label}
                aria-label={item.label}
                className={({ isActive }) =>
                  `flex min-h-8 items-center justify-center ${
                    isActive ? "bg-mint text-on-accent" : "text-lavender hover:text-mint"
                  }`
                }
              >
                <span aria-hidden="true">{item.glyph}</span>
              </NavLink>
            ))}
          </nav>
        )}

        {/* Decorative demo telemetry: kept as one quiet footer, not a panel. */}
        {!railCollapsed && (
          <div
            aria-hidden="true"
            className="lowercase hidden flex-col gap-0.5 border-t-2 border-lavender/20 px-3 py-2 text-sm text-lavender/60 md:flex"
          >
            <p className="flex items-center justify-between gap-2">
              <span>// workspace_modules</span>
              <span>sys_ok</span>
            </p>
            <p className="flex items-center justify-between gap-2">
              <span>// mem_store · demo</span>
              <span className="flex items-center gap-2">
                <span className="flex h-2 w-10 border border-lavender/60">
                  <span className="bg-lavender/60" style={{ width: "64%" }} />
                </span>
                64.2%
              </span>
            </p>
          </div>
        )}
      </aside>

      <section className="flex min-w-0 flex-1 flex-col gap-3">
        {crumbs.length > 1 && (
          <nav aria-label="ruta">
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
      </section>
    </div>
  );
}
