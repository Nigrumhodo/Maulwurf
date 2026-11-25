import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";

function pad(n: number): string {
  return String(n).padStart(2, "0");
}

/** Fecha y hora retro, actualizada cada minuto (sin segundos). */
export function Clock({ className = "" }: { className?: string } = {}) {
  // Fixed abbreviations from the locale files instead of Intl: they keep the retro 3–4 letter
  // width and the existing Spanish spelling ("sep", not Intl's "sept").
  const { t } = useTranslation("common");
  const days = t("clock.days", { returnObjects: true });
  const months = t("clock.months", { returnObjects: true });
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = window.setInterval(() => setNow(new Date()), 60_000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div className={`lowercase whitespace-nowrap font-pixel select-none text-lavender/80 ${className}`}>
      {days[now.getDay()]} {pad(now.getDate())} {months[now.getMonth()]}
      <span className="text-mint"> · </span>
      {pad(now.getHours())}:{pad(now.getMinutes())}
    </div>
  );
}
