import { useEffect } from "react";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link, useLocation } from "react-router-dom";
import { WavesBackground } from "@/components/motion/WavesBackground";
import { ThemeToggle } from "@/components/ui/ThemeToggle";
import { useLegalValues } from "@/legal/useLegalValues";

export interface LegalLayoutSection {
  id: string;
  title: string;
}

export interface LegalLayoutProps {
  title: string;
  sections: LegalLayoutSection[];
  /** Mount point next to the theme toggle for the UI language switcher. */
  languageSwitcher?: ReactNode;
  children: ReactNode;
}

// Public shell shared by the privacy policy and the terms (no session required).
export function LegalLayout({ title, sections, languageSwitcher, children }: LegalLayoutProps) {
  const { t } = useTranslation("legal");
  const values = useLegalValues();
  const { hash } = useLocation();

  // Router links do not scroll to `#fragment` targets on their own.
  useEffect(() => {
    if (!hash) return;
    document.getElementById(decodeURIComponent(hash.slice(1)))?.scrollIntoView?.();
  }, [hash]);

  return (
    <div className="relative z-10 min-h-screen px-4 py-6">
      <WavesBackground />
      <div className="crt-overlay" aria-hidden="true" />

      <header className="relative z-10 mx-auto flex max-w-5xl flex-wrap items-center justify-between gap-2 border-2 border-pure-black bg-dark px-4 py-2 shadow-block">
        <Link to="/" className="lowercase shrink-0 whitespace-nowrap text-2xl text-mint">
          maulwurf<span className="text-lavender">@lab</span>
        </Link>
        <div className="flex shrink-0 flex-wrap items-center gap-3">
          {languageSwitcher}
          <ThemeToggle />
        </div>
      </header>

      <main className="relative z-10 mx-auto mt-6 max-w-5xl border-2 border-pure-black bg-surface p-5 shadow-block">
        <Link
          to="/"
          className="lowercase inline-flex min-h-8 items-center text-lg text-lavender/80 hover:text-mint"
        >
          ‹ {t("common.back")}
        </Link>

        <h1 className="mt-2 text-3xl text-mint sm:text-4xl">{title}</h1>

        <p
          role="note"
          className="lowercase mt-4 max-w-prose border-2 border-mint px-3 py-2 text-lg text-lavender"
        >
          {t("common.draftBanner")} · {t("common.updated", { date: values.lastUpdated })}
        </p>

        <nav aria-label={t("common.index")} className="mt-6 max-w-prose">
          <h2 className="lowercase text-xl text-mint">{t("common.index")}</h2>
          <ol className="mt-2 flex flex-col gap-1">
            {sections.map((section, i) => (
              <li key={section.id}>
                <a
                  href={`#${section.id}`}
                  className="inline-flex min-h-8 items-center text-lg text-lavender/80 hover:text-mint"
                >
                  {i + 1}. {section.title}
                </a>
              </li>
            ))}
          </ol>
        </nav>

        <div className="mt-8 flex flex-col gap-8">{children}</div>
      </main>
    </div>
  );
}
