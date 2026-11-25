import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { buttonClasses } from "@/components/ui/buttonStyles";

const TILES = [
  { to: "/gestion/subir", glyph: "↑", id: "upload", primary: true },
  { to: "/gestion/biblioteca", glyph: "▸", id: "library", primary: false },
  { to: "/gestion/chat", glyph: ">", id: "chat", primary: false },
  { to: "/gestion/tareas", glyph: "!", id: "tasks", primary: false },
] as const;

// Four large entry points; exactly one is primary so the first step is obvious.
export function AccessTiles() {
  const { t } = useTranslation("plan");
  return (
    <nav aria-label={t("dashboard.tiles.nav")} className="grid grid-cols-1 gap-4 sm:grid-cols-2">
      {TILES.map((tile) => (
        <Link
          key={tile.to}
          to={tile.to}
          className={buttonClasses(
            tile.primary ? "primary" : "secondary",
            "lg",
            "min-h-28 flex-col items-start justify-center gap-1 text-left",
          )}
        >
          <span>
            <span aria-hidden="true">{tile.glyph} </span>
            {t(`dashboard.tiles.${tile.id}.label`)}
          </span>
          <span className="lowercase whitespace-normal text-base opacity-80">{t(`dashboard.tiles.${tile.id}.hint`)}</span>
        </Link>
      ))}
    </nav>
  );
}
