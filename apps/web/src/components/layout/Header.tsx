import { NavLink } from "react-router-dom";
import { useAuth } from "@/components/auth/AuthProvider";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { folder, madriguera } from "@/components/Visual";
import { Clock } from "./Clock";

// D- (Dashboard) — Header global: brand + reloj a la izquierda, dos botones con
// logo centrados y, a la derecha, toggle de tema + "salir". Sustituye al Navbar.
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
          <NavLink
            to="/"
            end
            className={({ isActive }) =>
              `lowercase font-pixel flex items-center gap-2 border-2 border-pure-black px-3 py-1 text-lavender hover:text-mint ${
                isActive ? "bg-mint text-on-accent" : ""
              }`
            }
          >
            <img src={madriguera} alt="" className="h-9 w-9" style={{ imageRendering: "pixelated" }} />
            inicio
          </NavLink>

          <button
            type="button"
            className="lowercase font-pixel flex items-center gap-2 border-2 border-pure-black px-3 py-1 text-lavender hover:text-mint"
          >
            <img src={folder} alt="" className="h-9 w-9" style={{ imageRendering: "pixelated" }} />
            transcripción<span className="hidden sm:inline"> y asignaturas</span>
          </button>
        </div>

        <div className="flex items-center justify-end gap-2">
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
