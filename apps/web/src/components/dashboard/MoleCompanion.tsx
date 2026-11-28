import { useTranslation } from "react-i18next";
import { GridSprite } from "@/components/motion/GridSprite";
import { RetroVisual } from "@/components/ui/RetroVisual";
import {
  estadoPrivacidadRam,
  estadoPrivacidadRamAnim,
  estadoPrivacidadRamClaro,
  heroEscritorioSnes,
  heroEscritorioSnesAnim,
  spriteEscuchandoNotas,
} from "@/components/Visual";

// Front C (16-bit RPG) Inicio companion: SNES CRT study desk in dark mode, animated 4-frame
// listening mole in light mode, plus the compact G1 RAM-only privacy reminder underneath.
const LISTENING_SHEET = { width: 636, height: 224, columns: 4, rows: 1 };

export function MoleCompanion() {
  const { t } = useTranslation("plan");
  return (
    <aside aria-label={t("mole.label")} className="flex flex-col gap-3">
      <div className="flex flex-col items-center gap-3 border-2 border-pure-black bg-dark p-3 shadow-block-sm">
        <span className="img-dark-only w-full">
          <RetroVisual
            src={heroEscritorioSnesAnim}
            staticSrc={heroEscritorioSnes}
            className="mx-auto h-44 w-44 object-contain"
          />
        </span>
        <span className="img-light-only">
          <div className="flex justify-center">
            <GridSprite
              src={spriteEscuchandoNotas}
              sheetWidth={LISTENING_SHEET.width}
              sheetHeight={LISTENING_SHEET.height}
              columns={LISTENING_SHEET.columns}
              rows={LISTENING_SHEET.rows}
              height={160}
              fps={4}
            />
          </div>
        </span>
        <p className="lowercase text-center text-base text-lavender/80">
          &gt; {t("mole.speech")}
        </p>
      </div>

      <div className="flex items-center gap-3 border-2 border-lavender/30 bg-surface p-2">
        <div className="shrink-0 border-2 border-pure-black bg-dark p-1">
          <RetroVisual
            src={estadoPrivacidadRamAnim}
            staticSrc={estadoPrivacidadRam}
            lightSrc={estadoPrivacidadRamClaro}
            className="h-11 w-11 object-contain"
          />
        </div>
        <p className="lowercase text-sm leading-tight text-lavender/75">
          {t("mole.privacy")}
        </p>
      </div>
    </aside>
  );
}
