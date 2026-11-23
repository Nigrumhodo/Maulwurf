import { Link } from "react-router-dom";
import { buttonClasses } from "@/components/ui/buttonStyles";

const TILES = [
  { to: "/gestion/subir", glyph: "↑", label: "subir clase", hint: "sube una grabación y consérvala como texto", primary: true },
  { to: "/gestion/biblioteca", glyph: "▸", label: "biblioteca", hint: "lee y busca tus transcripciones", primary: false },
  { to: "/gestion/chat", glyph: ">", label: "chat", hint: "pregunta con citas exactas", primary: false },
  { to: "/gestion/tareas", glyph: "!", label: "tareas", hint: "revisa lo que propuso la clase", primary: false },
] as const;

// Four large entry points; exactly one is primary so the first step is obvious.
export function AccessTiles() {
  return (
    <nav aria-label="accesos principales" className="grid grid-cols-1 gap-4 sm:grid-cols-2">
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
            {tile.label}
          </span>
          <span className="lowercase whitespace-normal text-base opacity-80">{tile.hint}</span>
        </Link>
      ))}
    </nav>
  );
}
