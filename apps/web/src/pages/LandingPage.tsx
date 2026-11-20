import { Link } from "react-router-dom";
import { Card } from "@/components/ui/Card";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { buttonClasses } from "@/components/ui/buttonStyles";
import { loginup, loginuposcuro } from "@/components/Visual";
import { GOOGLE_AUTH_START_URL } from "@/lib/auth";

// D1.8 — Public landing page on `/` when unauthenticated. Makes zero private API calls.

const PIPELINE_STEPS = [
  {
    id: "01",
    title: "sube sin guardar audio",
    desc: "transmisión directa por stream a tmpfs en RAM; los temporales se destruyen y verifican antes de indexar.",
  },
  {
    id: "02",
    title: "encuentra con citas reales",
    desc: "búsqueda híbrida (texto + pgvector) y chat RAG que valida cada segmento contra PostgreSQL.",
  },
  {
    id: "03",
    title: "decide antes de escribir",
    desc: "las tareas y bloques de agenda siempre esperan tu confirmación explícita antes de tocar Google Calendar o Gmail.",
  },
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
      <div className="crt-overlay" aria-hidden="true" />

      <header className="mx-auto flex max-w-6xl items-center justify-between border-2 border-pure-black bg-dark px-4 py-3 shadow-block">
        <span className="lowercase text-2xl text-mint">
          maulwurf<span className="text-lavender">@lab</span>
        </span>
        <nav className="flex items-center gap-3">
          <Link
            to="/login"
            className="lowercase font-pixel border-2 border-pure-black px-3 py-1 text-lavender hover:border-mint hover:text-mint"
          >
            iniciar sesión
          </Link>
          <ThemeToggle />
        </nav>
      </header>

      <main className="mx-auto mt-8 flex max-w-6xl flex-col gap-8">
        <section className="grid items-center gap-6 border-2 border-pure-black bg-surface p-6 shadow-block lg:grid-cols-[1.3fr_0.7fr]">
          <div className="flex flex-col gap-4">
            <p className="lowercase text-sm tracking-wider text-mint">
              &gt; privacy-first study assistant // mvp + v1
            </p>
            <h1 className="text-4xl tracking-wide text-mint sm:text-5xl">
              SUBE → ENCUENTRA → DECIDE
            </h1>
            <p className="lowercase text-xl leading-snug text-lavender">
              convierte grabaciones de clase en transcripciones consultables, chat con citas exactas
              y propuestas revisables de Google Calendar sin guardar archivos de audio.
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <a href={GOOGLE_AUTH_START_URL} className={buttonClasses("primary", "lg")}>
                entrar con google ›
              </a>
              <Link to="/login" className={buttonClasses("secondary", "lg")}>
                acceso existente
              </Link>
            </div>
            <p className="lowercase text-sm text-lavender/60">
              solo texto cifrado persistido · sin reproductor · confirmación humana obligatoria
            </p>
          </div>

          <div className="flex items-center justify-center border-2 border-pure-black bg-dark p-4 shadow-block">
            <span className="img-dark-only">
              <img
                src={loginuposcuro}
                alt="maulwurf terminal"
                className="max-h-56 w-auto object-contain"
                style={{ imageRendering: "pixelated" }}
              />
            </span>
            <span className="img-light-only">
              <img
                src={loginup}
                alt="maulwurf terminal"
                className="max-h-56 w-auto object-contain"
                style={{ imageRendering: "pixelated" }}
              />
            </span>
          </div>
        </section>

        <section aria-label="flujo de trabajo" className="grid gap-4 md:grid-cols-3">
          {PIPELINE_STEPS.map((step) => (
            <Card key={step.id} title={`${step.id} · ${step.title}`}>
              <p className="lowercase text-lg text-lavender">{step.desc}</p>
            </Card>
          ))}
        </section>

        <section className="grid gap-4 lg:grid-cols-2">
          <Card title="contrato de privacidad (g1)" variant="accent">
            <ul className="lowercase flex flex-col gap-2 text-lg text-lavender">
              <li>&gt; audio efímero únicamente en RAM (tmpfs acotado y no ejecutable).</li>
              <li>&gt; verificación estricta de ausencia temporal antes de indexar texto.</li>
              <li>&gt; aislamiento multitenant mediante RLS forzado en PostgreSQL 16.</li>
              <li>&gt; caché local del navegador y service worker limitados al shell estático.</li>
            </ul>
          </Card>

          <Card title="preguntas frecuentes">
            <dl className="lowercase flex flex-col gap-3 text-lavender">
              {FAQ_ITEMS.map((item) => (
                <div key={item.q} className="border-b border-lavender/20 pb-2 last:border-b-0">
                  <dt className="text-mint">&gt; {item.q}</dt>
                  <dd className="text-lavender/80">{item.a}</dd>
                </div>
              ))}
            </dl>
          </Card>
        </section>
      </main>

      <footer className="lowercase mx-auto mt-8 flex max-w-6xl flex-wrap items-center justify-between gap-2 border-2 border-pure-black bg-surface px-4 py-2 text-lavender/60">
        <span>audio no conservado · cifrado aes-gcm · solo texto persistido</span>
        <span>maulwurf@lab © 2026</span>
      </footer>
    </div>
  );
}
