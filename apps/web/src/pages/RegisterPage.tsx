import { Link } from "react-router-dom";
import { AuthShell } from "@/components/auth/AuthShell";
import { GoogleButton } from "@/components/auth/GoogleButton";
import { Typewriter } from "@/components/motion/Typewriter";

const steps = [
  { n: "01", label: "conecta tu cuenta de google" },
  { n: "02", label: "sube la grabación de tu clase" },
  { n: "03", label: "estudia con tareas y citas" },
];

export function RegisterPage() {
  return (
    <AuthShell title="registro">
      <div className="lowercase">
        <h1 className="text-2xl text-mint">
          <Typewriter text="crea tu cuenta" speed={45} />
        </h1>
        <p className="mt-2 text-lavender">&gt; una sola cuenta, todo tu estudio</p>
      </div>

      <ol className="lowercase flex flex-col gap-2 text-lavender">
        {steps.map((s) => (
          <li key={s.n} className="flex items-baseline gap-3">
            <span className="text-mint">{s.n}</span>
            {s.label}
          </li>
        ))}
      </ol>

      <GoogleButton label="continuar con google" />

      <div className="lowercase border-t-2 border-lavender/30 pt-4 text-lavender">
        <p className="mb-1">¿ya tienes cuenta?</p>
        <Link to="/login" className="text-mint hover:text-lavender">
          › iniciar sesión
        </Link>
      </div>
    </AuthShell>
  );
}
