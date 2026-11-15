import { Link } from "react-router-dom";
import { buttonClasses } from "@/components/ui/buttonStyles";

// Dashboard ingest deck. The reservation form is `/gestion/subir`; this panel does not
// pick a file or name a subject.
const VU = {
  left: 6,
  right: 4,
};

function Reel({ center }: { center: string }) {
  return (
    <div
      className="relative flex h-14 w-14 shrink-0 items-center justify-center rounded-full border-2 border-pure-black bg-surface shadow-block"
      aria-hidden="true"
    >
      <div className="animate-reel absolute inset-0 flex items-center justify-center">
        <div className={`h-3 w-3 ${center}`} />
      </div>
      <div className="absolute h-full w-[2px] bg-lavender/30" />
      <div className="absolute h-[2px] w-full bg-lavender/30" />
    </div>
  );
}

function VuRow({ label, active }: { label: string; active: number }) {
  return (
    <div className="flex flex-col gap-1">
      <div className="flex items-center justify-between text-xs">
        <span className="lowercase text-lavender/60">{label}</span>
        <span className="lowercase text-mint">-{12 + active * 2}db</span>
      </div>
      <div className="flex h-2 gap-0.5" aria-hidden="true">
        {Array.from({ length: 10 }, (_, i) => (
          <span key={i} className={`flex-1 ${i < active ? "bg-mint" : "bg-lavender/15"}`} />
        ))}
      </div>
    </div>
  );
}

export function TapeDeck() {
  return (
    <section className="border-2 border-pure-black bg-surface shadow-block">
      <header className="flex items-center justify-between border-b-2 border-pure-black bg-mint px-3 py-2 text-on-accent">
        <span className="lowercase flex items-center gap-2 font-bold">
          <span aria-hidden="true">■</span>
          01 · unidad de ingesta de audio
        </span>
        <span className="lowercase border-2 border-on-accent px-1.5 py-0.5 text-xs">
          rec_standby
        </span>
      </header>

      <div className="flex flex-col gap-4 p-4">
        <div className="retro-grid relative flex flex-col items-center justify-center border-2 border-pure-black bg-surface p-3">
          <div className="mb-2 flex w-full items-center justify-between text-xs">
            <span className="lowercase text-lavender/60">model: reel-98 pro</span>
            <span className="lowercase flex items-center gap-1 text-mint">
              <span className="inline-block h-2 w-2 animate-pulse bg-mint" aria-hidden="true" />
              ready
            </span>
          </div>

          <div className="flex h-24 w-full items-center justify-around border-2 border-pure-black bg-surface px-6">
            <Reel center="bg-mint" />
            <div className="flex flex-col items-center">
              <div className="border-2 border-pure-black bg-dark px-2 py-0.5 text-mint">08:42</div>
              <span className="mt-1 uppercase tracking-tighter text-lavender/50">stereo 60</span>
            </div>
            <Reel center="bg-lavender" />
          </div>

          <div className="mt-2 flex w-full flex-col gap-1 border-2 border-pure-black bg-surface p-2">
            <VuRow label="vu l" active={VU.left} />
            <VuRow label="vu r" active={VU.right} />
          </div>
        </div>

        <Link to="/gestion/subir" className={buttonClasses("secondary")}>
          abrir el formulario de subida
        </Link>
      </div>
    </section>
  );
}
