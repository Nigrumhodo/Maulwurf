import { useState } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { GESTION_MODULES, GESTION_ITEMS } from "./gestionNav";
import type { GestionModule } from "./gestionNav";

// D- (Gestión) — Academic hub sidebar. Three grouped modules (materias & tareas ·
// biblioteca & ingesta · terminal ia & agenda) plus a storage telemetry block.
// Collapsible: collapsed keeps only the item glyphs.
export function GestionLayout() {
  const [collapsed, setCollapsed] = useState(false);
  const location = useLocation();

  function moduleActive(module: GestionModule): boolean {
    return module.items.some((item) => location.pathname.startsWith(item.to));
  }

  return (
    <div className="flex items-start gap-4">
      <aside
        className={`shrink-0 border-2 border-pure-black bg-surface shadow-block transition-[width] ${
          collapsed ? "w-14" : "w-64"
        }`}
      >
        <header className="flex items-center justify-between border-b-2 border-pure-black bg-mint px-2 py-2 text-on-accent">
          {!collapsed && <span className="lowercase font-bold">gestión</span>}
          <button
            type="button"
            onClick={() => setCollapsed((c) => !c)}
            aria-label={collapsed ? "expandir menú" : "colapsar menú"}
            aria-expanded={!collapsed}
            className="lowercase font-pixel border-2 border-on-accent/40 px-1 leading-none hover:bg-on-accent hover:text-mint"
          >
            {collapsed ? "▸" : "◂"}
          </button>
        </header>

        {collapsed ? (
          <nav className="flex flex-col gap-1 p-2" aria-label="gestión">
            {GESTION_ITEMS.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                title={item.label}
                className={({ isActive }) =>
                  `lowercase flex justify-center border-2 px-1 py-1 ${
                    isActive
                      ? "border-mint bg-mint text-on-accent"
                      : "border-transparent text-lavender hover:text-mint"
                  }`
                }
              >
                <span aria-hidden="true">{item.glyph}</span>
              </NavLink>
            ))}
          </nav>
        ) : (
          <div className="flex flex-col gap-3 p-3">
            <div className="border-2 border-pure-black bg-surface p-2.5">
              <p className="lowercase text-lavender/50">// workspace_modules</p>
              <p className="lowercase flex items-center justify-between text-lavender">
                <span className="font-bold">gestión</span>
                <span className="border border-pure-black bg-mint px-1 text-on-accent">sys_ok</span>
              </p>
            </div>

            {GESTION_MODULES.map((module) => {
              const active = moduleActive(module);
              return (
                <div
                  key={module.id}
                  className={`flex flex-col gap-1 border-2 p-2.5 shadow-block ${
                    active
                      ? "border-mint bg-mint text-on-accent"
                      : "border-pure-black bg-surface text-lavender"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="lowercase font-bold">
                      {module.id}. {module.title}
                    </span>
                    <span className="lowercase border border-pure-black px-1 text-[10px]">
                      [{active ? "activo" : module.tag}]
                    </span>
                  </div>
                  <p className="lowercase text-[10px] opacity-70">{module.desc}</p>
                  <div className="mt-1 flex flex-col gap-1">
                    {module.items.map((item) => (
                      <NavLink
                        key={item.to}
                        to={item.to}
                        className={({ isActive }) =>
                          `lowercase flex items-center gap-2 border-2 px-2 py-1 ${
                            isActive
                              ? "border-on-accent bg-on-accent text-mint"
                              : active
                                ? "border-on-accent/40 text-on-accent hover:bg-on-accent hover:text-mint"
                                : "border-pure-black text-lavender hover:border-mint hover:text-mint"
                          }`
                        }
                      >
                        <span aria-hidden="true">{item.glyph}</span>
                        <span>{item.label}</span>
                      </NavLink>
                    ))}
                  </div>
                </div>
              );
            })}

            <div className="border-2 border-pure-black bg-surface p-3 shadow-block">
              <div className="lowercase flex items-center justify-between text-lavender">
                <span className="text-lavender/60">// mem_store</span>
                <span className="text-mint">64.2%</span>
              </div>
              <div className="mt-1 h-2.5 w-full border-2 border-pure-black p-0.5">
                <div className="h-full bg-mint" style={{ width: "64%" }} />
              </div>
              <div className="lowercase mt-1 flex items-center justify-between border-t border-lavender/20 pt-1 text-lavender/50">
                <span>buffer: 512/800mb</span>
                <span className="text-mint">sector: 0x7f</span>
              </div>
            </div>
          </div>
        )}
      </aside>

      <section className="min-w-0 flex-1">
        <Outlet />
      </section>
    </div>
  );
}
