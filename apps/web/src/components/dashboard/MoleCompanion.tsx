import { GridSprite } from "@/components/motion/GridSprite";
import { aLaEspera } from "@/components/Visual";

// Inicio's single decorative element: the pixel mole «a la espera» (4 columns × 2 rows,
// animated by `GridSprite`) with a short bubble. Decorative only; it has no actions.
const SHEET = { width: 1408, height: 768 };
const COLUMNS = 4;
const ROWS = 2;
const SIZE = 96;

export function MoleCompanion() {
  return (
    <aside aria-label="compañero maulwurf" className="flex flex-col items-center gap-3">
      <div className="border-2 border-pure-black bg-surface p-2">
        <GridSprite
          src={aLaEspera}
          sheetWidth={SHEET.width}
          sheetHeight={SHEET.height}
          columns={COLUMNS}
          rows={ROWS}
          height={SIZE}
          fps={5}
        />
      </div>
      <p className="lowercase text-center text-base text-lavender/80">
        &gt; sube una clase y yo guardo el texto.
      </p>
    </aside>
  );
}
