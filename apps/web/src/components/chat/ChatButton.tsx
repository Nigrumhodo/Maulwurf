import { useState } from "react";
import { SpriteAnimator } from "@/components/motion/SpriteAnimator";
import { chat1, chat2 } from "@/components/Visual";
import { ChatPanel } from "./ChatPanel";

// Sprites del chat: cada imagen es una fila de 6 fotogramas.
// chat1 = reposo (bucle); chat2 = apertura (una vez, se queda en el último).
const SHEET_WIDTH = 1376;
const IDLE_HEIGHT = 343;
const OPEN_HEIGHT = 241;
const FRAMES = 6;
const SCALE = 0.2;

export function ChatButton() {
  const [open, setOpen] = useState(false);

  return (
    <div className="fixed bottom-6 right-6 z-50 flex flex-col items-end gap-3">
      {open && <ChatPanel onClose={() => setOpen(false)} />}

      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        aria-label={open ? "cerrar chat" : "abrir chat"}
        className="block cursor-pointer"
      >
        {open ? (
          <SpriteAnimator
            key="open"
            src={chat2}
            frameWidth={SHEET_WIDTH / FRAMES}
            frameHeight={OPEN_HEIGHT}
            frames={FRAMES}
            loop={false}
            scale={SCALE}
          />
        ) : (
          <SpriteAnimator
            key="idle"
            src={chat1}
            frameWidth={SHEET_WIDTH / FRAMES}
            frameHeight={IDLE_HEIGHT}
            frames={FRAMES}
            scale={SCALE}
          />
        )}
      </button>
    </div>
  );
}
