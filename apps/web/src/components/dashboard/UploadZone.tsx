import { Link } from "react-router-dom";
import { Card } from "@/components/ui/Card";
import { buttonClasses } from "@/components/ui/buttonStyles";
import { AvatarSprite } from "@/components/dashboard/AvatarSprite";
import { avatar, avatarred } from "@/components/Visual";

// D- (Dashboard) — Hero de subida (M9/F1). El avatar es una animación de 18
// fotogramas en una sola fila: `avatarred` (oscuro) y `avatar` (claro). El
// enlace abre el formulario real (`/gestion/subir`); los bytes siguen en D2.2.
const FRAMES = 18;
const AVATAR_HEIGHT = 112;

// Dimensiones reales de las hojas (una fila × 18 columnas).
const AVATAR_SHEET = { width: 2400, height: 122 };
const AVATARRED_SHEET = { width: 4126, height: 211 };

export function UploadZone() {
  return (
    <Card
      title="01 · subir clase"
      variant="accent"
      footer="mp3 · m4a · wav · ogg · opus · flac · webm"
      className="glitch-hover hover:border-mint min-h-[260px]"
    >
      <div className="flex items-center gap-6">
        <div className="img-dark-only shrink-0">
          <AvatarSprite
            src={avatarred}
            sheetWidth={AVATARRED_SHEET.width}
            sheetHeight={AVATARRED_SHEET.height}
            frames={FRAMES}
            height={AVATAR_HEIGHT}
            fps={1.5}
          />
        </div>
        <div className="img-light-only shrink-0">
          <AvatarSprite
            src={avatar}
            sheetWidth={AVATAR_SHEET.width}
            sheetHeight={AVATAR_SHEET.height}
            frames={FRAMES}
            height={AVATAR_HEIGHT}
            fps={1.5}
          />
        </div>

        <Link
          to="/gestion/subir"
          className="flex flex-1 flex-col items-center justify-center gap-3 border-2 border-dashed border-lavender/50 px-6 py-8 text-center hover:border-mint"
        >
          <span className="text-4xl leading-none text-mint" aria-hidden="true">
            ▾
          </span>
          <p className="lowercase text-lavender">abre el formulario de subida</p>
          <p className="lowercase text-lavender/50">materia, fecha, zona e idioma</p>
          <span className={buttonClasses("secondary")}>continuar</span>
        </Link>
      </div>
    </Card>
  );
}
