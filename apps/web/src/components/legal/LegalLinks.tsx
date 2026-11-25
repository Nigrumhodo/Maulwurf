import { useTranslation } from "react-i18next";
import { Link } from "react-router-dom";

const linkClass =
  "lowercase inline-flex min-h-8 min-w-8 items-center text-base text-lavender/80 hover:text-mint";

export interface LegalLinksProps {
  className?: string;
}

/** Inline footer links: privacy · terms · AGPL-3.0 license. */
export function LegalLinks({ className = "" }: LegalLinksProps) {
  const { t } = useTranslation("legal");
  return (
    <nav
      aria-label={t("links.label")}
      className={`flex flex-wrap items-center gap-x-2 gap-y-1 ${className}`.trim()}
    >
      <Link to="/privacidad" className={linkClass}>
        {t("links.privacy")}
      </Link>
      <span aria-hidden="true" className="text-lavender/80">
        ·
      </span>
      <Link to="/terminos" className={linkClass}>
        {t("links.terms")}
      </Link>
      <span aria-hidden="true" className="text-lavender/80">
        ·
      </span>
      <Link to="/terminos#licencia" className={linkClass}>
        {t("links.license")}
      </Link>
    </nav>
  );
}
