import { Link } from "react-router-dom";
import { GridSprite } from "@/components/motion/GridSprite";
import { aLaEspera } from "@/components/Visual";

// Dashboard — "compañero maulwurf.daemon" (pixel mole companion). Usa la hoja
// «a la espera» (4 columnas × 2 filas) animada por `GridSprite` y una burbuja de
// diálogo. El CTA abre el chat; nunca escribe en Calendar ni envía correo.
const SHEET = { width: 1408, height: 768 };
const COLUMNS = 4;
const ROWS = 2;
const SIZE = 112;

export function MoleCompanion() {
  return (
    <section className="border-2 border-pure-black bg-surface shadow-block">
      <header className="flex items-center justify-between border-b-2 border-pure-black bg-mint px-3 py-2 text-on-accent">
        <span className="lowercase flex items-center gap-2 font-bold">
          <span aria-hidden="true">■</span>
          04 · compañero // maulwurf.daemon
        </span>
        <span className="lowercase border-2 border-on-accent px-1.5 py-0.5 text-xs">ai_v3</span>
      </header>

      <div className="flex flex-col items-center gap-3 p-3">
        <div className="relative border-2 border-pure-black bg-surface p-2 shadow-block">
          <GridSprite
            src={aLaEspera}
            sheetWidth={SHEET.width}
            sheetHeight={SHEET.height}
            columns={COLUMNS}
            rows={ROWS}
            height={SIZE}
            fps={5}
          />
          <span
            className="absolute right-1 top-1 h-2 w-2 border border-pure-black bg-mint"
            aria-hidden="true"
          />
        </div>

        <div className="lowercase relative w-full border-2 border-pure-black bg-surface p-3 text-lavender shadow-block">
          <p className="text-lavender/50">&gt; maulwurf dice:</p>
          <p className="mt-1">
            demo: aquí verás tus <span className="font-bold text-mint underline">grabaciones</span>{" "}
            pendientes de procesar y podrás lanzar la síntesis nocturna.
          </p>
        </div>

        <Link to="/gestion/chat" className="lowercase w-full">
          <span className="flex items-center justify-center gap-2 border-2 border-pure-black bg-mint p-2 text-on-accent shadow-block hover:bg-surface hover:text-mint">
            <span aria-hidden="true">&gt;</span>
            abrir chat
          </span>
        </Link>
      </div>
    </section>
  );
}
