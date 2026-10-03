import { Link } from "react-router-dom";
import { AuthShell } from "@/components/auth/AuthShell";
import { GoogleButton } from "@/components/auth/GoogleButton";
import { Typewriter } from "@/components/motion/Typewriter";

export function LoginPage() {
  return (
    <AuthShell title="login">
      <div className="lowercase">
        <h1 className="text-2xl text-mint">
          <Typewriter text="bienvenido de vuelta" speed={45} />
        </h1>
        <p className="mt-2 text-lavender">&gt; autenticación requerida</p>
      </div>

      <GoogleButton label="entrar con google" />

      <div className="lowercase border-t-2 border-lavender/30 pt-4 text-lavender">
        <p className="mb-1">¿aún no tienes cuenta?</p>
        <Link to="/register" className="text-mint hover:text-lavender">
          › crear cuenta
        </Link>
      </div>
    </AuthShell>
  );
}
