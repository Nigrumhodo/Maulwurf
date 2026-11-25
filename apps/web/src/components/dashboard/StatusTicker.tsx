import { useTranslation } from "react-i18next";

// Status marquee. Session text does not include a guest mode.
export function StatusTicker() {
  const { t } = useTranslation("plan");
  const items = [
    "maulwurf.os v0.1",
    t("ticker.session"),
    t("ticker.noAudio"),
    t("ticker.flow"),
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
