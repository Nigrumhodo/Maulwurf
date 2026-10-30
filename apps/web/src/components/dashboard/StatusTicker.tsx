// D- (Dashboard) — Cinta de estado (marquee). Puramente estética en S1: solo
// muestra cadenas estáticas derivadas del estado de sesión, sin datos privados.
export function StatusTicker({ guest }: { guest: boolean }) {
  const items = [
    "maulwurf.os v0.1",
    guest ? "modo invitado" : "sesión activa",
    "audio no conservado",
    "sube → encuentra → decide",
  ];
  const line = items.join("  ·  ");

  return (
    <div className="overflow-hidden border-2 border-pure-black bg-surface py-1">
      <div className="marquee whitespace-nowrap lowercase text-mint">
        <span className="inline-block pr-8">{line}</span>
        <span className="inline-block pr-8" aria-hidden="true">
          {line}
        </span>
      </div>
    </div>
  );
}
