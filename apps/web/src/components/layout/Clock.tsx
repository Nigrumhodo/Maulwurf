import { useEffect, useState } from "react";

const DAYS = ["dom", "lun", "mar", "mié", "jue", "vie", "sáb"];
const MONTHS = [
  "ene",
  "feb",
  "mar",
  "abr",
  "may",
  "jun",
  "jul",
  "ago",
  "sep",
  "oct",
  "nov",
  "dic",
];

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** Fecha y hora retro, actualizada cada minuto (sin segundos). */
export function Clock() {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div className="lowercase font-pixel select-none text-lavender">
      {DAYS[now.getDay()]} {pad(now.getDate())} {MONTHS[now.getMonth()]}
      <span className="text-mint"> · </span>
      {pad(now.getHours())}:{pad(now.getMinutes())}
    </div>
  );
}
