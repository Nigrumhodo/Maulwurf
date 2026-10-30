import { Link } from "react-router-dom";
import { AvatarSprite } from "@/components/dashboard/AvatarSprite";
import { avatar, avatarred } from "@/components/Visual";

// Dashboard — "compañero maulwurf.daemon" (pixel mole companion). Reuses the
// 18-frame avatar sprite (dark/light sheets) and a speech bubble. The CTA opens
// the chat, never writes to Calendar or sends mail.
const FRAMES = 18;
const SIZE = 88;

const AVATAR_SHEET = { width: 2400, height: 122 };
const AVATARRED_SHEET = { width: 4126, height: 211 };

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
          <div className="img-dark-only">
            <AvatarSprite
              src={avatarred}
              sheetWidth={AVATARRED_SHEET.width}
              sheetHeight={AVATARRED_SHEET.height}
              frames={FRAMES}
              height={SIZE}
              fps={1.5}
            />
          </div>
          <div className="img-light-only">
            <AvatarSprite
              src={avatar}
              sheetWidth={AVATAR_SHEET.width}
              sheetHeight={AVATAR_SHEET.height}
              frames={FRAMES}
              height={SIZE}
              fps={1.5}
            />
          </div>
          <span
            className="absolute right-1 top-1 h-2 w-2 border border-pure-black bg-mint"
            aria-hidden="true"
          />
        </div>

        <div className="lowercase relative w-full border-2 border-pure-black bg-surface p-3 text-lavender shadow-block">
          <p className="text-lavender/50">&gt; maulwurf dice:</p>
          <p className="mt-1">
            tienes <span className="font-bold text-mint underline">2 grabaciones</span> pendientes de
            procesar en inteligencia artificial. ¿lanzas la síntesis nocturna?
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
