import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { buttonClasses } from "@/components/ui/buttonStyles";

// Dashboard ingest deck. The reservation form is `/gestion/subir`; this panel does not
// pick a file or name a subject.
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

export function TapeDeck() {
  const { t } = useTranslation("plan");
  return (
    <section className="border-2 border-pure-black bg-surface shadow-block">
      <header className="flex items-center justify-between border-b-2 border-pure-black bg-mint px-3 py-2 text-on-accent">
        <span className="lowercase flex items-center gap-2 font-bold">
          <span aria-hidden="true">■</span>
          {t("tapeDeck.title")}
        </span>
        <span className="lowercase border-2 border-on-accent px-1.5 py-0.5 text-xs">
          ram_tmpfs
        </span>
      </header>

      <div className="flex flex-col gap-4 p-4">
        <div className="retro-grid relative flex flex-col items-center justify-center border-2 border-pure-black bg-surface p-3">
          <div className="mb-2 flex w-full items-center justify-between text-xs">
            <span className="lowercase text-lavender/60">{t("tapeDeck.pipeline")}</span>
            <span className="lowercase flex items-center gap-1 text-mint">
              <span className="inline-block h-2 w-2 animate-pulse bg-mint" aria-hidden="true" />
              {t("tapeDeck.ready")}
            </span>
          </div>

          <div className="flex h-24 w-full items-center justify-around border-2 border-pure-black bg-surface px-4">
            <Reel center="bg-mint" />
            <div className="flex flex-col items-center text-center">
              <div className="border-2 border-pure-black bg-dark px-2 py-0.5 text-mint">
                0 B en disco
              </div>
              <span className="mt-1 lowercase text-xs text-lavender/60">{t("tapeDeck.encryptedOnly")}</span>
            </div>
            <Reel center="bg-lavender" />
          </div>

          <div className="lowercase mt-2 flex w-full flex-col gap-1 border-2 border-pure-black bg-surface p-2 text-xs text-lavender/80">
            <p>&gt; {t("tapeDeck.formats")}</p>
            <p>&gt; {t("tapeDeck.policy")}</p>
          </div>
        </div>

        <Link to="/gestion/subir" className={buttonClasses("secondary")}>
          {t("tapeDeck.openForm")}
        </Link>
      </div>
    </section>
  );
}
