import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";
import { WavesBackground } from "@/components/motion/WavesBackground";
import { RetroVisual } from "@/components/ui/RetroVisual";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { LegalConsentNotice } from "@/components/legal/LegalConsentNotice";
import { LegalLinks } from "@/components/legal/LegalLinks";
import { UiLanguageSwitcher } from "@/components/ui/UiLanguageSwitcher";
import { buttonClasses } from "@/components/ui/buttonStyles";
import {
  estadoPrivacidadRam,
  estadoPrivacidadRamAnim,
  heroMadriguera,
  heroMadrigueraAnim,
  heroPergaminoClaro,
} from "@/components/Visual";
import { GOOGLE_AUTH_START_URL } from "@/lib/auth";

// D1.8 — Public landing page on `/` when unauthenticated. Makes zero private API calls.

const PIPELINE_STEPS = [
  { id: "01", key: "upload" },
  { id: "02", key: "find" },
  { id: "03", key: "decide" },
] as const;

const PRIVACY_POINTS = ["ram", "absence", "tenancy", "cache"] as const;

const FAQ_ITEMS = ["recording", "writes", "deletion"] as const;

export function LandingPage() {
  const { t } = useTranslation("landing");
  return (
    <div className="relative z-10 min-h-screen px-4 py-6">
      <WavesBackground />
      <div className="crt-overlay" aria-hidden="true" />

      <header className="relative z-10 mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-2 border-2 border-pure-black bg-dark px-4 py-2 shadow-block">
        <span className="lowercase shrink-0 whitespace-nowrap text-2xl text-mint">
          maulwurf<span className="text-lavender">@lab</span>
        </span>
        <nav className="flex min-w-0 flex-wrap items-center gap-3">
          <UiLanguageSwitcher labelClassName="hidden sm:inline" />
          <ThemeToggle />
          <Link
            to="/login"
            className="lowercase font-pixel inline-flex min-h-8 items-center border-2 border-pure-black px-3 py-1 text-lg text-lavender hover:border-mint hover:text-mint"
          >
            {t("header.login")}
          </Link>
        </nav>
      </header>

      <main className="relative z-10 mx-auto mt-6 max-w-5xl border-2 border-pure-black bg-surface shadow-block">
        <section className="grid items-center gap-6 p-5 lg:grid-cols-[1fr_auto]">
          <div className="flex min-w-0 flex-col gap-4">
            <h1 className="text-4xl tracking-wide text-mint sm:text-5xl">
              {t("hero.title")}
            </h1>
            <p className="lowercase max-w-prose text-xl leading-snug text-lavender">
              {t("hero.lead")}
            </p>
            <p className="lowercase max-w-prose text-lg text-lavender/80">
              {t("hero.sub")}
            </p>
            <div className="mt-2 flex flex-wrap items-center gap-3">
              <a href={GOOGLE_AUTH_START_URL} className={buttonClasses("primary", "lg")}>
                {t("hero.ctaGoogle")}
              </a>
              <Link to="/register" className={buttonClasses("secondary", "lg")}>
                {t("hero.ctaRegister")}
              </Link>
              <Link to="/login" className={buttonClasses("ghost", "md")}>
                {t("hero.ctaLogin")}
              </Link>
            </div>
            <LegalConsentNotice className="lowercase max-w-prose" />
          </div>

          <div className="flex items-center justify-center border-2 border-pure-black bg-dark p-3 shadow-block-sm">
            <span className="img-dark-only">
              <RetroVisual
                src={heroMadrigueraAnim}
                staticSrc={heroMadriguera}
                alt={t("hero.altDark")}
                className="max-h-52 w-auto object-contain"
              />
            </span>
            <span className="img-light-only">
              <RetroVisual
                src={heroPergaminoClaro}
                alt={t("hero.altLight")}
                className="max-h-52 w-auto object-contain"
              />
            </span>
          </div>
        </section>

        <section aria-label={t("pipeline.label")} className="border-t-2 border-lavender/30 p-5">
          <h2 className="lowercase mb-4 text-xl text-mint">{t("pipeline.heading")}</h2>
          <ol className="grid gap-4 md:grid-cols-3 md:divide-x-2 md:divide-lavender/20">
            {PIPELINE_STEPS.map((step) => (
              <li key={step.id} className="flex flex-col gap-1 md:pl-4 md:first:pl-0">
                <p className="lowercase text-lg text-lavender">
                  <span className="text-mint">{step.id}</span> · {t(`pipeline.steps.${step.key}.title`)}
                </p>
                <p className="lowercase text-base text-lavender/80">{t(`pipeline.steps.${step.key}.desc`)}</p>
              </li>
            ))}
          </ol>
        </section>

        <section className="border-t-2 border-lavender/30 p-5">
          <div className="flex flex-col items-start gap-4 sm:flex-row sm:items-center">
            <div className="shrink-0 border-2 border-pure-black bg-dark p-1.5 shadow-block-sm">
              <RetroVisual
                src={estadoPrivacidadRamAnim}
                staticSrc={estadoPrivacidadRam}
                className="h-20 w-20 object-contain"
              />
            </div>
            <div className="min-w-0 flex-1">
              <h2 className="lowercase mb-3 text-xl text-mint">{t("privacy.heading")}</h2>
              <ul className="lowercase grid gap-x-6 gap-y-1 text-base text-lavender/80 md:grid-cols-2">
                {PRIVACY_POINTS.map((key) => (
                  <li key={key}>&gt; {t(`privacy.points.${key}`)}</li>
                ))}
              </ul>
            </div>
          </div>
        </section>

        <section className="border-t-2 border-lavender/30 p-5">
          <h2 className="lowercase mb-3 text-xl text-mint">{t("faq.heading")}</h2>
          <div className="flex flex-col divide-y-2 divide-lavender/20 border-y-2 border-lavender/20">
            {FAQ_ITEMS.map((key) => (
              <details key={key} className="lowercase group py-2">
                <summary className="min-h-8 cursor-pointer text-lg text-lavender hover:text-mint">
                  {t(`faq.items.${key}.q`)}
                </summary>
                <p className="mt-1 max-w-prose text-base text-lavender/80">{t(`faq.items.${key}.a`)}</p>
              </details>
            ))}
          </div>
        </section>
      </main>

      <footer className="lowercase relative z-10 mx-auto mt-6 flex max-w-5xl flex-wrap items-center justify-between gap-2 border-2 border-pure-black bg-dark px-4 py-2 text-base text-lavender/60">
        <span>{t("footer.privacy")}</span>
        <LegalLinks />
        <span>maulwurf@lab © 2026</span>
      </footer>
    </div>
  );
}
