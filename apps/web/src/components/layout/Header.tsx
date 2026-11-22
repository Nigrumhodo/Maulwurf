import { NavLink } from "react-router-dom";
import { useAuth } from "@/components/auth/AuthProvider";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { folder, folderClaro, madriguera, madrigueraClaro } from "@/components/Visual";
import { Clock } from "./Clock";

function navClass({ isActive }: { isActive: boolean }): string {
  return `capitalize font-pixel flex min-h-10 items-center justify-center gap-2 whitespace-nowrap border-2 border-pure-black px-2 py-1 text-lavender hover:text-mint sm:px-3 ${
    isActive ? "bg-mint text-on-accent" : ""
  }`;
}

// D- (Dashboard) — Header global: brand + reloj a la izquierda, "inicio" y el
// botón "gestión" (hub académico con menú lateral) centrados y, a la derecha,
// ajustes, toggle de tema + "salir". El menú desplegable "académico" pasó a ser
// la página `/gestion` con su propia navegación colapsable.
export function Header({ onOpenSearch }: { onOpenSearch?: () => void } = {}) {
  const { logout } = useAuth();

  return (
    <header className="sticky top-0 z-50 border-b-2 border-pure-black bg-dark">
      <div className="mx-auto flex max-w-7xl flex-col gap-2 px-4 py-3 sm:grid sm:grid-cols-[1fr_auto_1fr] sm:items-center sm:gap-3">
        <div className="hidden items-center gap-4 sm:flex">
          <NavLink to="/" className="inline-flex min-h-10 items-center lowercase whitespace-nowrap text-xl text-mint">
            maulwurf<span className="text-lavender">@lab</span>
          </NavLink>
          <Clock />
        </div>

        <div className="flex gap-2">
          <NavLink to="/" end className={(s) => `${navClass(s)} flex-1 sm:flex-none`}>
            <span className="img-dark-only">
              <img src={madriguera} alt="" className="h-6 w-6 object-contain sm:h-9 sm:w-9" style={{ imageRendering: "pixelated" }} />
            </span>
            <span className="img-light-only">
              <img src={madrigueraClaro} alt="" className="h-6 w-6 object-contain sm:h-9 sm:w-9" style={{ imageRendering: "pixelated" }} />
            </span>
            inicio
          </NavLink>

          <NavLink to="/gestion" className={(s) => `${navClass(s)} flex-1 sm:flex-none`}>
            <span className="img-dark-only">
              <img src={folder} alt="" className="h-6 w-6 object-contain sm:h-9 sm:w-9" style={{ imageRendering: "pixelated" }} />
            </span>
            <span className="img-light-only">
              <img src={folderClaro} alt="" className="h-6 w-6 object-contain sm:h-9 sm:w-9" style={{ imageRendering: "pixelated" }} />
            </span>
            <span className="capitalize">gestión</span>
          </NavLink>
        </div>

        <div className="flex items-stretch gap-2 sm:items-center sm:justify-end">
          {onOpenSearch !== undefined && (
            <button
              type="button"
              onClick={onOpenSearch}
              aria-label="buscar con ctrl+k"
              className="lowercase font-pixel inline-flex min-h-10 flex-1 items-center justify-center gap-1.5 whitespace-nowrap border-2 border-pure-black px-2 py-1 text-lavender hover:border-mint hover:text-mint sm:hidden sm:flex-none md:inline-flex md:px-2.5"
            >
              <span>buscar</span>
              <kbd className="hidden border border-lavender/40 px-1 text-xs text-mint md:inline">ctrl+k</kbd>
            </button>
          )}
          <NavLink to="/settings" className={(s) => `${navClass(s)} flex-1 sm:flex-none`}>
            ajustes
          </NavLink>
          <ThemeToggle />
          <button
            type="button"
            onClick={() => void logout()}
            className="capitalize font-pixel min-h-10 flex-1 whitespace-nowrap border-2 border-pure-black px-2 py-1 text-lavender hover:text-mint sm:flex-none sm:px-3 active:translate-x-[1px] active:translate-y-[1px]"
          >
            salir
          </button>
        </div>
      </div>
    </header>
  );
}
