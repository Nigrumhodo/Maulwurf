import { BlinkingCaret } from "@/components/motion/BlinkingCaret";

// Dashboard — telemetry strip: a blinking system line plus quick counters
// (mock numbers, replaced by real metrics later). Keeps the Cyber Y2K tokens:
// `mint` accent, `surface` panels and hard `shadow-block` shadows.
interface TelemetryBannerProps {
  guest: boolean;
}

const METRICS = [
  { label: "audio ingerido", value: "24.5 hrs" },
  { label: "clases activas", value: "06" },
  { label: "apuntes .md", value: "18" },
];

export function TelemetryBanner({ guest }: TelemetryBannerProps) {
  return (
    <section className="border-2 border-pure-black bg-surface shadow-block">
      <div className="flex flex-col gap-3 px-4 py-3 lg:flex-row lg:items-center lg:justify-between">
        <div className="flex flex-wrap items-center gap-2 lowercase text-lavender">
          <span className="animate-blink text-mint" aria-hidden="true">
            &gt;
          </span>
          <span className="font-bold tracking-wider">maulwurf.os v0.1</span>
          <span className="text-lavender/40" aria-hidden="true">
            //
          </span>
          <span className="border-2 border-mint bg-mint px-1 text-on-accent">en línea</span>
          <span className="text-lavender/40" aria-hidden="true">
            //
          </span>
          <span>{guest ? "sesión: invitado" : "sesión activa"}</span>
          <BlinkingCaret block />
        </div>

        <div className="flex flex-wrap items-center gap-2 text-sm">
          {METRICS.map((m) => (
            <div
              key={m.label}
              className="lowercase flex items-center gap-2 border-2 border-pure-black bg-surface px-2 py-1"
            >
              <span className="text-lavender/60">{m.label}:</span>
              <span className="font-bold text-mint">{m.value}</span>
            </div>
          ))}
          <div className="lowercase flex items-center gap-2 border-2 border-pure-black bg-surface px-2 py-1">
            <span className="text-lavender/60">mem_store:</span>
            <span className="font-bold text-mint">84% ok</span>
            <div
              className="flex h-2 w-12 overflow-hidden border border-pure-black"
              role="progressbar"
              aria-valuenow={84}
              aria-valuemin={0}
              aria-valuemax={100}
            >
              <div className="bg-mint" style={{ width: "84%" }} />
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
