import { BlinkingCaret } from "@/components/motion/BlinkingCaret";

// Dashboard — telemetry strip: a system line (one blinking caret, nothing readable blinks)
// plus quick counters
// (mock numbers labelled as demo, replaced by real metrics later). Keeps the Cyber
// Y2K tokens: `mint` accent, `surface` panels and hard `shadow-block` shadows.
const METRICS = [
  { label: "audio ingerido", value: "24.5 hrs" },
  { label: "clases activas", value: "06" },
  { label: "apuntes .md", value: "18" },
];

export function TelemetryBanner() {
  return (
    <section className="border-2 border-pure-black bg-surface shadow-block">
      <div className="flex flex-col gap-3 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex flex-wrap items-center gap-2 lowercase text-lavender">
          <span className="text-mint" aria-hidden="true">
            &gt;
          </span>
          <span className="font-bold tracking-wider">maulwurf.os v0.1</span>
          <span className="text-lavender/60" aria-hidden="true">
            //
          </span>
          <span className="border-2 border-mint bg-mint px-1 text-on-accent">en línea</span>
          <span className="lowercase border-2 border-lavender/60 px-1 text-lavender/80">
            demo
          </span>
          <span className="text-lavender/60" aria-hidden="true">
            //
          </span>
          <span>sesión activa</span>
          <BlinkingCaret block />
        </div>

        <div className="flex flex-wrap items-center gap-2 text-sm">
          {METRICS.map((m) => (
            <div
              key={m.label}
              className="lowercase flex items-center gap-2 border-2 border-lavender/30 px-2 py-1"
            >
              <span className="text-lavender/80">{m.label}:</span>
              <span className="font-bold text-mint">{m.value}</span>
            </div>
          ))}
          <div className="lowercase flex items-center gap-2 border-2 border-lavender/30 px-2 py-1">
            <span className="text-lavender/80">mem_store:</span>
            <span className="font-bold text-mint">84% ok</span>
            {/* Decorative only: no role="progressbar", which would announce a fake value. */}
            <div className="flex h-2 w-12 overflow-hidden border border-lavender/60" aria-hidden="true">
              <div className="bg-mint" style={{ width: "84%" }} />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
