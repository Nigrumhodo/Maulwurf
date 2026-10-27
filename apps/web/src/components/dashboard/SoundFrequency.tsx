import { Card } from "@/components/ui/Card";
import { BlinkingCaret } from "@/components/motion/BlinkingCaret";
import { gokured, topogoku } from "@/components/Visual";

// D- (Dashboard) — Señal de audio (maqueta). "Topo Goku" lanza la onda: imagen
// por tema (gokured en oscuro, topogoku en claro) + ecualizador animado. La
// onda arranca como una línea casi plana en el centro y, hacia la derecha,
// crece en amplitud (altura) y duración (más lenta). Las barras son finas y
// de ancho uniforme (simétricas) y llenan todo el ancho.
const COUNT = 60;
const BARS = Array.from({ length: COUNT }, (_, i) => {
  const t = i / (COUNT - 1);
  const amp = 10 + t * t * 92; // amplitud 10 → 102 px
  return {
    h: Math.max(3, Math.round(amp * Math.abs(Math.sin(i * 0.42)))), // onda simétrica
    delay: (i % 5) * 0.05,
    dur: 1.4 + t * 1.0, // 1.4 → 2.4 s: cada vez más lento
  };
});

export function SoundFrequency() {
  return (
    <Card title="02 · error de señal" variant="accent" className="glitch-hover hover:border-mint">
      <div className="flex items-center">
        {/* Topo Goku: gokured en oscuro, topogoku en claro. */}
        <img
          src={gokured}
          alt=""
          className="img-dark-only h-32 w-auto shrink-0"
          style={{ imageRendering: "pixelated" }}
        />
        <img
          src={topogoku}
          alt=""
          className="img-light-only h-32 w-auto shrink-0"
          style={{ imageRendering: "pixelated" }}
        />

        {/* Onda centrada, densa, que crece desde las patitas del topo. */}
        <div className="-ml-8 flex h-32 flex-1 translate-y-2 items-center gap-[3px]" aria-hidden="true">
          {BARS.map((bar, i) => (
            <span
              key={i}
              className="eq-bar flex-1 bg-mint"
              style={{
                height: `${bar.h}px`,
                animationDelay: `${bar.delay}s`,
                animationDuration: `${bar.dur}s`,
              }}
            />
          ))}
        </div>
      </div>

      <p className="mt-4 lowercase text-lavender/60">
        &gt; señal inestable · reintentando <BlinkingCaret />
      </p>
    </Card>
  );
}
