import { Link } from "react-router-dom";
import { WavesBackground } from "@/components/motion/WavesBackground";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { buttonClasses } from "@/components/ui/buttonStyles";
import { loginup, loginuposcuro } from "@/components/Visual";
import { GOOGLE_AUTH_START_URL } from "@/lib/auth";

// D1.8 — Public landing page on `/` when unauthenticated. Makes zero private API calls.

const PIPELINE_STEPS = [
  { id: "01", title: "sube sin guardar audio", desc: "el audio vive solo en RAM mientras se transcribe." },
  { id: "02", title: "encuentra con citas reales", desc: "cada respuesta cita un segmento verificado." },
  { id: "03", title: "decide antes de escribir", desc: "nada llega a calendar o gmail sin tu confirmación." },
] as const;

const PRIVACY_POINTS = [
  "audio efímero únicamente en RAM (tmpfs acotado y no ejecutable).",
  "verificación estricta de ausencia temporal antes de indexar texto.",
  "aislamiento multitenant mediante RLS forzado en PostgreSQL 16.",
  "caché local del navegador y service worker limitados al shell estático.",
] as const;

const FAQ_ITEMS = [
  {
    q: "¿se conserva mi grabación o hay reproductor?",
    a: "no. El audio solo vive en memoria temporal mientras se transcribe y nunca pasa a disco, S3 ni al navegador.",
  },
  {
    q: "¿maulwurf crea eventos o envía correos solo?",
    a: "nunca. Cada tarea entra para revisión y solo escribe en Google Calendar cuando la confirmas; Gmail requiere opt-in y permiso explícito.",
  },
  {
    q: "¿puedo borrar todos mis datos?",
    a: "sí. Desde Ajustes puedes lanzar el borrado observable de tu cuenta, transcripciones, embeddings y tareas.",
  },
] as const;

export function LandingPage() {
  return (
    <div className="relative z-10 min-h-screen px-4 py-6">
      <WavesBackground />
      <div className="crt-overlay" aria-hidden="true" />

      <header className="relative z-10 mx-auto flex max-w-5xl items-center justify-between border-2 border-pure-black bg-dark px-4 py-2 shadow-block">
        <span className="lowercase text-2xl text-mint">
          maulwurf<span className="text-lavender">@lab</span>
        </span>
        <nav className="flex items-center gap-3">
          <ThemeToggle />
          <Link
            to="/login"
            className="lowercase font-pixel inline-flex min-h-8 items-center border-2 border-pure-black px-3 py-1 text-lg text-lavender hover:border-mint hover:text-mint"
          >
            iniciar sesión
          </Link>
        </nav>
      </header>

      <main className="relative z-10 mx-auto mt-6 max-w-5xl border-2 border-pure-black bg-surface shadow-block">
        <section className="grid items-center gap-6 p-5 lg:grid-cols-[1fr_auto]">
          <div className="flex flex-col gap-4">
            <h1 className="text-4xl tracking-wide text-mint sm:text-5xl">
              SUBE → ENCUENTRA → DECIDE
            </h1>
            <p className="lowercase max-w-prose text-xl leading-snug text-lavender">
              maulwurf convierte tus grabaciones de clase en texto consultable con citas exactas.
            </p>
            <p className="lowercase max-w-prose text-lg text-lavender/80">
              para no perder lo que dijo el profesor ni las entregas que mencionó, sin guardar el
              audio.
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <a href={GOOGLE_AUTH_START_URL} className={buttonClasses("primary", "lg")}>
                entrar con google ›
              </a>
              <Link to="/register" className={buttonClasses("secondary", "lg")}>
                crear cuenta
              </Link>
              <Link to="/login" className={buttonClasses("ghost", "md")}>
                acceso existente
              </Link>
            </div>
          </div>

          <div className="flex items-center justify-center border-2 border-pure-black bg-dark p-4">
            <span className="img-dark-only">
              <img
                src={loginuposcuro}
                alt="maulwurf terminal"
                className="max-h-32 w-auto object-contain"
                style={{ imageRendering: "pixelated" }}
              />
            </span>
            <span className="img-light-only">
              <img
                src={loginup}
                alt="maulwurf terminal"
                className="max-h-32 w-auto object-contain"
                style={{ imageRendering: "pixelated" }}
              />
            </span>
          </div>
        </section>

        <section aria-label="flujo de trabajo" className="border-t-2 border-lavender/30 p-5">
          <h2 className="lowercase mb-4 text-xl text-mint">cómo funciona</h2>
          <ol className="grid gap-4 md:grid-cols-3 md:divide-x-2 md:divide-lavender/20">
            {PIPELINE_STEPS.map((step) => (
              <li key={step.id} className="flex flex-col gap-1 md:pl-4 md:first:pl-0">
                <p className="lowercase text-lg text-lavender">
                  <span className="text-mint">{step.id}</span> · {step.title}
                </p>
                <p className="lowercase text-base text-lavender/80">{step.desc}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="border-t-2 border-lavender/30 p-5">
          <h2 className="lowercase mb-3 text-xl text-mint">contrato de privacidad (g1)</h2>
          <ul className="lowercase grid gap-x-6 gap-y-1 text-base text-lavender/80 md:grid-cols-2">
            {PRIVACY_POINTS.map((p) => (
              <li key={p}>&gt; {p}</li>
            ))}
          </ul>
        </section>

        <section className="border-t-2 border-lavender/30 p-5">
          <h2 className="lowercase mb-3 text-xl text-mint">preguntas frecuentes</h2>
          <div className="flex flex-col divide-y-2 divide-lavender/20 border-y-2 border-lavender/20">
            {FAQ_ITEMS.map((item) => (
              <details key={item.q} className="lowercase group py-2">
                <summary className="min-h-8 cursor-pointer text-lg text-lavender hover:text-mint">
                  {item.q}
                </summary>
                <p className="mt-1 max-w-prose text-base text-lavender/80">{item.a}</p>
              </details>
            ))}
          </div>
        </section>
      </main>

      <footer className="lowercase relative z-10 mx-auto mt-6 flex max-w-5xl flex-wrap items-center justify-between gap-2 border-2 border-pure-black bg-dark px-4 py-2 text-base text-lavender/60">
        <span>audio no conservado · solo texto cifrado persistido</span>
        <span>maulwurf@lab © 2026</span>
      </footer>
    </div>
  );
}
