import { Link, useSearchParams } from "react-router-dom";
import { AuthShell } from "@/components/auth/AuthShell";
import { GoogleButton } from "@/components/auth/GoogleButton";
import { Typewriter } from "@/components/motion/Typewriter";

// D1.3 — Mapeo de errores del callback OAuth a mensajes accionables. Las razones
// las define el backend en `routers/auth.py`; aquí solo se traducen para el usuario.
const ERROR_MESSAGES: Record<string, string> = {
  access_denied: "google rechazó el acceso.",
  invalid_state: "la sesión de login expiró; vuelve a intentarlo.",
  missing_params: "el regreso desde google vino incompleto; inténtalo de nuevo.",
  oauth_failed: "no se pudo completar el login con google.",
  oauth_not_configured: "el login con google no está configurado en el servidor.",
};

export function LoginPage() {
  const [searchParams] = useSearchParams();
  const error = searchParams.get("error");
  const message = error ? (ERROR_MESSAGES[error] ?? "el login con google falló.") : null;

  return (
    <AuthShell title="login">
      <div className="lowercase">
        <h1 className="text-2xl text-mint">
          <Typewriter text="bienvenido de vuelta" speed={45} />
        </h1>
        <p className="mt-2 text-lavender">&gt; autenticación requerida</p>
      </div>

      {message !== null && (
        <p role="alert" className="lowercase border-2 border-lavender bg-pure-black px-3 py-2 text-lavender">
          &gt; {message}
        </p>
      )}

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
