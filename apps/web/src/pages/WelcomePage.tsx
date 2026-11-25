import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { Typewriter } from "@/components/motion/Typewriter";
import { FlickerText } from "@/components/motion/FlickerText";
import { WavesBackground } from "@/components/motion/WavesBackground";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { UiLanguageSwitcher } from "@/components/ui/UiLanguageSwitcher";
import { buttonClasses } from "@/components/ui/buttonStyles";
import { useDocumentTitle } from "@/lib/useDocumentTitle";

export function WelcomePage() {
  const { t } = useTranslation("auth");
  useDocumentTitle(t("welcome.documentTitle"));
  return (
    <div className="relative z-10 flex min-h-screen flex-col items-center justify-center px-4 py-10">
      <WavesBackground />
      <div className="crt-overlay" aria-hidden="true" />

      <div className="absolute right-4 top-4 z-10 flex gap-2">
        <UiLanguageSwitcher labelClassName="hidden sm:inline" />
        <ThemeToggle />
      </div>

      <main className="lowercase relative z-10 flex w-full max-w-lg flex-col items-center gap-4 border-2 border-pure-black bg-surface p-8 text-center shadow-block">
        <p className="text-xl text-lavender">&gt; {t("welcome.granted")}</p>
        <h1 className="text-4xl text-mint">
          <Typewriter text={t("welcome.heading")} speed={50} />
        </h1>
        <p className="text-lg text-lavender">
          <FlickerText mode="flicker">{t("welcome.tagline")}</FlickerText>
        </p>
        <Link to="/" className={buttonClasses("primary", "lg", "mt-4")}>
          {t("welcome.continue")} ›
        </Link>
      </main>
    </div>
  );
}
