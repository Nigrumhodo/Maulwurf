import { NavLink } from "react-router-dom";
import { useAuth } from "@/components/auth/AuthProvider";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { folder, madriguera } from "@/components/Visual";
import { Clock } from "./Clock";

function navClass({ isActive }: { isActive: boolean }): string {
  return `lowercase font-pixel flex items-center gap-2 border-2 border-pure-black px-3 py-1 text-lavender hover:text-mint ${
    isActive ? "bg-mint text-on-accent" : ""
  }`;
}

// D- (Dashboard) — Header global: brand + reloj a la izquierda, "inicio" y el
// botón "gestión" (hub académico con menú lateral) centrados y, a la derecha,
// ajustes, toggle de tema + "salir". El menú desplegable "académico" pasó a ser
// la página `/gestion` con su propia navegación colapsable.
export function Header() {
  const { logout } = useAuth();

  return (
    <header className="sticky top-0 z-50 border-b-2 border-pure-black bg-dark">
      <div className="mx-auto grid max-w-7xl grid-cols-[1fr_auto_1fr] items-center gap-3 px-4 py-3">
        <div className="flex items-center gap-4">
          <NavLink to="/" className="hidden lowercase text-xl text-mint sm:block">
            maulwurf<span className="text-lavender">@lab</span>
          </NavLink>
          <div className="hidden sm:block">
            <Clock />
          </div>
        </div>

        <div className="flex items-center gap-2">
          <NavLink to="/" end className={navClass}>
            <img src={madriguera} alt="" className="h-9 w-9" style={{ imageRendering: "pixelated" }} />
            inicio
          </NavLink>

          <NavLink to="/gestion" className={navClass}>
            <img src={folder} alt="" className="h-9 w-9" style={{ imageRendering: "pixelated" }} />
            <span className="capitalize">gestión</span>
          </NavLink>
        </div>

        <div className="flex items-center justify-end gap-2">
          <NavLink to="/settings" className={navClass}>
            ajustes
          </NavLink>
          <ThemeToggle />
          <button
            type="button"
            onClick={() => void logout()}
            className="lowercase font-pixel border-2 border-pure-black px-3 py-1 text-lavender hover:text-mint active:translate-x-[1px] active:translate-y-[1px]"
          >
            salir
          </button>
        </div>
      </div>
    </header>
  );
}
