import { Link } from "react-router-dom";
import { Typewriter } from "@/components/motion/Typewriter";
import { FlickerText } from "@/components/motion/FlickerText";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { buttonClasses } from "@/components/ui/buttonStyles";

export function WelcomePage() {
  return (
    <div className="relative flex min-h-screen flex-col items-center justify-center px-4 py-10">
      <div className="crt-overlay" aria-hidden="true" />

      <div className="absolute right-4 top-4">
        <ThemeToggle />
      </div>

      <div className="lowercase text-center">
        <p className="text-xl text-lavender">&gt; acceso concedido</p>
        <h1 className="mt-4 text-4xl text-mint">
          <Typewriter text="uff te logueaste crack en grande" speed={50} />
        </h1>
        <p className="mt-6 text-lg text-lavender">
          <FlickerText mode="flicker">bienvenido a maulwurf</FlickerText>
        </p>
      </div>

      <div className="mt-10 flex items-center gap-4">
        <Link to="/" className={buttonClasses("primary", "lg")}>
          continuar ›
        </Link>
      </div>
    </div>
  );
}
