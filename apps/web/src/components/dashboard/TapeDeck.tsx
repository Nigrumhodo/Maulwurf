import { Button } from "@/components/ui/Button";

// Dashboard — "unidad de ingesta" (audio ingest deck). Static mock that merges the
// hardware vibe (spinning tape reels, VU meters, switches) with our dropzone and a
// subject selector. No bytes are sent; the real flow is D2.1/D2.2.
const SUBJECTS = ["01. ia", "02. cálculo", "03. sistemas", "04. bbdd ii", "05. redes", "06. ética"];

const VU = {
  left: 6,
  right: 4,
};

const SWITCHES = [
  { label: "reducción de ruido cr-ii", on: true },
  { label: "auto-transcripción ai", on: true },
];

function Reel({ center }: { center: string }) {
  return (
    <div
      className="relative flex h-14 w-14 shrink-0 items-center justify-center rounded-full border-2 border-pure-black bg-surface shadow-block"
      aria-hidden="true"
    >
      <div className={`animate-reel absolute inset-0 flex items-center justify-center`}>
        <div className={`h-3 w-3 ${center}`} />
      </div>
      <div className="absolute h-full w-[2px] bg-lavender/30" />
      <div className="absolute w-full h-[2px] bg-lavender/30" />
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
      <div className="flex gap-0.5 h-2" aria-hidden="true">
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
        {/* Pixel tape-reel hardware frame */}
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
              <div className="border-2 border-pure-black bg-dark px-2 py-0.5 text-mint">
                08:42
              </div>
              <span className="mt-1 uppercase tracking-tighter text-lavender/50">stereo 60</span>
            </div>
            <Reel center="bg-lavender" />
          </div>

          <div className="mt-2 flex w-full flex-col gap-1 border-2 border-pure-black bg-surface p-2">
            <VuRow label="vu l" active={VU.left} />
            <VuRow label="vu r" active={VU.right} />
          </div>
        </div>

        {/* Dropzone */}
        <button
          type="button"
          className="lowercase flex cursor-pointer flex-col items-center justify-center gap-1 border-2 border-dashed border-lavender/50 px-4 py-5 text-center hover:border-mint"
        >
          <span className="text-2xl text-mint" aria-hidden="true">
            ▾
          </span>
          <span className="text-lavender">arrastra tu audio aquí</span>
          <span className="text-lavender/50">mp3 · m4a · wav · ogg · flac · webm</span>
        </button>
        <Button variant="secondary">seleccionar archivo audio</Button>

        {/* Target subject selector */}
        <div className="flex flex-col gap-2">
          <span className="lowercase text-xs text-lavender/60">
            asignar al tomo de destino: <span className="text-mint">01. ia</span>
          </span>
          <div className="grid grid-cols-3 gap-1.5">
            {SUBJECTS.map((subject, i) => (
              <button
                key={subject}
                type="button"
                className={`lowercase truncate border-2 border-pure-black p-1 text-center ${
                  i === 0
                    ? "bg-mint text-on-accent"
                    : "bg-surface text-lavender hover:text-mint"
                }`}
              >
                {subject}
              </button>
            ))}
          </div>
        </div>

        {/* Hardware switches */}
        <div className="flex flex-col gap-2 border-t-2 border-pure-black pt-3">
          {SWITCHES.map((s) => (
            <div key={s.label} className="lowercase flex items-center justify-between text-lavender">
              <span className="text-lavender/70">&gt; {s.label}:</span>
              <span className="border-2 border-pure-black bg-mint px-2 py-0.5 text-on-accent">
                [ on ]
              </span>
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
