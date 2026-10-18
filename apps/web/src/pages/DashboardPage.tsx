import { Button } from "@/components/ui/Button";
import { Card } from "@/components/ui/Card";
import { CardGrid } from "@/components/ui/CardGrid";
import { ChatButton } from "@/components/chat/ChatButton";
import { Typewriter } from "@/components/motion/Typewriter";
import { BlinkingCaret } from "@/components/motion/BlinkingCaret";

// D- (Dashboard) — Maqueta del panel de mando (M9). Cuatro ventanas + botón de chat.
// Nada tiene lógica todavía: la integración con el backend llega en D2.x/D3.x/D4.x.
export function DashboardPage() {
  return (
    <div className="relative flex flex-col gap-8">
      <div className="crt-overlay" aria-hidden="true" />

      <header className="lowercase">
        <h1 className="text-3xl text-mint">
          <Typewriter text="bienvenido de vuelta" speed={40} />
        </h1>
        <p className="mt-1 text-lavender">&gt; agenda, pendientes y estado de ingesta</p>
      </header>

      <CardGrid columns={2}>
        <Card
          title="subir archivo"
          footer="mp3 · m4a · wav · ogg · opus · flac · webm"
          className="min-h-[300px] glitch-hover hover:border-mint"
        >
          <div className="flex h-full flex-col items-center justify-center gap-3 border-2 border-dashed border-lavender/50 px-6 py-8 text-center">
            <span className="text-4xl leading-none text-mint" aria-hidden="true">
              ▾
            </span>
            <p className="lowercase text-lavender">arrastra tu clase aquí</p>
            <p className="lowercase text-lavender/50">o</p>
            {/* TODO(D2.1): abrir formulario de upload (materia, fecha, zona, idioma, consentimiento).
                El botón NO funciona todavía; el backend se encarga de POST /audios + PUT binario. */}
            <Button variant="secondary">seleccionar archivo</Button>
          </div>
        </Card>

        <Card
          title="transcripciones recientes"
          className="min-h-[300px] glitch-hover hover:border-mint"
        >
          {/* TODO(D2.4): integrar GET /audios → lista de clases con su estado. */}
          <ul className="flex flex-col gap-2 lowercase text-lavender">
            <li className="flex items-center gap-2 text-lavender/50">
              <span className="inline-block h-2 w-2 bg-lavender/40" />
              sin transcripciones todavía
            </li>
          </ul>
        </Card>

        <Card
          title="tareas pendientes"
          className="min-h-[300px] glitch-hover hover:border-mint"
        >
          {/* TODO(D3.1): integrar GET /tasks?status=pending → bandeja "para confirmar". */}
          <ul className="flex flex-col gap-2 lowercase text-lavender">
            <li className="flex items-center gap-2 text-lavender/50">
              <span className="inline-block h-2 w-2 bg-lavender/40" />
              sin tareas pendientes
            </li>
          </ul>
        </Card>

        <Card
          title="en construcción"
          className="min-h-[300px] glitch-hover hover:border-mint"
        >
          <p className="lowercase text-lavender/60">
            próximamente <BlinkingCaret />
          </p>
        </Card>
      </CardGrid>

      <ChatButton />
    </div>
  );
}
