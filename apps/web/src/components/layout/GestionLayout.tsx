import { useState } from "react";
import { NavLink, Outlet } from "react-router-dom";

// D- (Gestión) — Hub académico: menú lateral colapsable con todas las opciones
// de gestión según la documentación (subir clase, biblioteca, chat, materias,
// tareas y agenda). Los iconos son emojis provisionales (logos luego). La barra
// se oculta/expande con el botón del encabezado.
const NAV_ITEMS = [
  { to: "/gestion/subir", label: "subir clase", emoji: "📤", end: false },
  { to: "/gestion/biblioteca", label: "biblioteca", emoji: "📚", end: false },
  { to: "/gestion/chat", label: "chat", emoji: "💬", end: false },
  { to: "/gestion/materias", label: "materias", emoji: "📓", end: false },
  { to: "/gestion/tareas", label: "tareas", emoji: "✅", end: false },
  { to: "/gestion/agenda", label: "agenda", emoji: "📅", end: false },
];

function itemClass({ isActive }: { isActive: boolean }): string {
  return `lowercase font-pixel flex items-center gap-2 border-2 border-transparent px-2 py-1 text-lavender hover:text-mint ${
    isActive ? "border-mint bg-mint text-on-accent" : ""
  }`;
}

export function GestionLayout() {
  const [collapsed, setCollapsed] = useState(false);

  return (
    <div className="flex items-start gap-4">
      <aside
        className={`shrink-0 border-2 border-pure-black bg-surface shadow-block transition-[width] ${
          collapsed ? "w-14" : "w-56"
        }`}
      >
        <header className="flex items-center justify-between border-b-2 border-pure-black bg-mint px-2 py-2 text-on-accent">
          {!collapsed && (
            <span className="lowercase font-bold">
              <span aria-hidden="true">🗂️</span> gestión
            </span>
          )}
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

        <nav className="flex flex-col gap-1 p-2" aria-label="gestión">
          {NAV_ITEMS.map((item) => (
            <NavLink
              key={item.to}
              to={item.to}
              end={item.end}
              title={item.label}
              className={({ isActive }) =>
                `${itemClass({ isActive })} ${collapsed ? "justify-center px-1" : ""}`
              }
            >
              <span aria-hidden="true">{item.emoji}</span>
              {!collapsed && <span>{item.label}</span>}
            </NavLink>
          ))}
        </nav>
      </aside>

      <section className="min-w-0 flex-1">
        <Outlet />
      </section>
    </div>
  );
}
