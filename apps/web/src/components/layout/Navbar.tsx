import { NavLink } from "react-router-dom";
import { useAuth } from "@/components/auth/AuthProvider";

const pages = [
  { path: "/", label: "01.home" },
  { path: "/biblioteca", label: "02.biblioteca" },
  { path: "/chat", label: "03.chat" },
  { path: "/materias", label: "04.materias" },
  { path: "/settings", label: "05.settings" },
];

export function Navbar() {
  const { logout } = useAuth();

  return (
    <nav className="sticky top-0 z-50 border-b-2 border-pure-black bg-dark">
      <div className="mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-3 px-4 py-3">
        <NavLink to="/" className="lowercase text-xl text-mint">
          maulwurf<span className="text-lavender">@lab</span>
        </NavLink>

        <div className="flex flex-wrap items-center gap-1">
          {pages.map((p) => (
            <NavLink
              key={p.path}
              to={p.path}
              end={p.path === "/"}
              className={({ isActive }) =>
                `lowercase px-3 py-1 text-lavender hover:text-mint ${
                  isActive ? "bg-mint text-on-accent" : ""
                }`
              }
            >
              {p.label}
            </NavLink>
          ))}
        </div>

        <button
          type="button"
          onClick={() => void logout()}
          className="lowercase font-pixel border-2 border-pure-black px-3 py-1 text-lavender hover:text-mint active:translate-x-[1px] active:translate-y-[1px]"
        >
          salir
        </button>
      </div>
    </nav>
  );
}
